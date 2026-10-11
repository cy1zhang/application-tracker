import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  ApiError,
  getApplication,
  listApplicationEvents,
  updateApplication,
} from "../api/client";
import type { ApplicationEvent } from "../api/types";
import { makeApplication } from "../test/fixtures";
import { ApplicationDetail } from "./ApplicationDetail";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getApplication: vi.fn(),
    listApplicationEvents: vi.fn(),
    updateApplication: vi.fn(),
  };
});

const created: ApplicationEvent = {
  event_id: "event-1",
  application_id: "app-1",
  event_type: "created",
  origin: "user",
  occurred_at: "2026-09-20T12:00:00+00:00",
  changes: {
    company: { before: null, after: "Acme" },
    status: { before: null, after: "applied" },
    season: { before: null, after: null },
  },
};

const updated: ApplicationEvent = {
  event_id: "event-2",
  application_id: "app-1",
  event_type: "updated",
  origin: "user",
  occurred_at: "2026-09-21T12:00:00+00:00",
  changes: { status: { before: "applied", after: "interviewing" } },
};

afterEach(() => {
  vi.resetAllMocks();
});

function renderDetail() {
  render(<ApplicationDetail applicationId="app-1" onBack={vi.fn()} />);
}

async function startEditingStatus(value: string) {
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Status"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
}

test("shows fields, unknown values, and history", async () => {
  vi.mocked(getApplication).mockResolvedValue(makeApplication({ season: null }));
  vi.mocked(listApplicationEvents).mockResolvedValue([created, updated]);
  renderDetail();

  expect(
    await screen.findByRole("heading", { name: "Acme · Software Engineer Intern" }),
  ).toBeInTheDocument();
  expect(screen.getByText("2027 (season unknown)")).toBeInTheDocument();
  expect(screen.getByText("Company: Acme")).toBeInTheDocument();
  expect(screen.getByText("Status: Applied → Interviewing")).toBeInTheDocument();
  expect(screen.queryByText("Season: Unknown")).not.toBeInTheDocument();
  expect(screen.getByText(/^Created by you/)).toBeInTheDocument();
});

test("saves only changed fields and shows the new history", async () => {
  vi.mocked(getApplication)
    .mockResolvedValueOnce(makeApplication())
    .mockResolvedValueOnce(makeApplication({ status: "interviewing", version: 2 }));
  vi.mocked(listApplicationEvents)
    .mockResolvedValueOnce([created])
    .mockResolvedValueOnce([created, updated]);
  vi.mocked(updateApplication).mockResolvedValue(
    makeApplication({ status: "interviewing", version: 2 }),
  );
  renderDetail();

  await startEditingStatus("interviewing");

  await waitFor(() =>
    expect(updateApplication).toHaveBeenCalledWith("app-1", {
      expected_version: 1,
      status: "interviewing",
    }),
  );
  expect(
    await screen.findByText("Status: Applied → Interviewing"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("saving without changes does not call the API", async () => {
  vi.mocked(getApplication).mockResolvedValue(makeApplication());
  vi.mocked(listApplicationEvents).mockResolvedValue([created]);
  renderDetail();

  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(updateApplication).not.toHaveBeenCalled();
});

test("a stale edit keeps the form and offers to reload", async () => {
  vi.mocked(getApplication).mockResolvedValue(makeApplication());
  vi.mocked(listApplicationEvents).mockResolvedValue([created]);
  vi.mocked(updateApplication).mockRejectedValue(
    new ApiError(409, "Application version is stale"),
  );
  renderDetail();

  await startEditingStatus("offer");

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "changed since you opened it",
  );
  expect(screen.getByLabelText("Status")).toHaveValue("offer");

  fireEvent.click(screen.getByRole("button", { name: "Reload latest" }));

  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(getApplication).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("shows a missing application as an error", async () => {
  vi.mocked(getApplication).mockRejectedValue(
    new ApiError(404, "Application not found"),
  );
  vi.mocked(listApplicationEvents).mockResolvedValue([]);
  renderDetail();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Application not found",
  );
});
