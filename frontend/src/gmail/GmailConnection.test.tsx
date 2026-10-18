import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  disconnectGmail,
  getGmailConnection,
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
  };
});
vi.mock("./navigation", () => ({ navigateToGoogle: vi.fn() }));

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
