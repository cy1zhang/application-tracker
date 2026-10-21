import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ApiError, deleteAccount, getAccountStatus } from "../api/client";
import { DeleteAccount } from "./DeleteAccount";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, deleteAccount: vi.fn(), getAccountStatus: vi.fn() };
});

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
  vi.restoreAllMocks();
});

function type(value: string) {
  fireEvent.change(screen.getByLabelText(/Type DELETE/), { target: { value } });
}

const button = () => screen.getByRole("button", { name: "Delete account" });

test("the button stays disabled until the exact word is typed", () => {
  render(<DeleteAccount onAccountDeleted={vi.fn()} />);
  expect(button()).toBeDisabled();
  type("delete");
  expect(button()).toBeDisabled();
  type("DELETE ");
  expect(button()).toBeDisabled();
  type("DELETE");
  expect(button()).toBeEnabled();
  fireEvent.click(button());
  expect(deleteAccount).toHaveBeenCalledOnce();
});

test("does not send the request without confirmation", () => {
  render(<DeleteAccount onAccountDeleted={vi.fn()} />);
  fireEvent.click(button());
  fireEvent.submit(screen.getByLabelText(/Type DELETE/));
  expect(deleteAccount).not.toHaveBeenCalled();
});

test("discloses everything that is removed and what is not", () => {
  render(<DeleteAccount onAccountDeleted={vi.fn()} />);
  expect(screen.getByRole("heading", { name: "Danger zone" })).toBeInTheDocument();
  const text = document.body.textContent ?? "";
  for (const part of [
    "applications and their change history",
    "review items",
    "message references",
    "sync history",
    "stored AI key and settings",
    "Gmail connection",
    "sign-in account",
    "Your Gmail messages",
    "Google account",
    "already sent to your AI provider",
    "Google access is revoked on a best-effort basis",
    "Google account's permissions page",
    "Operational logs expire after 14 days",
    "minimal message reference",
    "will need approval again",
    "cannot be undone",
    "within a few minutes",
  ]) {
    expect(text).toContain(part);
  }
});

test("a click sends the confirmation once and disables the form", async () => {
  vi.mocked(deleteAccount).mockResolvedValue({ state: "deleting" });
  vi.mocked(getAccountStatus).mockResolvedValue({ state: "deleting" });
  render(<DeleteAccount onAccountDeleted={vi.fn()} />);
  type("DELETE");
  await act(async () => {
    fireEvent.click(button());
  });
  expect(deleteAccount).toHaveBeenCalledOnce();
  expect(screen.getByText("Deleting your data...")).toBeInTheDocument();
  expect(screen.queryByLabelText(/Type DELETE/)).not.toBeInTheDocument();
});

test("polls every 3 seconds until deleted, then calls onAccountDeleted once", async () => {
  const done = vi.fn();
  vi.mocked(deleteAccount).mockResolvedValue({ state: "deleting" });
  vi.mocked(getAccountStatus)
    .mockResolvedValueOnce({ state: "deleting" })
    .mockResolvedValue({ state: "deleted" });
  render(<DeleteAccount onAccountDeleted={done} />);
  type("DELETE");
  await act(async () => {
    fireEvent.click(button());
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2900);
  });
  expect(getAccountStatus).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
  expect(getAccountStatus).toHaveBeenCalledTimes(1);
  expect(done).not.toHaveBeenCalled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3000);
  });
  expect(done).toHaveBeenCalledOnce();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  expect(getAccountStatus).toHaveBeenCalledTimes(2);
  expect(done).toHaveBeenCalledOnce();
});

test("a 200 deleted response finishes immediately", async () => {
  const done = vi.fn();
  vi.mocked(deleteAccount).mockResolvedValue({ state: "deleted" });
  render(<DeleteAccount onAccountDeleted={done} />);
  type("DELETE");
  await act(async () => {
    fireEvent.click(button());
  });
  expect(done).toHaveBeenCalledOnce();
});

test("a 410 while polling counts as deleted", async () => {
  const done = vi.fn();
  vi.mocked(deleteAccount).mockResolvedValue({ state: "deleting" });
  vi.mocked(getAccountStatus).mockRejectedValue(new ApiError(410, "Account deleted"));
  render(<DeleteAccount onAccountDeleted={done} />);
  type("DELETE");
  await act(async () => {
    fireEvent.click(button());
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });
  expect(done).toHaveBeenCalledOnce();
});

test("a 503 is shown as text and keeps the input", async () => {
  vi.mocked(deleteAccount).mockRejectedValue(new ApiError(503, "Deletion worker is unavailable"));
  render(<DeleteAccount onAccountDeleted={vi.fn()} />);
  type("DELETE");
  await act(async () => {
    fireEvent.click(button());
  });
  expect(screen.getByRole("alert")).toHaveTextContent("Deletion worker is unavailable");
  expect(screen.getByLabelText(/Type DELETE/)).toHaveValue("DELETE");
  expect(button()).toBeEnabled();
});

test("unmounting stops polling", async () => {
  vi.mocked(deleteAccount).mockResolvedValue({ state: "deleting" });
  vi.mocked(getAccountStatus).mockResolvedValue({ state: "deleting" });
  const { unmount } = render(<DeleteAccount onAccountDeleted={vi.fn()} />);
  type("DELETE");
  await act(async () => {
    fireEvent.click(button());
  });
  unmount();
  expect(vi.getTimerCount()).toBe(0);
  await vi.advanceTimersByTimeAsync(10000);
  expect(getAccountStatus).not.toHaveBeenCalled();
});

test("resumed mode shows progress and polls without a form", async () => {
  vi.mocked(getAccountStatus).mockResolvedValue({ state: "deleting" });
  render(<DeleteAccount resumed onAccountDeleted={vi.fn()} />);
  expect(screen.getByText("Account deletion in progress")).toBeInTheDocument();
  expect(screen.queryByLabelText(/Type DELETE/)).not.toBeInTheDocument();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });
  expect(getAccountStatus).toHaveBeenCalledTimes(1);
});

test("never writes to browser storage", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  vi.mocked(deleteAccount).mockResolvedValue({ state: "deleted" });
  render(<DeleteAccount onAccountDeleted={vi.fn()} />);
  type("DELETE");
  await act(async () => {
    fireEvent.click(button());
  });
  expect(setItem).not.toHaveBeenCalled();
});
