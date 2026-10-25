import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { getGmailConnection, getSettings } from "../api/client";
import type { ReviewItem } from "../api/types";
import { makeApplication } from "../test/fixtures";
import { Sheet } from "./Sheet";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getGmailConnection: vi.fn(), getSettings: vi.fn() };
});

const acme = makeApplication({ application_id: "a", company: "Acme" });
const globex = makeApplication({ application_id: "b", company: "Globex", role: "Platform Co-op", role_type: "co_op" });
const review: ReviewItem = {
  message_id: "synthetic-message", reason: "ambiguous_application", evidence: "Synthetic evidence",
  received_at: null, thread_id: null, source_url: null, subject: null, sender: null,
  application_id: "a", proposed_status: null, proposed_season: null, proposed_year: null,
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
  expect(tabs.map((tab) => tab.textContent)).toEqual(["Applications (2)", "Needs a look (1)", "AI changes (0)"]);
  expect(tabs[0]).toHaveAttribute("aria-selected", "true");
  fireEvent.click(tabs[1]);
  expect(screen.getByText("Synthetic evidence")).toBeInTheDocument();
});

test("test_header_click_toggles_sort", () => {
  render(<Sheet applications={[globex, acme]} reviews={[]} onReload={vi.fn()} mode="live" />);
  const company = screen.getByRole("button", { name: "Company" });
  const rows = () => within(screen.getByRole("table")).getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[0].textContent);

  fireEvent.click(company);
  expect(rows()).toEqual(["Acme", "Globex"]);
  fireEvent.click(company);
  expect(rows()).toEqual(["Globex", "Acme"]);
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
