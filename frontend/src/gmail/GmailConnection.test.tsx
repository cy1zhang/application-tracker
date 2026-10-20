import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  ApiError,
  disconnectGmail,
  getGmailConnection,
  getLatestSync,
  startSync,
  startGmailConnection,
} from "../api/client";
import { GmailConnection } from "./GmailConnection";
import { navigateToGoogle } from "./navigation";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getGmailConnection: vi.fn(),
    startGmailConnection: vi.fn(),
    disconnectGmail: vi.fn(),
    getLatestSync: vi.fn(),
    startSync: vi.fn(),
  };
});
vi.mock("./navigation", () => ({ navigateToGoogle: vi.fn() }));

beforeEach(() => {
  vi.mocked(getLatestSync).mockRejectedValue(new ApiError(404, "not found"));
});

afterEach(() => {
  vi.resetAllMocks();
  window.history.replaceState({}, "", "/");
  vi.unstubAllGlobals();
});

test("shows disconnected status and starts Google consent", async () => {
  vi.mocked(getGmailConnection).mockResolvedValue({ connected: false, email: null });
  vi.mocked(startGmailConnection).mockResolvedValue({
    authorization_url: "https://accounts.google.test/consent?state=opaque",
  });
  render(<GmailConnection />);

  expect(await screen.findByText("Gmail is not connected.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Connect Gmail" }));

  await waitFor(() => {
    expect(navigateToGoogle).toHaveBeenCalledWith(
      "https://accounts.google.test/consent?state=opaque",
    );
  });
});

test("canceling disconnect leaves connection intact", async () => {
  vi.mocked(getGmailConnection).mockResolvedValue({
    connected: true,
    email: "alex@example.test",
  });
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(false));
  render(<GmailConnection />);

  fireEvent.click(await screen.findByRole("button", { name: "Disconnect Gmail" }));

  expect(disconnectGmail).not.toHaveBeenCalled();
  expect(screen.getByText("alex@example.test")).toBeInTheDocument();
});

test("disconnect clears displayed connection", async () => {
  vi.mocked(getGmailConnection)
    .mockResolvedValueOnce({ connected: true, email: "alex@example.test" })
    .mockResolvedValueOnce({ connected: false, email: null });
  vi.mocked(disconnectGmail).mockResolvedValue();
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  render(<GmailConnection />);

  fireEvent.click(await screen.findByRole("button", { name: "Disconnect Gmail" }));

  expect(await screen.findByText("Gmail is not connected.")).toBeInTheDocument();
  expect(disconnectGmail).toHaveBeenCalledOnce();
});

test("failed consent message appears and result query is cleared", async () => {
  window.history.replaceState({}, "", "/?gmail=failed&view=list");
  vi.mocked(getGmailConnection).mockResolvedValue({ connected: false, email: null });
  render(<GmailConnection />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Gmail connection failed. Try again.",
  );
  expect(window.location.search).toBe("?view=list");
});

test("API errors leave the manual tracker available", async () => {
  vi.mocked(getGmailConnection).mockRejectedValue(new Error("network"));
  render(<GmailConnection />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not load Gmail connection status.",
  );
  expect(screen.getByRole("button", { name: "Retry Gmail status" })).toBeInTheDocument();
});

test("starts a sync and shows queued progress without claiming completion", async () => {
  vi.mocked(getGmailConnection).mockResolvedValue({ connected: true, email: "a@example.test" });
  vi.mocked(startSync).mockResolvedValue({
    run_id: "run-1", mode: "import", status: "pending", processed_count: 0,
    started_at: "2026-10-20T12:00:00Z", import_after: "2026-07-20T12:00:00Z",
    last_success_at: null,
  });
  render(<GmailConnection />);

  fireEvent.click(await screen.findByRole("button", { name: "Sync now" }));

  expect(await screen.findByText("Sync queued (0 messages processed)."))
    .toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sync now" })).toBeDisabled();
  expect(startSync).toHaveBeenCalledOnce();
});

test("shows partial failure and allows another sync", async () => {
  vi.mocked(getGmailConnection).mockResolvedValue({ connected: true, email: "a@example.test" });
  vi.mocked(getLatestSync).mockResolvedValue({
    run_id: "run-1", mode: "incremental", status: "partial", processed_count: 4,
    started_at: "2026-10-20T12:00:00Z", import_after: null,
    last_success_at: "2026-10-19T12:00:00Z",
  });
  render(<GmailConnection />);

  expect(await screen.findByText("Sync stopped after 4 messages. Try again."))
    .toBeInTheDocument();
  expect(screen.getByText(/Last successful sync:/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sync now" })).toBeEnabled();
});

test("revoked Gmail access offers reconnect instead of another sync", async () => {
  vi.mocked(getGmailConnection).mockResolvedValue({ connected: true, email: "a@example.test" });
  vi.mocked(getLatestSync).mockResolvedValue({
    run_id: "run-1", mode: "incremental", status: "reconnect_required",
    processed_count: 0, started_at: "2026-10-20T12:00:00Z", import_after: null,
    last_success_at: null,
  });
  vi.mocked(startGmailConnection).mockResolvedValue({
    authorization_url: "https://accounts.google.test/consent?state=opaque",
  });
  render(<GmailConnection />);

  expect(await screen.findByText("Gmail access expired. Reconnect Gmail to sync again."))
    .toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Sync now" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Reconnect Gmail" }));
  await waitFor(() => expect(navigateToGoogle).toHaveBeenCalledOnce());
});
