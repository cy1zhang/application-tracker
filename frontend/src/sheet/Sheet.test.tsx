import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { ApiError, getGmailConnection, getSettings, updateApplication } from "../api/client";
import type { ReviewItem } from "../api/types";
import { makeApplication } from "../test/fixtures";
import { Sheet } from "./Sheet";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getGmailConnection: vi.fn(), getSettings: vi.fn(), updateApplication: vi.fn() };
});

const acme = makeApplication({ application_id: "a", company: "Acme" });
const globex = makeApplication({ application_id: "b", company: "Globex", role: "Platform Co-op", role_type: "co_op" });
const review: ReviewItem = {
  message_id: "synthetic-message", reason: "ambiguous_application", evidence: "Synthetic evidence",
  received_at: null, thread_id: null, source_url: null, subject: null, sender: null,
  application_id: "a", proposed_status: null, proposed_season: null, proposed_year: null,
};
const unparsedReview: ReviewItem = {
  ...review, message_id: "unparsed-message", reason: "unparseable_message",
  subject: "Application received", application_id: null, evidence: "",
  source_url: "https://mail.google.com/mail/u/0/#all/synthetic-message",
};

beforeEach(() => {
  vi.mocked(getGmailConnection).mockResolvedValue({ connected: false, email: null });
  vi.mocked(getSettings).mockResolvedValue({
    available_providers: [], hugging_face_host: null, provider: null, key_hint: null, enabled: false,
    key_status: null, requests_today: 0, daily_limit: 50, usage_day: "2026-10-20",
  });
});

afterEach(() => vi.resetAllMocks());

test("test_tabs_render_with_counts", () => {
  render(<Sheet applications={[acme, globex]} reviews={[review]} onReload={vi.fn()} mode="live" />);

  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((tab) => tab.textContent)).toEqual(["Applications (2)", "Needs a look (1)", "Unparsed mail (0)", "AI changes (0)"]);
  expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  fireEvent.click(tabs[1]);
  expect(screen.getByText("Synthetic evidence")).toBeInTheDocument();
  fireEvent.click(tabs[0]);
  expect(screen.getByRole("table")).toBeInTheDocument();
  expect(tabs[0]).toHaveAttribute("aria-selected", "true");
});

test("unparsed mail has its own tab and does not inflate Needs a look", () => {
  render(<Sheet applications={[acme]} reviews={[review, unparsedReview]} onReload={vi.fn()} mode="live" />);

  fireEvent.click(screen.getByRole("tab", { name: "Needs a look (1)" }));
  expect(screen.getByText("Synthetic evidence")).toBeInTheDocument();
  expect(screen.queryByText("Application received")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Unparsed mail (1)" }));
  expect(screen.getByText("Application received")).toBeInTheDocument();
  expect(screen.queryByText("Synthetic evidence")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open email" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Dismiss email" })).toBeInTheDocument();
});

test("test_header_click_toggles_sort", () => {
  const sortAcme = makeApplication({ ...acme, status: "rejected", application_date: "2026-04-10" });
  const sortGlobex = makeApplication({ ...globex, status: "interviewing", application_date: "2026-06-10" });
  const early = makeApplication({ application_id: "early", company: "Early", status: "applied", application_date: "2026-01-10" });
  const late = makeApplication({ application_id: "late", company: "Late", status: "offer", application_date: "2026-12-10" });
  render(<Sheet applications={[sortGlobex, sortAcme, late, early]} reviews={[]} onReload={vi.fn()} mode="live" />);
  const company = screen.getByRole("button", { name: "Company" });
  const rows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[0].textContent);

  fireEvent.click(company);
  expect(rows()).toEqual(["Acme", "Early", "Globex", "Late"]);
  fireEvent.click(company);
  expect(rows()).toEqual(["Late", "Globex", "Early", "Acme"]);

  const status = screen.getByRole("button", { name: "Status" });
  fireEvent.click(status);
  expect(rows()).toEqual(["Early", "Globex", "Late", "Acme"]);
  fireEvent.click(status);
  expect(rows()).toEqual(["Acme", "Late", "Globex", "Early"]);

  const applied = within(screen.getByRole("columnheader", { name: "Applied" })).getByRole("button");
  fireEvent.click(applied);
  expect(rows()).toEqual(["Early", "Acme", "Globex", "Late"]);
  fireEvent.click(applied);
  expect(rows()).toEqual(["Late", "Globex", "Acme", "Early"]);
});

