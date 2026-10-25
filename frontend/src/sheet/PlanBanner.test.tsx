import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ApiError, applyPlan, getGmailConnection, getPlanReport, getSettings, setAutoApplyMode } from "../api/client";
import type { PlanReportResponse } from "../api/types";
import { Sheet } from "./Sheet";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    applyPlan: vi.fn(),
    getGmailConnection: vi.fn(),
    getPlanReport: vi.fn(),
    getSettings: vi.fn(),
    setAutoApplyMode: vi.fn(),
  };
});

const report: PlanReportResponse = {
  analyzed: 4,
  waiting: 0,
  report: {
    counts: { create: 2, update: 1, ignore: 1, needs_look: 0 },
    reasons: { new_application: 2, matched: 1, "not_application:job_alert": 1 },
    sample: [{
      message_id: "synthetic-1", action: "create", reason: "new_application",
      company: "Acme", role: "Engineer", status: "applied", season: "summer", year: 2027,
      target: null, subject: "Synthetic status", sender: "sender@example.test",
      source_url: "https://mail.google.com/mail/u/0/#all/synthetic-thread",
    }],
  },
};

function renderSheet(mode: "off" | "plan" | "on") {
  return render(<Sheet applications={[]} reviews={[]} onReload={vi.fn()} mode={mode} />);
}

beforeEach(() => {
  vi.mocked(getGmailConnection).mockResolvedValue({ connected: false, email: null });
  vi.mocked(getSettings).mockResolvedValue({
    available_providers: [], hugging_face_host: null, provider: null, key_hint: null, enabled: false,
    key_status: null, requests_today: 0, daily_limit: 50, usage_day: "2026-10-20",
  });
  vi.mocked(getPlanReport).mockResolvedValue(report);
  vi.mocked(applyPlan).mockResolvedValue({ queued: true });
  vi.mocked(setAutoApplyMode).mockResolvedValue({ auto_apply_mode: "plan" });
});

afterEach(() => vi.resetAllMocks());

test("test_banner_hidden_when_mode_off_or_on", async () => {
  const view = renderSheet("plan");
  expect(await screen.findByRole("button", { name: "Review plan" })).toBeInTheDocument();
  view.rerender(<Sheet applications={[]} reviews={[]} onReload={vi.fn()} mode="off" />);
  expect(screen.queryByRole("button", { name: "Review plan" })).not.toBeInTheDocument();
  view.rerender(<Sheet applications={[]} reviews={[]} onReload={vi.fn()} mode="on" />);
  expect(screen.queryByRole("button", { name: "Review plan" })).not.toBeInTheDocument();
  expect(getPlanReport).toHaveBeenCalledOnce();
});

