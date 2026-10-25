import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { getApplication, listAiChanges, listApplicationEvents } from "../api/client";
import type { ReviewItem } from "../api/types";
import { makeApplication } from "../test/fixtures";
import { Sheet } from "./Sheet";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getApplication: vi.fn(),
    listAiChanges: vi.fn(),
    listApplicationEvents: vi.fn(),
  };
});

const acme = makeApplication({ application_id: "app/1", company: "Acme", role: "Engineer", version: 7 });
const globex = makeApplication({ application_id: "app-2", company: "Globex", role: "Engineer" });

beforeEach(() => {
  vi.mocked(listAiChanges).mockResolvedValue({ items: [], next_cursor: null });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

function openRowMenu() {
  fireEvent.click(screen.getByRole("button", { name: "Row actions for Acme · Engineer" }));
}

test("test_delete_confirms_and_sends_expected_version", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204 });
  vi.stubGlobal("fetch", fetchMock);
  const onReload = vi.fn().mockResolvedValue(undefined);
  render(<Sheet applications={[acme]} reviews={[]} onReload={onReload} mode="live" />);

  openRowMenu();
  fireEvent.click(screen.getByRole("button", { name: "Delete row" }));
  expect(confirm).toHaveBeenCalledWith("Delete Acme · Engineer?");
  expect(fetchMock).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Delete row" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/applications/app%2F1?expected_version=7",
    { method: "DELETE", headers: undefined },
  ));
  await waitFor(() => expect(onReload).toHaveBeenCalledOnce());
});

test("delete reports a stale version and reloads the row", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: false, status: 409, json: async () => ({ detail: "Application version is stale" }),
  }));
  const onReload = vi.fn().mockResolvedValue(undefined);
  render(<Sheet applications={[acme]} reviews={[]} onReload={onReload} mode="live" />);

  openRowMenu();
  fireEvent.click(screen.getByRole("button", { name: "Delete row" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Changed elsewhere. Reload latest");
  expect(onReload).toHaveBeenCalledOnce();
});

test("test_history_opens_drawer", async () => {
  vi.mocked(getApplication).mockResolvedValue(acme);
  vi.mocked(listApplicationEvents).mockResolvedValue([{
    event_id: "event-1", application_id: acme.application_id, event_type: "created",
    origin: "user", occurred_at: acme.created_at,
    changes: { company: { before: null, after: "Acme" } },
  }]);
  render(<Sheet applications={[acme]} reviews={[]} onReload={vi.fn()} mode="live" />);

  openRowMenu();
  fireEvent.click(screen.getByRole("button", { name: "History" }));
  const drawer = await screen.findByRole("complementary", { name: "Application history" });
  expect(within(drawer).getByRole("button", { name: "Close" })).toHaveFocus();
  expect(within(drawer).getByText("Company: Acme")).toBeInTheDocument();
  expect(screen.getByRole("table")).toBeInTheDocument();
  fireEvent.click(within(drawer).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("complementary", { name: "Application history" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Row actions for Acme · Engineer" })).toHaveFocus();
});

test("test_merge_opens_existing_form", () => {
  render(<Sheet applications={[acme, globex]} reviews={[]} onReload={vi.fn()} mode="live" />);

  openRowMenu();
  fireEvent.click(screen.getByRole("button", { name: "Merge duplicate" }));
  const drawer = screen.getByRole("complementary", { name: "Merge duplicate" });
  expect(within(drawer).getByRole("heading", { name: "Merge duplicate" })).toBeInTheDocument();
  expect(within(drawer).getByRole("option", { name: "Globex · Engineer" })).toBeInTheDocument();
  expect(screen.getByRole("table")).toBeInTheDocument();
});

test("test_needs_a_look_tab_shows_review_queue", () => {
  const review: ReviewItem = {
    message_id: "synthetic-review", reason: "ambiguous_application", evidence: "Synthetic evidence",
    received_at: null, thread_id: null, source_url: "https://example.test/email", subject: null, sender: null,
    application_id: acme.application_id, proposed_status: "interviewing", proposed_season: null, proposed_year: null,
  };
  render(<Sheet applications={[acme]} reviews={[review]} onReload={vi.fn()} mode="live" />);

  fireEvent.click(screen.getByRole("tab", { name: "Needs a look (1)" }));
  expect(screen.getByRole("heading", { name: "Needs review" })).toBeInTheDocument();
  expect(screen.getByText("Synthetic evidence")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Accept proposal" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open email" })).toHaveAttribute("href", "https://example.test/email");
  expect(screen.queryByRole("button", { name: "Analyze with Ollama" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByRole("button", { name: "Analyze with Ollama" })).toBeInTheDocument();
});
