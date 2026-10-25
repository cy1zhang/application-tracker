import { afterEach, expect, test, vi } from "vitest";

import { makeApplication } from "../test/fixtures";
import {
  ApiError,
  applyPlan,
  createApplication,
  deleteAccount,
  disconnectGmail,
  getAccountStatus,
  getGmailConnection,
  getLatestSync,
  getLocalAiInput,
  getPlanReport,
  getSettings,
  listAllApplications,
  setAutoApplyMode,
  startGmailConnection,
  startSync,
  submitLocalAiSuggestion,
  updateApplication,
} from "./client";

function fakeResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("follows cursors through short and empty pages", async () => {
  const first = makeApplication({ application_id: "app-1" });
  const second = makeApplication({ application_id: "app-2" });
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(fakeResponse({ items: [first], next_cursor: "c1" }))
    .mockResolvedValueOnce(fakeResponse({ items: [], next_cursor: "c2" }))
    .mockResolvedValueOnce(fakeResponse({ items: [second], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(listAllApplications()).resolves.toEqual([first, second]);
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/api/applications?limit=100",
    "/api/applications?limit=100&cursor=c1",
    "/api/applications?limit=100&cursor=c2",
  ]);
});

test("creates an application with a JSON body", async () => {
  const created = makeApplication();
  const fetchMock = vi.fn().mockResolvedValue(fakeResponse(created, 201));
  vi.stubGlobal("fetch", fetchMock);
  const payload = {
    company: "Acme",
    role: "Software Engineer Intern",
    role_type: "internship" as const,
    season: null,
    year: null,
    status: "applied" as const,
    application_date: null,
  };

  await expect(createApplication(payload)).resolves.toEqual(created);
  expect(fetchMock).toHaveBeenCalledWith("/api/applications", {
    method: "POST",
    body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json" },
  });
});

test("local AI API sends model output only to the selected review", async () => {
  const prepared = { system_prompt: "Synthetic", user_prompt: "Email", format: {}, generation: 2 };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(fakeResponse(prepared))
    .mockResolvedValueOnce(fakeResponse({ saved: false, review: null }));
  vi.stubGlobal("fetch", fetchMock);
  const signal = new AbortController().signal;

  await expect(getLocalAiInput("msg/1", signal)).resolves.toEqual(prepared);
  await expect(submitLocalAiSuggestion("msg/1", {
    generation: 2, model: "gemma4:latest", extraction: { status: null },
  }, signal)).resolves.toEqual({ saved: false, review: null });
  expect(fetchMock.mock.calls[0][0]).toBe("/api/reviews/msg%2F1/local-ai-input");
  expect(fetchMock.mock.calls[0][1].signal).toBe(signal);
  expect(fetchMock.mock.calls[1][0]).toBe("/api/reviews/msg%2F1/local-ai-suggestion");
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
    generation: 2, model: "gemma4:latest", extraction: { status: null },
  });
});

test("reports the API detail for a stale update", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        fakeResponse({ detail: "Application version is stale" }, 409),
      ),
  );

  const error = await updateApplication("app-1", {
    expected_version: 1,
    status: "offer",
  }).catch((caught: unknown) => caught);

  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({
    status: 409,
    message: "Application version is stale",
  });
});

test("falls back to a generic message for structured validation errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(fakeResponse({ detail: [{ msg: "bad year" }] }, 422)),
  );

  await expect(listAllApplications()).rejects.toMatchObject({
    status: 422,
    message: "Request failed with status 422",
  });
});

test("explains when the server cannot be reached", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
  );

  await expect(listAllApplications()).rejects.toMatchObject({
    status: 0,
    message: "Could not reach the server. Is the backend running?",
  });
});

test("Gmail API returns status and authorization URL without credentials", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(fakeResponse({ connected: false, email: null }))
    .mockResolvedValueOnce(fakeResponse({ authorization_url: "https://google.test/consent" }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(getGmailConnection()).resolves.toEqual({ connected: false, email: null });
  await expect(startGmailConnection()).resolves.toEqual({
    authorization_url: "https://google.test/consent",
  });
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/api/gmail/connection",
    "/api/gmail/connect",
  ]);
});

test("Gmail disconnect accepts an empty 204 response", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 204 }));

  await expect(disconnectGmail()).resolves.toBeUndefined();
});

test("sync API starts work and reads the latest status", async () => {
  const run = { run_id: "run-1", status: "pending", processed_count: 0 };
  const fetchMock = vi.fn().mockResolvedValue(fakeResponse(run));
  vi.stubGlobal("fetch", fetchMock);

  await expect(startSync()).resolves.toMatchObject(run);
  await expect(getLatestSync()).resolves.toMatchObject(run);
  expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
    ["/api/syncs", "POST"], ["/api/syncs/latest", undefined],
  ]);
});

test("plan API reads the report and sends the approved mode and apply requests", async () => {
  const plan = { analyzed: 2, waiting: 0, report: { counts: { create: 1 }, reasons: {}, sample: [] } };
  const ai = { available_providers: [], enabled: false };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(fakeResponse({ ai, auto_apply_mode: "off" }))
    .mockResolvedValueOnce(fakeResponse({ ai, auto_apply_mode: "plan" }))
    .mockResolvedValueOnce(fakeResponse(plan))
    .mockResolvedValueOnce(fakeResponse({ queued: true }, 202));
  vi.stubGlobal("fetch", fetchMock);

  await expect(getSettings()).resolves.toMatchObject({ auto_apply_mode: "off" });
  await expect(setAutoApplyMode("plan")).resolves.toMatchObject({ auto_apply_mode: "plan" });
  await expect(getPlanReport()).resolves.toEqual(plan);
  await expect(applyPlan()).resolves.toEqual({ queued: true });
  expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
    ["/api/settings", undefined],
    ["/api/settings/auto-apply", "PUT"],
    ["/api/ai-plan/report", undefined],
    ["/api/ai-plan/apply", "POST"],
  ]);
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ mode: "plan" });
});

test("account API sends the DELETE confirmation body and reads status", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(fakeResponse({ state: "deleting" }, 202))
    .mockResolvedValueOnce(fakeResponse({ state: "active" }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(deleteAccount()).resolves.toEqual({ state: "deleting" });
  await expect(getAccountStatus()).resolves.toEqual({ state: "active" });
  expect(fetchMock.mock.calls[0][0]).toBe("/api/account");
  expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ confirm: "DELETE" });
  expect(fetchMock.mock.calls[1][0]).toBe("/api/account");
});

test("423 and 410 responses announce the account state", async () => {
  const deleting = vi.fn();
  const deleted = vi.fn();
  window.addEventListener("account-deleting", deleting);
  window.addEventListener("account-deleted", deleted);
  try {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse({ detail: "busy" }, 423)));
    await expect(listAllApplications()).rejects.toMatchObject({ status: 423 });
    expect(deleting).toHaveBeenCalledOnce();
    expect(deleted).not.toHaveBeenCalled();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(fakeResponse({ detail: "gone" }, 410)));
    await expect(listAllApplications()).rejects.toMatchObject({ status: 410 });
    expect(deleted).toHaveBeenCalledOnce();
  } finally {
    window.removeEventListener("account-deleting", deleting);
    window.removeEventListener("account-deleted", deleted);
  }
});