test("tabs use arrow keys to select and focus their labelled panels", () => {
  render(<Sheet applications={[acme]} reviews={[review]} onReload={vi.fn()} mode="live" />);
  const tabs = screen.getAllByRole("tab");
  expect(screen.getByRole("tablist", { name: "Sheet tabs" })).toBeInTheDocument();
  for (const tab of tabs) expect(document.getElementById(tab.getAttribute("aria-controls") ?? "missing")).toBeInTheDocument();
  expect(tabs.map((tab) => tab.getAttribute("tabindex"))).toEqual(["0", "-1", "-1", "-1"]);
  expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[0].id);
  expect(tabs[0]).toHaveAttribute("aria-controls", screen.getByRole("tabpanel").id);

  tabs[0].focus();
  fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
  expect(tabs[1]).toHaveFocus();
  expect(tabs[1]).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[1].id);
  expect(tabs[1]).toHaveAttribute("aria-controls", screen.getByRole("tabpanel").id);

  fireEvent.keyDown(tabs[1], { key: "ArrowRight" });
  expect(tabs[2]).toHaveFocus();
  expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[2].id);

  fireEvent.keyDown(tabs[2], { key: "ArrowRight" });
  expect(tabs[3]).toHaveFocus();
  expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", tabs[3].id);

  fireEvent.keyDown(tabs[3], { key: "ArrowRight" });
  expect(tabs[0]).toHaveFocus();
  fireEvent.keyDown(tabs[0], { key: "ArrowLeft" });
  expect(tabs[3]).toHaveFocus();
  expect(tabs.map((tab) => tab.getAttribute("tabindex"))).toEqual(["-1", "-1", "-1", "0"]);
});

test("test_search_and_filters_reduce_rows", () => {
  render(<Sheet applications={[acme, globex]} reviews={[]} onReload={vi.fn()} mode="live" />);
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(3);

  fireEvent.change(screen.getByLabelText("Search"), { target: { value: "platform" } });
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  expect(screen.getByRole("cell", { name: "Globex" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Role type"), { target: { value: "internship" } });
  expect(screen.getByText("No applications match these filters.")).toBeInTheDocument();
});

test("test_settings_button_opens_panel_with_gmail_ai_delete_and_ollama", () => {
  render(<Sheet applications={[acme]} reviews={[]} onReload={vi.fn()} mode="live" onAccountDeleted={vi.fn()} />);
  expect(screen.queryByRole("heading", { name: "Danger zone" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByRole("heading", { name: /gmail/i })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "AI assistance" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Danger zone" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Analyze with Ollama" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Analyze with Ollama" }));
  expect(screen.getByRole("heading", { name: "Local Ollama suggestions" })).toBeInTheDocument();
});

test("keeps Ollama controls in Settings when Needs a look is open", () => {
  render(<Sheet applications={[acme]} reviews={[review]} onReload={vi.fn()} mode="live" />);
  fireEvent.click(screen.getByRole("tab", { name: "Needs a look (1)" }));
  expect(screen.queryByRole("button", { name: "Analyze with Ollama" })).not.toBeInTheDocument();
});

test("test_new_row_button_opens_existing_create_form", () => {
  render(<Sheet applications={[]} reviews={[]} onReload={vi.fn()} mode="live" />);
  fireEvent.click(screen.getByRole("button", { name: "New row" }));

  expect(screen.getByRole("heading", { name: "New application" })).toBeInTheDocument();
  expect(screen.getByLabelText("Company")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Create application" })).toBeInTheDocument();
});

test("failed blur save stays visible through tab switches and New row until dismissed", async () => {
  vi.mocked(updateApplication).mockRejectedValue(new ApiError(409, "Stale version"));
  render(<Sheet applications={[acme]} reviews={[]} onReload={vi.fn()} mode="live" />);
  fireEvent.click(screen.getByRole("button", { name: "Software Engineer Intern" }));
  const editor = screen.getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  fireEvent.blur(editor, { relatedTarget: screen.getByRole("tab", { name: "Needs a look (0)" }) });
  fireEvent.click(screen.getByRole("tab", { name: "Needs a look (0)" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save Role for Acme: Changed elsewhere. Reload latest");
  fireEvent.click(screen.getByRole("tab", { name: "Applications (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "New row" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Couldn't save Role for Acme: Changed elsewhere. Reload latest");
  fireEvent.click(screen.getByRole("button", { name: "Dismiss save error for Role" }));
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
});

test("save error clears when its cell is edited again", async () => {
  vi.mocked(updateApplication).mockRejectedValue(new ApiError(409, "Stale version"));
  render(<Sheet applications={[acme]} reviews={[]} onReload={vi.fn()} mode="live" />);
  fireEvent.click(screen.getByRole("button", { name: "Software Engineer Intern" }));
  const editor = screen.getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  fireEvent.keyDown(editor, { key: "Enter" });
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save Role for Acme");
  fireEvent.click(screen.getByRole("button", { name: "Software Engineer Intern" }));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("ordinary save failure persists after leaving the grid", async () => {
  vi.mocked(updateApplication).mockRejectedValue(new ApiError(503, "Save unavailable"));
  render(<Sheet applications={[acme]} reviews={[]} onReload={vi.fn()} mode="live" />);
  fireEvent.click(screen.getByRole("button", { name: "Software Engineer Intern" }));
  const editor = screen.getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  fireEvent.blur(editor, { relatedTarget: screen.getByRole("button", { name: "New row" }) });
  fireEvent.click(screen.getByRole("button", { name: "New row" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save Role for Acme: Save unavailable");
});
