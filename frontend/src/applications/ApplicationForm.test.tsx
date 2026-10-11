import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ApiError } from "../api/client";
import type { ApplicationValues } from "../api/types";
import { makeApplication } from "../test/fixtures";
import { ApplicationForm, buildPatch } from "./ApplicationForm";

function renderCreateForm(
  onSubmit = vi.fn<(values: ApplicationValues) => Promise<void>>(),
) {
  onSubmit.mockResolvedValue(undefined);
  render(
    <ApplicationForm
      submitLabel="Create application"
      onSubmit={onSubmit}
      onCancel={vi.fn()}
    />,
  );
  return onSubmit;
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: "Create application" }));
}

const UNCHANGED: ApplicationValues = {
  company: "Acme",
  role: "Software Engineer Intern",
  role_type: "internship",
  season: "summer",
  year: 2027,
  status: "applied",
  application_date: "2026-09-20",
};

test("does not default the status", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "Acme");
  fill("Role", "Software Engineer Intern");
  submit();

  expect(await screen.findByRole("alert")).toHaveTextContent("Choose a status.");
  expect(onSubmit).not.toHaveBeenCalled();
});

test("submits trimmed values and leaves blank optional fields unknown", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "  Acme  ");
  fill("Role", " Software Engineer Intern ");
  fill("Status", "applied");
  submit();

  await waitFor(() =>
    expect(onSubmit).toHaveBeenCalledWith({
      company: "Acme",
      role: "Software Engineer Intern",
      role_type: "unknown",
      season: null,
      year: null,
      status: "applied",
      application_date: null,
    }),
  );
});

test("submits every field when provided", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "Acme");
  fill("Role", "Software Engineer Intern");
  fill("Role type", "internship");
  fill("Season", "summer");
  fill("Year", "2027");
  fill("Status", "applied");
  fill("Application date", "2026-09-20");
  submit();

  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(UNCHANGED));
});

test("rejects a blank company", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "   ");
  fill("Role", "Software Engineer Intern");
  fill("Status", "applied");
  submit();

  expect(await screen.findByRole("alert")).toHaveTextContent("Enter a company.");
  expect(onSubmit).not.toHaveBeenCalled();
});

test("rejects a fractional year", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "Acme");
  fill("Role", "Software Engineer Intern");
  fill("Status", "applied");
  fill("Year", "2027.5");
  submit();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Year must be a whole number.",
  );
  expect(onSubmit).not.toHaveBeenCalled();
});

test("shows the API error when saving fails", async () => {
  const onSubmit = vi.fn<(values: ApplicationValues) => Promise<void>>();
  render(
    <ApplicationForm
      submitLabel="Create application"
      onSubmit={onSubmit.mockRejectedValue(
        new ApiError(0, "Could not reach the server. Is the backend running?"),
      )}
      onCancel={vi.fn()}
    />,
  );
  fill("Company", "Acme");
  fill("Role", "Software Engineer Intern");
  fill("Status", "applied");
  submit();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not reach the server. Is the backend running?",
  );
});

test("edit mode starts from the current values", () => {
  render(
    <ApplicationForm
      initial={makeApplication({ season: null })}
      submitLabel="Save changes"
      onSubmit={vi.fn()}
      onCancel={vi.fn()}
    />,
  );

  expect(screen.getByLabelText("Company")).toHaveValue("Acme");
  expect(screen.getByLabelText("Season")).toHaveValue("");
  expect(screen.getByLabelText("Year")).toHaveValue(2027);
  expect(screen.getByLabelText("Status")).toHaveValue("applied");
});

test("edit mode keeps fields that were already unknown", async () => {
  const onSubmit = vi
    .fn<(values: ApplicationValues) => Promise<void>>()
    .mockResolvedValue(undefined);
  render(
    <ApplicationForm
      initial={makeApplication({ company: null, status: null })}
      submitLabel="Save changes"
      onSubmit={onSubmit}
      onCancel={vi.fn()}
    />,
  );
  fill("Year", "2028");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() =>
    expect(onSubmit).toHaveBeenCalledWith({
      ...UNCHANGED,
      company: null,
      status: null,
      year: 2028,
    }),
  );
});

test("edit mode does not allow blanking a known company", async () => {
  const onSubmit = vi.fn<(values: ApplicationValues) => Promise<void>>();
  render(
    <ApplicationForm
      initial={makeApplication()}
      submitLabel="Save changes"
      onSubmit={onSubmit}
      onCancel={vi.fn()}
    />,
  );
  fill("Company", "  ");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Enter a company.");
  expect(onSubmit).not.toHaveBeenCalled();
});

test("buildPatch sends only changed fields with the expected version", () => {
  const original = makeApplication({ version: 3 });

  expect(
    buildPatch(original, { ...UNCHANGED, season: null, status: "interviewing" }),
  ).toEqual({ expected_version: 3, season: null, status: "interviewing" });
});

test("buildPatch returns null when nothing changed", () => {
  expect(buildPatch(makeApplication(), UNCHANGED)).toBeNull();
  expect(
    buildPatch(makeApplication({ role_type: null }), {
      ...UNCHANGED,
      role_type: "unknown",
    }),
  ).toBeNull();
});
