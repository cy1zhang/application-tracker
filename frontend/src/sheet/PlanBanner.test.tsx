import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { applyPlan, getGmailConnection, getPlanReport, getSettings, setAutoApplyMode } from "../api/client";
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
