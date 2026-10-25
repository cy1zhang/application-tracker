import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError, listAiChanges, undoAiChange } from "../api/client";
import { makeApplication } from "../test/fixtures";
import { Sheet } from "./Sheet";

vi.mock("../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/client")>(),
  listAiChanges: vi.fn(), undoAiChange: vi.fn(),
}));

const application = makeApplication({ application_id: "app-1", company: "Acme", status: "interviewing" });
const older = {
  message_id: "old", received_at: "2026-10-20T12:00:00Z", action: "updated" as const,
  reason: "ai_update", application_id: "app-1", changes: { status: { before: "applied", after: "oa_received" } },
  subject: "Synthetic update", sender: "sender@example.test", source_url: "https://mail.google.com/mail/u/0/#all/old", undone_at: null,
};
const latest = {
  ...older, message_id: "new", received_at: "2026-10-21T12:00:00Z",
  changes: { status: { before: "oa_received", after: "interviewing" } },
  source_url: "https://mail.google.com/mail/u/0/#all/new",
};

afterEach(() => vi.resetAllMocks());

test("test_marker_shown_only_for_latest_ai_field", async () => {
  vi.mocked(listAiChanges).mockResolvedValue({ items: [latest, older], next_cursor: null });
  render(<Sheet applications={[application]} reviews={[]} onReload={vi.fn()} mode="live" />);

  const row = within(screen.getByRole("table")).getAllByRole("row")[1];
  await waitFor(() => expect(within(row).getByRole("button", { name: "AI change for Status" })).toBeInTheDocument());
  expect(within(row).queryByRole("button", { name: "AI change for Company" })).not.toBeInTheDocument();
  expect(within(row).getAllByRole("button", { name: /AI change for/ })).toHaveLength(1);
});

test("test_undone_change_has_no_marker", async () => {
  vi.mocked(listAiChanges).mockResolvedValue({ items: [{ ...latest, undone_at: "2026-10-22T12:00:00Z" }], next_cursor: null });
  render(<Sheet applications={[application]} reviews={[]} onReload={vi.fn()} mode="live" />);

  await waitFor(() => expect(listAiChanges).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: /AI change for/ })).not.toBeInTheDocument();
});

test("test_hover_shows_open_email_and_undo", async () => {
  vi.mocked(listAiChanges).mockResolvedValue({ items: [latest], next_cursor: null });
  render(<Sheet applications={[application]} reviews={[]} onReload={vi.fn()} mode="live" />);

  const marker = await screen.findByRole("button", { name: "AI change for Status" });
  marker.focus();
  expect(screen.getByRole("link", { name: "Open email" })).toHaveAttribute("href", latest.source_url);
  expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument();
  expect(screen.getByText(/Oct 21, 2026/)).toBeInTheDocument();
});

test("marker undo reports a hand edit conflict", async () => {
  vi.mocked(listAiChanges).mockResolvedValue({ items: [latest], next_cursor: null });
  vi.mocked(undoAiChange).mockRejectedValue(new ApiError(409, "changed_since"));
  render(<Sheet applications={[makeApplication({ ...application, status: "offer" })]} reviews={[]} onReload={vi.fn()} mode="live" />);

  const marker = await screen.findByRole("button", { name: "AI change for Status" });
  marker.focus();
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This cell changed after the AI set it.");
});
