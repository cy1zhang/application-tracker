import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError, mergeApplication } from "../api/client";
import { makeApplication } from "../test/fixtures";
import { MergeForm } from "./MergeForm";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, mergeApplication: vi.fn() };
});

afterEach(() => vi.resetAllMocks());

test("requires a choice for a conflicting status before merging", async () => {
  const kept = makeApplication({ application_id: "keep", status: "applied", version: 2 });
  const duplicate = makeApplication({ application_id: "duplicate", status: "offer", version: 4 });
  vi.mocked(mergeApplication).mockResolvedValue(kept);
  render(
    <MergeForm retained={kept} candidates={[duplicate]} onMerged={vi.fn().mockResolvedValue(undefined)} onCancel={() => {}} />,
  );

  fireEvent.change(screen.getByLabelText("Duplicate application"), { target: { value: "duplicate" } });
  fireEvent.click(screen.getByRole("button", { name: "Merge applications" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Choose a value for Status.");
  expect(mergeApplication).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "duplicate" } });
  fireEvent.click(screen.getByRole("button", { name: "Merge applications" }));
  expect(mergeApplication).toHaveBeenCalledWith("keep", {
    duplicate_id: "duplicate",
    expected_keep_version: 2,
    expected_duplicate_version: 4,
    resolutions: { status: "duplicate" },
  });
});

test("previews conflicting values and reloads after a stale version", async () => {
  const kept = makeApplication({ application_id: "keep", status: "applied" });
  const duplicate = makeApplication({ application_id: "duplicate", status: "offer" });
  vi.mocked(mergeApplication).mockRejectedValue(new ApiError(409, "Stale version"));
  const onReload = vi.fn().mockResolvedValue(undefined);
  render(<MergeForm retained={kept} candidates={[duplicate]} onMerged={vi.fn()} onReload={onReload} onCancel={() => {}} />);

  fireEvent.change(screen.getByLabelText("Duplicate application"), { target: { value: "duplicate" } });
  expect(screen.getByRole("option", { name: "Keep: Applied" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Duplicate: Offer" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "keep" } });
  fireEvent.click(screen.getByRole("button", { name: "Merge applications" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Latest values are loaded");
  expect(onReload).toHaveBeenCalledOnce();
  expect(screen.getByLabelText("Status")).toHaveValue("");
});

test("previews known values copied into empty fields", () => {
  const kept = makeApplication({ application_id: "keep", season: null });
  const duplicate = makeApplication({ application_id: "duplicate", season: "summer" });
  render(<MergeForm retained={kept} candidates={[duplicate]} onMerged={vi.fn()} onCancel={() => {}} />);

  fireEvent.change(screen.getByLabelText("Duplicate application"), { target: { value: "duplicate" } });
  expect(screen.getByText("Season: Unknown → Summer")).toBeInTheDocument();
});
