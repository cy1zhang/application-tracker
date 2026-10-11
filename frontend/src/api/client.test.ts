import { afterEach, expect, test, vi } from "vitest";

import { makeApplication } from "../test/fixtures";
import {
  ApiError,
  createApplication,
  listAllApplications,
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
