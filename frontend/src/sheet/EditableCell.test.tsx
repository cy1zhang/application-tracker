import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError, updateApplication } from "../api/client";
import { makeApplication } from "../test/fixtures";
import { SheetGrid } from "./SheetGrid";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, updateApplication: vi.fn() };
});

const application = makeApplication({ version: 7 });

function renderGrid(row = application) {
  const onReload = vi.fn().mockResolvedValue(undefined);
  const onSelect = vi.fn();
  const { rerender } = render(
    <SheetGrid
      applications={[row]}
      sortKey="updated_at"
      direction="desc"
      onSort={vi.fn()}
      onSelect={onSelect}
      onReload={onReload}
    />,
  );
  const cells = within(screen.getByRole("table")).getAllByRole("row")[1].querySelectorAll("td");
  return { cells, onReload, onSelect, rerenderRow: (nextRow: typeof row) => rerender(
    <SheetGrid applications={[nextRow]} sortKey="updated_at" direction="desc" onSort={vi.fn()} onSelect={onSelect} onReload={onReload} />,
  ) };
}

afterEach(() => vi.resetAllMocks());

test("test_enter_saves_with_expected_version", async () => {
  vi.mocked(updateApplication).mockResolvedValue(makeApplication({ role: "Platform Engineer", version: 8 }));
  const { cells, onReload } = renderGrid();
  fireEvent.click(within(cells[1]).getByRole("button"));
  const editor = within(cells[1]).getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  fireEvent.keyDown(editor, { key: "Enter" });

  await waitFor(() => expect(updateApplication).toHaveBeenCalledWith("app-1", {
    expected_version: 7,
    role: "Platform Engineer",
  }));
  await waitFor(() => expect(onReload).toHaveBeenCalledOnce());
});

test("test_escape_cancels", () => {
  const { cells } = renderGrid();
  fireEvent.click(within(cells[1]).getByRole("button"));
  const editor = within(cells[1]).getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Different role" } });
  fireEvent.keyDown(editor, { key: "Escape" });

  expect(within(cells[1]).getByRole("button")).toHaveTextContent("Software Engineer Intern");
  expect(updateApplication).not.toHaveBeenCalled();
});

test("test_unchanged_value_sends_nothing", () => {
  const { cells, onReload } = renderGrid(makeApplication({ company: null, version: 7 }));
  fireEvent.click(within(cells[0]).getByRole("button", { name: "Edit Company" }));
  fireEvent.keyDown(within(cells[0]).getByRole("textbox", { name: "Company" }), { key: "Enter" });

  expect(updateApplication).not.toHaveBeenCalled();
  expect(onReload).not.toHaveBeenCalled();
});

test("test_unknown_type_sends_nothing", () => {
  const { cells } = renderGrid(makeApplication({ role_type: null, version: 7 }));
  fireEvent.click(within(cells[2]).getByRole("button"));
  fireEvent.keyDown(within(cells[2]).getByRole("combobox", { name: "Type" }), { key: "Enter" });

  expect(updateApplication).not.toHaveBeenCalled();
});

test("test_clearing_sends_null", async () => {
  vi.mocked(updateApplication).mockResolvedValue(makeApplication({ application_date: null, version: 8 }));
  const { cells } = renderGrid();
  fireEvent.click(within(cells[5]).getByRole("button"));
  const editor = within(cells[5]).getByLabelText("Applied");
  fireEvent.change(editor, { target: { value: "" } });
  fireEvent.keyDown(editor, { key: "Enter" });

  await waitFor(() => expect(updateApplication).toHaveBeenCalledWith("app-1", {
    expected_version: 7,
    application_date: null,
  }));
});

test("test_409_shows_reload_message_and_reloads", async () => {
  vi.mocked(updateApplication).mockRejectedValue(new ApiError(409, "Stale version"));
  const { cells, onReload } = renderGrid();
  fireEvent.click(within(cells[1]).getByRole("button"));
  const editor = within(cells[1]).getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  fireEvent.keyDown(editor, { key: "Enter" });

  expect(await screen.findByRole("alert")).toHaveTextContent("Changed elsewhere. Reload latest");
  await waitFor(() => expect(onReload).toHaveBeenCalledOnce());
});

test("test_keyboard_tab_moves_between_cells", () => {
  const { cells } = renderGrid();
  const company = within(cells[0]).getByRole("button", { name: "Edit Company" });
  company.focus();
  fireEvent.keyDown(company, { key: "Enter" });
  const editor = within(cells[0]).getByRole("textbox", { name: "Company" });
  fireEvent.keyDown(editor, { key: "Tab" });

  expect(within(cells[1]).getByRole("button")).toHaveFocus();
});