test("test_apply_hidden_while_waiting", async () => {
  vi.mocked(getPlanReport).mockResolvedValue({ ...report, waiting: 3 });
  renderSheet("plan");

  expect(await screen.findByText("AI analyzed 4 of 7 emails")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Review plan" }));
  expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
});

test("test_apply_confirms_with_counts", async () => {
  const view = renderSheet("plan");
  fireEvent.click(await screen.findByRole("button", { name: "Review plan" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));

  expect(screen.getByText("Create 2 and update 1 applications?")).toBeInTheDocument();
  expect(applyPlan).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm apply" }));
  await waitFor(() => expect(applyPlan).toHaveBeenCalledOnce());
  expect(await screen.findByText("Applying…")).toBeInTheDocument();
  view.rerender(<Sheet applications={[]} reviews={[]} onReload={vi.fn()} mode="on" />);
  expect(screen.queryByText("Applying…")).not.toBeInTheDocument();
});

test("test_report_lists_sample", async () => {
  renderSheet("plan");
  fireEvent.click(await screen.findByRole("button", { name: "Review plan" }));

  const plan = screen.getByRole("region", { name: "AI plan report" });
  expect(within(plan).getByText("Create: 2")).toBeInTheDocument();
  expect(within(plan).getByText("new_application: 2")).toBeInTheDocument();
  expect(within(plan).getByRole("columnheader", { name: "Cycle" })).toBeInTheDocument();
  expect(within(plan).getByRole("columnheader", { name: "Open email" })).toBeInTheDocument();
  const row = within(plan).getByRole("row", { name: /Acme Engineer Applied Summer 2027 Create Open email/i });
  expect(within(row).getByRole("link", { name: "Open email" })).toHaveAttribute("href", report.report.sample[0].source_url);
});

test("test_start_tracking_sets_mode_plan", async () => {
  renderSheet("off");
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(screen.getByRole("button", { name: "Start automatic tracking (plan first)" }));

  await waitFor(() => expect(setAutoApplyMode).toHaveBeenCalledWith("plan"));
  expect(await screen.findByText("AI analyzed 4 of 4 emails")).toBeInTheDocument();
});

test("rechecks plan counts before applying and requires confirmation of changed counts", async () => {
  vi.mocked(getPlanReport).mockResolvedValueOnce(report).mockResolvedValueOnce({
    ...report, report: { ...report.report, counts: { ...report.report.counts, create: 3 } },
  }).mockResolvedValueOnce({
    ...report, report: { ...report.report, counts: { ...report.report.counts, create: 3 } },
  });
  renderSheet("plan");
  fireEvent.click(await screen.findByRole("button", { name: "Review plan" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm apply" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The plan changed. Review the new counts.");
  expect(screen.getByText("Create 3 and update 1 applications?")).toBeInTheDocument();
  expect(applyPlan).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm apply" }));
  await waitFor(() => expect(applyPlan).toHaveBeenCalledWith({ analyzed: 4, create: 3, update: 1 }));
});

test("a plan_changed apply response refreshes counts for reconfirmation", async () => {
  vi.mocked(getPlanReport).mockResolvedValueOnce(report).mockResolvedValueOnce(report).mockResolvedValueOnce({
    ...report, report: { ...report.report, counts: { ...report.report.counts, update: 2 } },
  });
  vi.mocked(applyPlan).mockRejectedValueOnce(new ApiError(409, "plan_changed"));
  renderSheet("plan");
  fireEvent.click(await screen.findByRole("button", { name: "Review plan" }));
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm apply" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("The plan changed. Review the new counts.");
  expect(screen.getByText("Create 2 and update 2 applications?")).toBeInTheDocument();
  expect(getPlanReport).toHaveBeenCalledTimes(3);
});

test("reloads after apply when settings turns on and stops polling on unmount", async () => {
  vi.useFakeTimers();
  try {
    const onReload = vi.fn();
    const view = render(<Sheet applications={[]} reviews={[]} onReload={onReload} mode="plan" />);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Review plan" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm apply" }));
    await act(async () => {});
    vi.mocked(getSettings).mockResolvedValue({
      available_providers: [], hugging_face_host: null, provider: null, key_hint: null, enabled: false,
      key_status: null, requests_today: 0, daily_limit: 50, usage_day: "2026-10-20", auto_apply_mode: "on",
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(onReload).toHaveBeenCalledOnce();
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(getSettings).toHaveBeenCalledOnce();
  } finally {
    vi.useRealTimers();
  }
});

test("stops checking settings after two minutes when apply stays pending", async () => {
  vi.useFakeTimers();
  try {
    const view = renderSheet("plan");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Review plan" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm apply" }));
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(getSettings).toHaveBeenCalledTimes(24);
    expect(screen.queryByText("Applying…")).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(getSettings).toHaveBeenCalledTimes(24);
    view.unmount();
  } finally {
    vi.useRealTimers();
  }
});

test("stops applying after two minutes even if a settings request hangs", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(getSettings).mockImplementation(() => new Promise(() => {}));
    const view = renderSheet("plan");
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Review plan" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm apply" }));
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(screen.queryByText("Applying…")).not.toBeInTheDocument();
    view.unmount();
  } finally {
    vi.useRealTimers();
  }
});
