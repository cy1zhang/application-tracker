import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  render(
    <SheetGrid
      applications={[row]}
      sortKey="updated_at"
      direction="desc"
      onSort={vi.fn()}
      onSelect={vi.fn()}
      onReload={onReload}
    />,
  );
  const cells = within(screen.getByRole("table")).getAllByRole("row")[1].querySelectorAll("td");
  return { cells, onReload };
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
