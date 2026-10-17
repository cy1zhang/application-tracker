import { afterEach, expect, test, vi } from "vitest";

import { makeApplication } from "../test/fixtures";
import { createApplication, listAllApplications } from "./client";

vi.mock("../auth/config", () => ({
  authConfig: { apiBaseUrl: "https://api.example.test" },
  authConfigError: null,
}));
vi.mock("../auth/session", () => ({
  getAccessToken: vi.fn().mockResolvedValue("access-token"),
}));

afterEach(() => vi.unstubAllGlobals());

test("hosted GET attaches the access token", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ items: [], next_cursor: null }),
  });
  vi.stubGlobal("fetch", fetchMock);

  await listAllApplications();

  expect(fetchMock).toHaveBeenCalledWith(
    "https://api.example.test/applications?limit=100",
    { headers: { Authorization: "Bearer access-token" } },
  );
});

test("hosted POST attaches the access token and content type", async () => {
  const payload = {
    company: "Acme",
    role: "Engineer",
    role_type: "internship" as const,
    season: null,
    year: null,
    status: "applied" as const,
    application_date: null,
  };
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 201,
    json: async () => makeApplication(),
  });
  vi.stubGlobal("fetch", fetchMock);

  await createApplication(payload);

  expect(fetchMock).toHaveBeenCalledWith("https://api.example.test/applications", {
    method: "POST",
    body: JSON.stringify(payload),
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer access-token",
    },
  });
});

test("a hosted 401 asks the UI to sign in", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: false,
    status: 401,
    json: async () => ({ message: "Unauthorized" }),
  }));
  const listener = vi.fn();
  window.addEventListener("auth-required", listener, { once: true });

  await expect(listAllApplications()).rejects.toMatchObject({ status: 401 });
  expect(listener).toHaveBeenCalledOnce();
});
