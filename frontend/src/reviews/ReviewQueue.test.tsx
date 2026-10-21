import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError, getApplication, getLocalAiInput, getSettings, resolveReview, submitLocalAiSuggestion } from "../api/client";
import { OllamaUnreachableError, listLocalModels, runLocalModel } from "./localOllama";
import type { ReviewItem } from "../api/types";
import { makeApplication } from "../test/fixtures";
import { ReviewQueue } from "./ReviewQueue";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getApplication: vi.fn(), getLocalAiInput: vi.fn(), getSettings: vi.fn(), resolveReview: vi.fn(), submitLocalAiSuggestion: vi.fn() };
});
vi.mock("./localOllama", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./localOllama")>();
  return { ...actual, listLocalModels: vi.fn(), runLocalModel: vi.fn() };
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

  expect(resolveReview).toHaveBeenCalledWith("msg-1", { action: "accept", expected_version: 3, expected_review_version: 0 });
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
  expect(resolveReview).toHaveBeenCalledWith("msg-1", { action: "accept", expected_version: 4, expected_review_version: 0 });
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

const aiReview: ReviewItem = {
  ...review,
  message_id: "msg-ai",
  reason: "ai_proposal",
  rule_reason: "ambiguous_application",
  ai_provider: "openai",
  ai_proposal: {
    company: "Acme",
    role: "Engineer",
    status: "interviewing",
    evidence: { status: "<img src=x onerror=alert(1)> invite you to interview" },
  },
};

test("renders an AI suggestion with evidence quotes as plain text", () => {
  const { container } = render(
    <ReviewQueue reviews={[aiReview]} applications={[makeApplication({ application_id: "app-1" })]} onResolved={vi.fn()} onBack={() => {}} />,
  );

  expect(screen.getByText("AI suggestion")).toBeInTheDocument();
  expect(screen.getByText("The email could match more than one application.")).toBeInTheDocument();
  expect(screen.getByText(/Company: Acme/)).toBeInTheDocument();
  expect(screen.getByText(/<img src=x onerror=alert\(1\)> invite you to interview/)).toBeInTheDocument();
  expect(container.querySelector("img")).toBeNull();
  expect(screen.getByRole("button", { name: "Accept proposal" })).toBeInTheDocument();
});

test("accept includes the AI proposal revision displayed to the owner", () => {
  vi.mocked(resolveReview).mockResolvedValue({ message_id: "msg-ai", action: "accept", application_id: "app-1" });
  render(<ReviewQueue reviews={[{ ...aiReview, review_version: 1 }]} applications={[makeApplication({ application_id: "app-1", version: 3 })]} onResolved={vi.fn().mockResolvedValue(undefined)} onBack={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Accept proposal" }));
  expect(resolveReview).toHaveBeenCalledWith("msg-ai", {
    action: "accept", expected_version: 3, expected_review_version: 1,
  });
});

test.each([
  ["ai_unavailable", "AI was not used for this email."],
  ["ai_budget_exhausted", "AI was not used: daily limit reached."],
  ["ai_key_invalid", "AI was not used: your API key was rejected."],
])("shows the %s note", (note, text) => {
  render(<ReviewQueue reviews={[{ ...review, ai_note: note }]} applications={[]} onResolved={vi.fn()} onBack={() => {}} />);

  expect(screen.getByText(text)).toBeInTheDocument();
  expect(screen.queryByText("AI suggestion")).not.toBeInTheDocument();
});

test("offers no accept for an AI suggestion without an application or status", () => {
  render(
    <ReviewQueue
      reviews={[{ ...aiReview, application_id: null, proposed_status: null }]}
      applications={[]}
      onResolved={vi.fn()}
      onBack={() => {}}
    />,
  );

  expect(screen.getByText("AI suggestion")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Accept proposal" })).not.toBeInTheDocument();
});

test("a rule-only item has no AI block or note", () => {
  render(<ReviewQueue reviews={[review]} applications={[]} onResolved={vi.fn()} onBack={() => {}} />);

  expect(screen.queryByText("AI suggestion")).not.toBeInTheDocument();
  expect(screen.queryByText(/AI was not used/)).not.toBeInTheDocument();
});

test("analyzes eligible reviews sequentially and shows saved suggestions without resolving them", async () => {
  const second = { ...review, message_id: "msg-2", subject: "Second email" };
  const ineligible = { ...review, message_id: "msg-3", reason: "unparseable_message" };
  const onResolved = vi.fn().mockResolvedValue(undefined);
  vi.mocked(listLocalModels).mockResolvedValue(["gemma4:latest"]);
  vi.mocked(getSettings).mockResolvedValue({ enabled: false } as Awaited<ReturnType<typeof getSettings>>);
  vi.mocked(getLocalAiInput).mockResolvedValue({ generation: 1, system_prompt: "system", user_prompt: "email", format: {} });
  vi.mocked(runLocalModel).mockResolvedValue({ status: "interviewing" });
  vi.mocked(submitLocalAiSuggestion).mockImplementation(async (id) => ({
    saved: true,
    review: { ...(id === "msg-1" ? review : second), reason: "ai_proposal", rule_reason: "ambiguous_application", ai_proposal: { status: "interviewing", evidence: { status: "invite you to interview" } } },
  }));
  render(<ReviewQueue reviews={[review, second, ineligible, aiReview]} applications={[]} onResolved={onResolved} onBack={() => {}} />);

  fireEvent.click(screen.getByRole("button", { name: "Analyze with Ollama" }));
  expect(await screen.findByRole("option", { name: "gemma4:latest" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  await waitFor(() => expect(onResolved).toHaveBeenCalledOnce());
  expect(vi.mocked(getLocalAiInput).mock.calls.map(([id]) => id)).toEqual(["msg-1", "msg-2"]);
  expect(vi.mocked(runLocalModel)).toHaveBeenCalledTimes(2);
  expect(vi.mocked(submitLocalAiSuggestion)).toHaveBeenCalledTimes(2);
  expect(resolveReview).not.toHaveBeenCalled();
  expect(screen.getAllByText("AI suggestion")).toHaveLength(3);
  expect(screen.getByText(/2 suggested, 2 skipped, 0 failed/)).toBeInTheDocument();
});

test("shows setup guidance when Ollama is unavailable and warns about hosted AI", async () => {
  vi.mocked(listLocalModels).mockRejectedValue(new Error("Could not reach Ollama on this computer."));
  vi.mocked(getSettings).mockResolvedValue({ enabled: true, provider: "huggingface" } as Awaited<ReturnType<typeof getSettings>>);
  render(<ReviewQueue reviews={[review]} applications={[]} onResolved={vi.fn()} onBack={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Analyze with Ollama" }));

  expect(await screen.findByRole("alert", { name: "Ollama connection" })).toHaveTextContent("OLLAMA_ORIGINS");
  expect(screen.getByText(/Hosted AI is on/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Start analysis" })).toBeDisabled();
});

test("stops after an Ollama failure and can retry only unsaved reviews", async () => {
  const second = { ...review, message_id: "msg-2" };
  vi.mocked(listLocalModels).mockResolvedValue(["gemma4:latest"]);
  vi.mocked(getSettings).mockResolvedValue({ enabled: false } as Awaited<ReturnType<typeof getSettings>>);
  vi.mocked(getLocalAiInput).mockResolvedValue({ generation: 1, system_prompt: "system", user_prompt: "email", format: {} });
  vi.mocked(runLocalModel).mockRejectedValueOnce(new OllamaUnreachableError()).mockResolvedValue({ status: "interviewing" });
  vi.mocked(submitLocalAiSuggestion).mockResolvedValue({ saved: false, review: null });
  render(<ReviewQueue reviews={[review, second]} applications={[]} onResolved={vi.fn()} onBack={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Analyze with Ollama" }));
  await screen.findByRole("option", { name: "gemma4:latest" });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByText(/0 suggested, 0 skipped, 1 failed/)).toBeInTheDocument();
  expect(getLocalAiInput).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(getLocalAiInput).toHaveBeenCalledTimes(3));
  expect(await screen.findByText(/0 suggested, 2 skipped, 0 failed/)).toBeInTheDocument();
});

test("Stop aborts the current local request before the next review", async () => {
  const second = { ...review, message_id: "msg-2" };
  const onResolved = vi.fn().mockResolvedValue(undefined);
  vi.mocked(listLocalModels).mockResolvedValue(["gemma4:latest"]);
  vi.mocked(getSettings).mockResolvedValue({ enabled: false } as Awaited<ReturnType<typeof getSettings>>);
  vi.mocked(getLocalAiInput).mockResolvedValue({ generation: 1, system_prompt: "system", user_prompt: "email", format: {} });
  vi.mocked(runLocalModel).mockImplementationOnce((_model, _input, signal) => new Promise((_resolve, reject) => {
    signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  }));
  render(<ReviewQueue reviews={[review, second]} applications={[]} onResolved={onResolved} onBack={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Analyze with Ollama" }));
  await screen.findByRole("option", { name: "gemma4:latest" });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(runLocalModel).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));

  await waitFor(() => expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled());
  expect(getLocalAiInput).toHaveBeenCalledTimes(1);
  expect(submitLocalAiSuggestion).not.toHaveBeenCalled();
  expect(onResolved).toHaveBeenCalledOnce();
});

test("Gmail failure stops the batch with an explanation and refreshes the queue", async () => {
  const onResolved = vi.fn().mockResolvedValue(undefined);
  vi.mocked(listLocalModels).mockResolvedValue(["gemma4:latest"]);
  vi.mocked(getSettings).mockResolvedValue({ enabled: false } as Awaited<ReturnType<typeof getSettings>>);
  vi.mocked(getLocalAiInput).mockRejectedValue(new ApiError(503, "gmail_connection_unavailable"));
  render(<ReviewQueue reviews={[review, { ...review, message_id: "msg-2" }]} applications={[]} onResolved={onResolved} onBack={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Analyze with Ollama" }));
  await screen.findByRole("option", { name: "gemma4:latest" });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Gmail is unavailable");
  expect(getLocalAiInput).toHaveBeenCalledTimes(1);
  expect(onResolved).toHaveBeenCalledOnce();
});

const input = { generation: 1, system_prompt: "system", user_prompt: "email", format: {} };

async function openPanel(reviews: ReviewItem[]) {
  vi.mocked(listLocalModels).mockResolvedValue(["gemma4:latest"]);
  vi.mocked(getSettings).mockResolvedValue({ enabled: false } as Awaited<ReturnType<typeof getSettings>>);
  vi.mocked(getLocalAiInput).mockResolvedValue(input);
  vi.mocked(submitLocalAiSuggestion).mockImplementation(async (id) => ({
    saved: true, review: { ...review, message_id: id, reason: "ai_proposal", ai_proposal: { status: "interviewing", evidence: { status: "invite you to interview" } } } as ReviewItem,
  }));
  render(<ReviewQueue reviews={reviews} applications={[]} onResolved={vi.fn().mockResolvedValue(undefined)} onBack={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Analyze with Ollama" }));
  await screen.findByRole("option", { name: "gemma4:latest" });
}

const queue = (count: number) => Array.from({ length: count }, (_, index) => ({ ...review, message_id: `msg-${index + 1}` }));

test("continues past a per-review missing Gmail message, 422 and invalid model JSON", async () => {
  await openPanel(queue(4));
  vi.mocked(getLocalAiInput).mockRejectedValueOnce(new ApiError(503, "message_unavailable")).mockResolvedValue(input);
  vi.mocked(runLocalModel).mockResolvedValue({ status: "interviewing" });
  vi.mocked(submitLocalAiSuggestion)
    .mockRejectedValueOnce(new ApiError(422, "Invalid model output"))
    .mockResolvedValueOnce({ saved: false, review: null })
    .mockRejectedValueOnce(new ApiError(409, "review_changed"));
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByText(/0 suggested, 1 skipped, 3 failed/)).toBeInTheDocument();
  expect(getLocalAiInput).toHaveBeenCalledTimes(4);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText(/Gmail/)).not.toBeInTheDocument();
});

test("continues past model output that is not valid JSON", async () => {
  await openPanel(queue(2));
  vi.mocked(runLocalModel).mockRejectedValueOnce(new Error("Ollama returned invalid JSON.")).mockResolvedValue({ status: "interviewing" });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByText(/1 suggested, 0 skipped, 1 failed/)).toBeInTheDocument();
  expect(getLocalAiInput).toHaveBeenCalledTimes(2);
});

test.each([
  ["Ollama is unreachable", () => new OllamaUnreachableError(), "runLocalModel"],
  ["authentication is required", () => new ApiError(401, "Authentication required"), "getLocalAiInput"],
  ["Gmail is disconnected", () => new ApiError(503, "gmail_connection_unavailable"), "getLocalAiInput"],
  ["the Gmail connection changed", () => new ApiError(409, "gmail_connection_changed"), "getLocalAiInput"],
])("stops the batch when %s", async (_name, makeError, where) => {
  await openPanel(queue(3));
  vi.mocked(runLocalModel).mockResolvedValue({ status: "interviewing" });
  if (where === "runLocalModel") vi.mocked(runLocalModel).mockRejectedValueOnce(makeError());
  else vi.mocked(getLocalAiInput).mockRejectedValueOnce(makeError());
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByRole("alert")).toBeInTheDocument();
  expect(getLocalAiInput).toHaveBeenCalledTimes(1);
});

test("does not blame Gmail when Ollama is unreachable", async () => {
  await openPanel(queue(2));
  vi.mocked(runLocalModel).mockRejectedValue(new OllamaUnreachableError());
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach Ollama");
  expect(screen.getByRole("alert")).not.toHaveTextContent("Gmail");
});

test("failed reviews are skipped by the next Start in the same session", async () => {
  await openPanel(queue(3));
  vi.mocked(runLocalModel).mockRejectedValueOnce(new Error("Ollama returned invalid JSON.")).mockResolvedValue({ status: "interviewing" });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  expect(await screen.findByText(/2 suggested, 0 skipped, 1 failed/)).toBeInTheDocument();
  vi.mocked(getLocalAiInput).mockClear();

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled());
  expect(getLocalAiInput).not.toHaveBeenCalledWith("msg-1", expect.anything());
});

test("a global stop does not remember the interrupted review as failed", async () => {
  await openPanel(queue(2));
  vi.mocked(runLocalModel).mockRejectedValueOnce(new OllamaUnreachableError()).mockResolvedValue({ status: "interviewing" });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await screen.findByRole("alert");
  vi.mocked(getLocalAiInput).mockClear();

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(vi.mocked(getLocalAiInput).mock.calls.map(([id]) => id)).toEqual(["msg-1", "msg-2"]));
});

test("the review limit defaults to 5, is capped by the eligible count and caps processing", async () => {
  await openPanel(queue(7));
  const limit = screen.getByLabelText("Reviews to analyze");
  expect(limit).toHaveValue(5);
  expect(limit).toHaveAttribute("min", "1");
  expect(limit).toHaveAttribute("max", "7");
  vi.mocked(runLocalModel).mockResolvedValue({ status: "interviewing" });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByText(/5 suggested/)).toBeInTheDocument();
  expect(vi.mocked(getLocalAiInput).mock.calls.map(([id]) => id)).toEqual(["msg-1", "msg-2", "msg-3", "msg-4", "msg-5"]);
});

test("a limit of 1 analyzes exactly one review", async () => {
  await openPanel(queue(3));
  vi.mocked(runLocalModel).mockResolvedValue({ status: "interviewing" });
  fireEvent.change(screen.getByLabelText("Reviews to analyze"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByText(/1 suggested/)).toBeInTheDocument();
  expect(getLocalAiInput).toHaveBeenCalledTimes(1);
  expect(getLocalAiInput).toHaveBeenCalledWith("msg-1", expect.anything());
});

test("the limit counts only reviews that have not failed before", async () => {
  await openPanel(queue(3));
  vi.mocked(runLocalModel).mockRejectedValueOnce(new Error("Ollama returned invalid JSON.")).mockResolvedValue({ status: "interviewing" });
  fireEvent.change(screen.getByLabelText("Reviews to analyze"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await screen.findByText(/0 suggested, 0 skipped, 1 failed/);
  vi.mocked(getLocalAiInput).mockClear();

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(getLocalAiInput).toHaveBeenCalledTimes(1));
  expect(getLocalAiInput).toHaveBeenCalledWith("msg-2", expect.anything());
});

test.each([
  ["429", new ApiError(429, "Too many requests")],
  ["502", new ApiError(502, "Bad gateway")],
])("stops the batch with a busy message on API %s", async (_name, error) => {
  await openPanel(queue(3));
  vi.mocked(getLocalAiInput).mockRejectedValueOnce(error);
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The app is busy or unreachable. Wait a moment, then start again.");
  expect(getLocalAiInput).toHaveBeenCalledTimes(1);
});

test.each([
  ["500", new ApiError(500, "Internal error")],
  ["504", new ApiError(504, "Gateway timeout")],
])("an API %s counts as failed, continues, and is retried on the next Start", async (_name, error) => {
  await openPanel(queue(3));
  vi.mocked(runLocalModel).mockResolvedValue({ status: "interviewing" });
  vi.mocked(getLocalAiInput).mockRejectedValueOnce(error);
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  expect(await screen.findByText(/2 suggested, 0 skipped, 1 failed/)).toBeInTheDocument();
  vi.mocked(getLocalAiInput).mockClear();

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(getLocalAiInput).toHaveBeenCalledWith("msg-1", expect.anything()));
});

test.each([
  ["422", new ApiError(422, "Invalid model output")],
  ["review_changed", new ApiError(409, "review_changed")],
  ["message_unavailable", new ApiError(503, "message_unavailable")],
])("a %s failure is not retried by the next Start", async (_name, error) => {
  await openPanel(queue(3));
  vi.mocked(runLocalModel).mockResolvedValue({ status: "interviewing" });
  vi.mocked(getLocalAiInput).mockRejectedValueOnce(error);
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  expect(await screen.findByText(/2 suggested, 0 skipped, 1 failed/)).toBeInTheDocument();
  vi.mocked(getLocalAiInput).mockClear();

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled());
  expect(getLocalAiInput).not.toHaveBeenCalledWith("msg-1", expect.anything());
});

test("a failed review is still skipped after the panel is closed and reopened", async () => {
  await openPanel(queue(3));
  vi.mocked(runLocalModel).mockRejectedValueOnce(new Error("Ollama returned invalid JSON.")).mockResolvedValue({ status: "interviewing" });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  expect(await screen.findByText(/2 suggested, 0 skipped, 1 failed/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Analyze with Ollama" }));
  expect(screen.queryByRole("button", { name: "Start analysis" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Analyze with Ollama" }));
  await screen.findByRole("option", { name: "gemma4:latest" });
  vi.mocked(getLocalAiInput).mockClear();

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Start analysis" })).toBeEnabled());
  expect(getLocalAiInput).not.toHaveBeenCalledWith("msg-1", expect.anything());
});

test("a 404 counts as skipped and is not remembered as failed", async () => {
  await openPanel(queue(3));
  vi.mocked(runLocalModel).mockResolvedValue({ status: "interviewing" });
  vi.mocked(getLocalAiInput).mockRejectedValueOnce(new ApiError(404, "Review not found"));
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  expect(await screen.findByText(/2 suggested, 1 skipped, 0 failed/)).toBeInTheDocument();
  vi.mocked(getLocalAiInput).mockClear();

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  await waitFor(() => expect(getLocalAiInput).toHaveBeenCalledWith("msg-1", expect.anything()));
});

test.each(["0", ""])("a limit of %j analyzes exactly one review", async (value) => {
  await openPanel(queue(3));
  vi.mocked(runLocalModel).mockResolvedValue({ status: "interviewing" });
  fireEvent.change(screen.getByLabelText("Reviews to analyze"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByText(/1 suggested/)).toBeInTheDocument();
  expect(getLocalAiInput).toHaveBeenCalledTimes(1);
});
