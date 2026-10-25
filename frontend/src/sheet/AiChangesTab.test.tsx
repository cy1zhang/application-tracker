import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError, listAiChanges, reconsiderAiChange, undoAiChange } from "../api/client";
import { makeApplication } from "../test/fixtures";
import { Sheet } from "./Sheet";

vi.mock("../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/client")>(),
  listAiChanges: vi.fn(), undoAiChange: vi.fn(), reconsiderAiChange: vi.fn(),
}));

const application = makeApplication({ application_id: "app-1", company: "Acme", status: "interviewing" });
const update = {
  message_id: "msg-1", received_at: "2026-10-21T12:00:00Z", action: "updated" as const,
  reason: "ai_update", application_id: "app-1", changes: { status: { before: "applied", after: "interviewing" } },
  subject: "Synthetic update", sender: "sender@example.test", source_url: "https://mail.google.com/mail/u/0/#all/msg-1", undone_at: null,
};

afterEach(() => vi.resetAllMocks());

test("test_undo_calls_api_and_reloads", async () => {
  vi.mocked(listAiChanges)
    .mockResolvedValueOnce({ items: [update], next_cursor: null })
    .mockResolvedValue({ items: [{ ...update, undone_at: "2026-10-22T12:00:00Z" }], next_cursor: null });
  vi.mocked(undoAiChange).mockResolvedValue({ undone: true });
  const onReload = vi.fn();
  render(<Sheet applications={[application]} reviews={[]} onReload={onReload} mode="live" />);
  fireEvent.click(screen.getByRole("tab", { name: /AI changes/ }));

  expect(await screen.findByText("Acme → Interviewing")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() => expect(undoAiChange).toHaveBeenCalledWith("msg-1"));
  await waitFor(() => expect(onReload).toHaveBeenCalledTimes(1));
  expect(await screen.findByText("Already undone.")).toBeInTheDocument();
});

test("test_undo_changed_since_message", async () => {
  vi.mocked(listAiChanges).mockResolvedValue({ items: [update], next_cursor: null });
  vi.mocked(undoAiChange).mockRejectedValue(new ApiError(409, "changed_since"));
  render(<Sheet applications={[application]} reviews={[]} onReload={vi.fn()} mode="live" />);
  fireEvent.click(screen.getByRole("tab", { name: /AI changes/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Undo" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("This cell changed after the AI set it.");
});

test("test_tab_pages_50_with_show_more", async () => {
  const first = Array.from({ length: 50 }, (_, index) => ({ ...update, message_id: `msg-${index}`, action: "ignored" as const, reason: "not_application:job_alert" }));
  vi.mocked(listAiChanges).mockImplementation(async (cursor) => cursor
    ? { items: [{ ...update, message_id: "msg-50", action: "ignored", reason: "not_application:newsletter_or_event" }], next_cursor: null }
    : { items: first, next_cursor: "next-page" });
  render(<Sheet applications={[application]} reviews={[]} onReload={vi.fn()} mode="live" />);
  fireEvent.click(screen.getByRole("tab", { name: /AI changes/ }));

  const panel = screen.getByRole("tabpanel", { name: /AI changes/ });
  await waitFor(() => expect(within(panel).getAllByRole("listitem")).toHaveLength(50));
  expect(within(panel).queryByText("Ignored: newsletter or event")).not.toBeInTheDocument();
  fireEvent.click(within(panel).getByRole("button", { name: "Show more" }));
  await waitFor(() => expect(within(panel).getAllByRole("listitem")).toHaveLength(51));
  expect(within(panel).getByText("Ignored: newsletter or event")).toBeInTheDocument();
});

test("test_reconsider_on_ignored_line", async () => {
  vi.mocked(listAiChanges).mockResolvedValue({ items: [{ ...update, action: "ignored", reason: "not_application:job_alert" }], next_cursor: null });
  vi.mocked(reconsiderAiChange).mockResolvedValue({ queued: true });
  const onReload = vi.fn();
  render(<Sheet applications={[application]} reviews={[]} onReload={onReload} mode="live" />);
  fireEvent.click(screen.getByRole("tab", { name: /AI changes/ }));

  expect(await screen.findByText("Ignored: job alert")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "This was an application" }));
  await waitFor(() => expect(reconsiderAiChange).toHaveBeenCalledWith("msg-1"));
  await waitFor(() => expect(onReload).toHaveBeenCalledTimes(1));
});

test("an update with several fields uses its stage in the summary", async () => {
  vi.mocked(listAiChanges).mockResolvedValue({
    items: [{ ...update, changes: {
      role: { before: "Software Engineer Intern", after: "Platform Engineer Intern" },
      status: { before: "applied", after: "interviewing" },
    } }], next_cursor: null,
  });
  render(<Sheet applications={[application]} reviews={[]} onReload={vi.fn()} mode="live" />);
  fireEvent.click(screen.getByRole("tab", { name: /AI changes/ }));

  expect(await screen.findByText("Acme → Interviewing")).toBeInTheDocument();
});
