import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError, getApplication, resolveReview } from "../api/client";
import type { ReviewItem } from "../api/types";
import { makeApplication } from "../test/fixtures";
import { ReviewQueue } from "./ReviewQueue";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getApplication: vi.fn(), resolveReview: vi.fn() };
});

afterEach(() => vi.resetAllMocks());

const review: ReviewItem = {
  message_id: "msg-1",
  reason: "ambiguous_application",
  evidence: "invite you to interview",
  received_at: "2026-10-18T00:00:00Z",
  thread_id: "thread-1",
  source_url: "https://mail.google.com/mail/u/0/#all/thread-1",
  subject: "Interview update",
  sender: "Recruiting <jobs@acme.test>",
  application_id: "app-1",
  proposed_status: "interviewing",
  proposed_season: null,
  proposed_year: null,
};

test("shows evidence and accepts a proposed change with the current version", async () => {
  vi.mocked(resolveReview).mockResolvedValue({ message_id: "msg-1", action: "accept", application_id: "app-1" });
  const onResolved = vi.fn().mockResolvedValue(undefined);
  render(
    <ReviewQueue reviews={[review]} applications={[makeApplication({ application_id: "app-1", version: 3 })]} onResolved={onResolved} onBack={() => {}} />,
  );

  expect(screen.getByText("invite you to interview")).toBeInTheDocument();
  expect(screen.getByText("Interview update")).toBeInTheDocument();
  expect(screen.getByText("From: Recruiting <jobs@acme.test>")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open email" })).toHaveAttribute("href", review.source_url);
  fireEvent.click(screen.getByRole("button", { name: "Accept proposal" }));

  expect(resolveReview).toHaveBeenCalledWith("msg-1", { action: "accept", expected_version: 3 });
  expect(await screen.findByText("Review saved.")).toBeInTheDocument();
});

test("shows a bounded first set of reviews and loads more on request", () => {
  const reviews = Array.from({ length: 21 }, (_, index) => ({
    ...review,
    message_id: `msg-${index}`,
    subject: `Subject ${index}`,
  }));
  render(<ReviewQueue reviews={reviews} applications={[]} onResolved={vi.fn()} onBack={() => {}} />);

  expect(screen.getAllByRole("article")).toHaveLength(20);
  expect(screen.queryByText("Subject 20")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show more reviews" }));
  expect(screen.getAllByRole("article")).toHaveLength(21);
  expect(screen.getByText("Subject 20")).toBeInTheDocument();
});

test("falls back to the application or a generic heading when headers are missing", () => {
  const bare = { ...review, subject: null, sender: null };
  render(
    <ReviewQueue
      reviews={[bare, { ...bare, message_id: "msg-2", application_id: null }]}
      applications={[makeApplication({ application_id: "app-1", company: "Acme", role: "Engineer" })]}
      onResolved={vi.fn()}
      onBack={() => {}}
    />,
  );

  expect(screen.getByRole("heading", { name: "Acme · Engineer" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Unmatched email" })).toBeInTheDocument();
  expect(screen.queryByText(/^From:/)).not.toBeInTheDocument();
});

test("rejects a review without changing an application", async () => {
  vi.mocked(resolveReview).mockResolvedValue({ message_id: "msg-1", action: "reject", application_id: null });
  render(
    <ReviewQueue reviews={[review]} applications={[]} onResolved={vi.fn().mockResolvedValue(undefined)} onBack={() => {}} />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Reject proposal" }));
  expect(resolveReview).toHaveBeenCalledWith("msg-1", { action: "reject" });
});

test("edits a matched application with its current version", async () => {
  const application = makeApplication({ application_id: "app-1", version: 3 });
  vi.mocked(resolveReview).mockResolvedValue({ message_id: "msg-1", action: "edit", application_id: "app-1" });
  render(<ReviewQueue reviews={[review]} applications={[application]} onResolved={vi.fn().mockResolvedValue(undefined)} onBack={() => {}} />);

  fireEvent.click(screen.getByRole("button", { name: "Edit and apply" }));
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "offer" } });
  fireEvent.click(screen.getByRole("button", { name: "Save review changes" }));

  await waitFor(() => expect(resolveReview).toHaveBeenCalledWith("msg-1", {
    action: "edit", application_id: "app-1", patch: { expected_version: 3, status: "offer" },
  }));
});

test("attaches an unmatched email without changing application fields", async () => {
  const application = makeApplication({ application_id: "app-1", version: 3 });
  vi.mocked(resolveReview).mockResolvedValue({ message_id: "msg-1", action: "attach", application_id: "app-1" });
  render(<ReviewQueue reviews={[{ ...review, application_id: null }]} applications={[application]} onResolved={vi.fn().mockResolvedValue(undefined)} onBack={() => {}} />);

  fireEvent.click(screen.getByRole("button", { name: "Attach to application" }));
  fireEvent.change(screen.getByLabelText("Application"), { target: { value: "app-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Attach email" }));
  expect(resolveReview).toHaveBeenCalledWith("msg-1", { action: "attach", application_id: "app-1", expected_version: 3 });
});

test("creates an application while preserving unknown status", async () => {
  vi.mocked(resolveReview).mockResolvedValue({ message_id: "msg-1", action: "create", application_id: "new" });
  render(<ReviewQueue reviews={[{ ...review, application_id: null, proposed_status: null }]} applications={[]} onResolved={vi.fn().mockResolvedValue(undefined)} onBack={() => {}} />);

  fireEvent.click(screen.getByRole("button", { name: "Create application" }));
  fireEvent.change(screen.getByLabelText("Company"), { target: { value: "Example Co" } });
  fireEvent.change(screen.getByLabelText("Role"), { target: { value: "Engineer" } });
  fireEvent.click(screen.getByRole("button", { name: "Create from email" }));
  await waitFor(() => expect(resolveReview).toHaveBeenCalledWith("msg-1", {
    action: "create", application: {
      company: "Example Co", role: "Engineer", role_type: "unknown", season: null,
      year: null, status: null, application_date: null,
    },
  }));
});

test("reloads after a stale resolution", async () => {
  vi.mocked(resolveReview).mockRejectedValue(new ApiError(409, "Stale version"));
  const onResolved = vi.fn().mockResolvedValue(undefined);
  render(<ReviewQueue reviews={[review]} applications={[makeApplication({ application_id: "app-1" })]} onResolved={onResolved} onBack={() => {}} />);

  fireEvent.click(screen.getByRole("button", { name: "Accept proposal" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("latest data is loaded");
  expect(onResolved).toHaveBeenCalledOnce();
});

test("retains actions for a review linked to a merged application ID", async () => {
  const retained = makeApplication({ application_id: "retained", version: 4 });
  vi.mocked(getApplication).mockResolvedValue(retained);
  vi.mocked(resolveReview).mockResolvedValue({ message_id: "msg-1", action: "accept", application_id: "retained" });
  render(<ReviewQueue reviews={[{ ...review, application_id: "merged-old" }]} applications={[retained]} onResolved={vi.fn().mockResolvedValue(undefined)} onBack={() => {}} />);

  expect(await screen.findByRole("button", { name: "Accept proposal" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Accept proposal" }));
  expect(resolveReview).toHaveBeenCalledWith("msg-1", { action: "accept", expected_version: 4 });
});

test("closes a stale edit form so it cannot resubmit an old version", async () => {
  vi.mocked(resolveReview).mockRejectedValue(new ApiError(409, "Stale version"));
  const onResolved = vi.fn().mockResolvedValue(undefined);
  render(<ReviewQueue reviews={[review]} applications={[makeApplication({ application_id: "app-1" })]} onResolved={onResolved} onBack={() => {}} />);

  fireEvent.click(screen.getByRole("button", { name: "Edit and apply" }));
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "offer" } });
  fireEvent.click(screen.getByRole("button", { name: "Save review changes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("latest data is loaded");
  expect(screen.queryByRole("button", { name: "Save review changes" })).not.toBeInTheDocument();
  expect(onResolved).toHaveBeenCalledOnce();
});