test("test_edit_uses_version_from_start_even_after_row_refresh", async () => {
  vi.mocked(updateApplication).mockRejectedValue(new ApiError(409, "Stale version"));
  const { cells, rerenderRow } = renderGrid();
  fireEvent.click(within(cells[1]).getByRole("button"));
  const editor = within(cells[1]).getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  rerenderRow(makeApplication({ role: "Staff Engineer", version: 8 }));
  fireEvent.keyDown(editor, { key: "Enter" });

  await waitFor(() => expect(updateApplication).toHaveBeenCalledWith("app-1", {
    expected_version: 7,
    role: "Platform Engineer",
  }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Changed elsewhere. Reload latest");
});

test("test_enter_returns_focus_to_cell_after_save", async () => {
  vi.mocked(updateApplication).mockResolvedValue(makeApplication({ role: "Platform Engineer", version: 8 }));
  const { cells } = renderGrid();
  fireEvent.click(within(cells[1]).getByRole("button"));
  const editor = within(cells[1]).getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  fireEvent.keyDown(editor, { key: "Enter" });

  await waitFor(() => expect(within(cells[1]).getByRole("button")).toHaveFocus());
});

test("test_escape_returns_focus_without_blur_save", () => {
  const { cells } = renderGrid();
  fireEvent.click(within(cells[1]).getByRole("button"));
  const editor = within(cells[1]).getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  act(() => {
    fireEvent.keyDown(editor, { key: "Escape" });
    fireEvent.blur(editor, { relatedTarget: null });
  });

  expect(within(cells[1]).getByRole("button")).toHaveFocus();
  expect(updateApplication).not.toHaveBeenCalled();
});

test("test_blur_saves_changed_value", async () => {
  vi.mocked(updateApplication).mockResolvedValue(makeApplication({ role: "Platform Engineer", version: 8 }));
  const { cells } = renderGrid();
  fireEvent.click(within(cells[1]).getByRole("button"));
  const editor = within(cells[1]).getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  fireEvent.blur(editor, { relatedTarget: null });

  await waitFor(() => expect(updateApplication).toHaveBeenCalledWith("app-1", {
    expected_version: 7,
    role: "Platform Engineer",
  }));
  await waitFor(() => expect(within(cells[1]).getByRole("button")).toHaveFocus());
});

test("test_status_edit_sends_selected_status", async () => {
  vi.mocked(updateApplication).mockResolvedValue(makeApplication({ status: "offer", version: 8 }));
  const { cells } = renderGrid();
  fireEvent.click(within(cells[4]).getByRole("button"));
  const editor = within(cells[4]).getByRole("combobox", { name: "Status" });
  fireEvent.change(editor, { target: { value: "offer" } });
  fireEvent.keyDown(editor, { key: "Enter" });

  await waitFor(() => expect(updateApplication).toHaveBeenCalledWith("app-1", {
    expected_version: 7,
    status: "offer",
  }));
});

test("test_cycle_edit_sends_season_and_year", async () => {
  vi.mocked(updateApplication).mockResolvedValue(makeApplication({ season: "fall", year: 2028, version: 8 }));
  const { cells } = renderGrid();
  fireEvent.click(within(cells[3]).getByRole("button"));
  fireEvent.change(within(cells[3]).getByRole("combobox", { name: "Season" }), { target: { value: "fall" } });
  const year = within(cells[3]).getByRole("spinbutton", { name: "Year" });
  fireEvent.change(year, { target: { value: "2028" } });
  fireEvent.keyDown(year, { key: "Enter" });

  await waitFor(() => expect(updateApplication).toHaveBeenCalledWith("app-1", {
    expected_version: 7,
    season: "fall",
    year: 2028,
  }));
});

test("cycle keyboard tab reaches year before leaving the cell and saving", async () => {
  vi.mocked(updateApplication).mockResolvedValue(makeApplication({ season: "fall", year: 2028, version: 8 }));
  const { cells } = renderGrid();
  fireEvent.click(within(cells[3]).getByRole("button"));
  const season = within(cells[3]).getByRole("combobox", { name: "Season" });
  fireEvent.change(season, { target: { value: "fall" } });
  fireEvent.keyDown(season, { key: "Tab" });
  expect(within(cells[3]).getByRole("spinbutton", { name: "Year" })).toHaveFocus();
  const year = within(cells[3]).getByRole("spinbutton", { name: "Year" });
  fireEvent.change(year, { target: { value: "2028" } });
  fireEvent.keyDown(year, { key: "Tab", shiftKey: true });
  expect(season).toHaveFocus();
  fireEvent.keyDown(season, { key: "Tab" });
  fireEvent.keyDown(year, { key: "Tab" });
  expect(within(cells[4]).getByRole("button")).toHaveFocus();
  await waitFor(() => expect(updateApplication).toHaveBeenCalledWith("app-1", {
    expected_version: 7, season: "fall", year: 2028,
  }));
});

test("test_conflict_message_clears_on_next_edit", async () => {
  vi.mocked(updateApplication).mockRejectedValue(new ApiError(409, "Stale version"));
  const { cells } = renderGrid();
  fireEvent.click(within(cells[1]).getByRole("button"));
  const editor = within(cells[1]).getByRole("textbox", { name: "Role" });
  fireEvent.change(editor, { target: { value: "Platform Engineer" } });
  fireEvent.keyDown(editor, { key: "Enter" });
  expect(await screen.findByRole("alert")).toHaveTextContent("Changed elsewhere. Reload latest");

  fireEvent.click(within(cells[1]).getByRole("button"));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("test_company_detail_button_has_distinct_name", () => {
  const { cells, onSelect } = renderGrid();
  const details = within(cells[0]).getByRole("button", { name: "Open details for Acme" });
  fireEvent.click(details);
  expect(onSelect).toHaveBeenCalledWith("app-1");
  expect(cells[0]).not.toHaveAttribute("aria-label");
});
