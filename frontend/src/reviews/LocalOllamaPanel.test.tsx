import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  ApiError, getAiQueueInput, getLocalAiInput, getSettings, listAiQueue,
  submitAiQueueResult, submitLocalAiSuggestion,
} from "../api/client";
import type { ReviewItem } from "../api/types";
import { LocalOllamaPanel } from "./LocalOllamaPanel";
import { listLocalModels, runLocalModel } from "./localOllama";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getAiQueueInput: vi.fn(), getLocalAiInput: vi.fn(), getSettings: vi.fn(), listAiQueue: vi.fn(),
    submitAiQueueResult: vi.fn(), submitLocalAiSuggestion: vi.fn(),
  };
});
vi.mock("./localOllama", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./localOllama")>();
  return { ...actual, listLocalModels: vi.fn(), runLocalModel: vi.fn() };
});

afterEach(() => vi.resetAllMocks());

const input = { generation: 2, system_prompt: "Synthetic system", user_prompt: "Synthetic email", format: {} };
const review: ReviewItem = {
  message_id: "review-1", reason: "unknown_stage", subject: "Synthetic update", sender: null,
  evidence: "", received_at: null, thread_id: null, source_url: null, application_id: null,
  proposed_status: null, proposed_season: null, proposed_year: null,
};

async function openPanel(mode: "off" | "plan" | "on", reviews: ReviewItem[] = [review]) {
  vi.mocked(getSettings).mockResolvedValue({ enabled: false, auto_apply_mode: mode } as Awaited<ReturnType<typeof getSettings>>);
  vi.mocked(listLocalModels).mockResolvedValue(["gemma4:latest"]);
  vi.mocked(runLocalModel).mockResolvedValue({ kind: "application_update" });
  const onFinished = vi.fn().mockResolvedValue(undefined);
  render(<LocalOllamaPanel reviews={reviews} failedIds={new Set()} onSuggested={vi.fn()} onFinished={onFinished} />);
  await screen.findByRole("option", { name: "gemma4:latest" });
  return onFinished;
}

test("test_panel_uses_ai_queue_when_mode_plan", async () => {
  const onFinished = await openPanel("plan");
  vi.mocked(listAiQueue).mockResolvedValue({ items: [{ message_id: "queue-1", received_at: null }], remaining: 1 });
  vi.mocked(getAiQueueInput).mockResolvedValue(input);
  vi.mocked(submitAiQueueResult).mockResolvedValue({ planned: true, executed: false, action: null });

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  await waitFor(() => expect(onFinished).toHaveBeenCalledOnce());
  expect(listAiQueue).toHaveBeenCalledWith(5, expect.any(AbortSignal));
  expect(getAiQueueInput).toHaveBeenCalledWith("queue-1", expect.any(AbortSignal));
  expect(submitAiQueueResult).toHaveBeenCalledWith("queue-1", {
    generation: 2, model: "gemma4:latest", extraction: { kind: "application_update" },
  }, expect.any(AbortSignal));
  expect(getLocalAiInput).not.toHaveBeenCalled();
  expect(submitLocalAiSuggestion).not.toHaveBeenCalled();
  expect(screen.getByText(/1 planned, 0 skipped, 0 failed/)).toBeInTheDocument();
});

test("test_panel_uses_reviews_when_mode_off", async () => {
  const onFinished = await openPanel("off");
  vi.mocked(getLocalAiInput).mockResolvedValue(input);
  vi.mocked(submitLocalAiSuggestion).mockResolvedValue({ saved: false, review: null });

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  await waitFor(() => expect(onFinished).toHaveBeenCalledOnce());
  expect(getLocalAiInput).toHaveBeenCalledWith("review-1", expect.any(AbortSignal));
  expect(submitLocalAiSuggestion).toHaveBeenCalledWith("review-1", {
    generation: 2, model: "gemma4:latest", extraction: { kind: "application_update" },
  }, expect.any(AbortSignal));
  expect(listAiQueue).not.toHaveBeenCalled();
  expect(getAiQueueInput).not.toHaveBeenCalled();
});

test("test_queue_result_counts_executed_in_on_mode", async () => {
  const onFinished = await openPanel("on", []);
  vi.mocked(listAiQueue).mockResolvedValue({ items: [{ message_id: "queue-1", received_at: null }], remaining: 1 });
  vi.mocked(getAiQueueInput).mockResolvedValue(input);
  vi.mocked(submitAiQueueResult).mockResolvedValue({ planned: true, executed: true, action: "create" });

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  await waitFor(() => expect(onFinished).toHaveBeenCalledOnce());
  expect(screen.getByText(/1 executed, 0 planned, 0 skipped, 0 failed/)).toBeInTheDocument();
  expect(runLocalModel).toHaveBeenCalledWith("gemma4:latest", input, expect.any(AbortSignal));
});

test("stops a queue batch when applying the result is unavailable", async () => {
  await openPanel("on", []);
  vi.mocked(listAiQueue).mockResolvedValue({
    items: [
      { message_id: "queue-1", received_at: null },
      { message_id: "queue-2", received_at: null },
    ],
    remaining: 2,
  });
  vi.mocked(getAiQueueInput).mockResolvedValue(input);
  vi.mocked(submitAiQueueResult).mockRejectedValue(new ApiError(503, "queue_unavailable"));

  fireEvent.click(screen.getByRole("button", { name: "Start analysis" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("AI queue is unavailable");
  expect(getAiQueueInput).toHaveBeenCalledTimes(1);
});
