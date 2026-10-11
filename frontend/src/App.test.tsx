import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import {
  ApiError,
  createApplication,
  getApplication,
  listAllApplications,
  listApplicationEvents,
} from "./api/client";
import type { Application } from "./api/types";
import { makeApplication } from "./test/fixtures";

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    listAllApplications: vi.fn(),
    createApplication: vi.fn(),
    getApplication: vi.fn(),
    listApplicationEvents: vi.fn(),
    updateApplication: vi.fn(),
  };
});

const internship = makeApplication({ application_id: "app-1" });
const coop = makeApplication({
  application_id: "app-2",
  company: "Globex",
  role: "Platform Co-op",
  role_type: "co_op",
  status: "interviewing",
});

afterEach(() => {
  vi.resetAllMocks();
});

test("shows internships by default with a status summary", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship, coop]);
  render(<App />);

  expect(await screen.findByRole("button", { name: "Acme" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Globex" })).not.toBeInTheDocument();
  const summary = screen.getByLabelText("Status summary");
  expect(within(summary).getByText("1 of 2")).toBeInTheDocument();
});

test("filters, searches, and explains empty results", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship, coop]);
  render(<App />);
  await screen.findByRole("button", { name: "Acme" });

  fireEvent.change(screen.getByLabelText("Role type"), {
    target: { value: "all" },
  });
  expect(screen.getByRole("button", { name: "Globex" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Search"), {
    target: { value: "platform" },
  });
  expect(screen.queryByRole("button", { name: "Acme" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Globex" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Search"), {
    target: { value: "no such company" },
  });
  expect(
    screen.getByText("No applications match these filters."),
  ).toBeInTheDocument();
});

test("shows an empty state before any applications exist", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([]);
  render(<App />);

  expect(await screen.findByText("No applications yet.")).toBeInTheDocument();
});

test("shows a load error and retries", async () => {
  vi.mocked(listAllApplications)
    .mockRejectedValueOnce(
      new ApiError(0, "Could not reach the server. Is the backend running?"),
    )
    .mockResolvedValueOnce([internship]);
  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not reach the server. Is the backend running?",
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(await screen.findByRole("button", { name: "Acme" })).toBeInTheDocument();
});

test("creates an application and opens its detail", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([]);
  vi.mocked(createApplication).mockResolvedValue(internship);
  vi.mocked(getApplication).mockResolvedValue(internship);
  vi.mocked(listApplicationEvents).mockResolvedValue([]);
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "New application" }));
  fireEvent.change(screen.getByLabelText("Company"), { target: { value: "Acme" } });
  fireEvent.change(screen.getByLabelText("Role"), {
    target: { value: "Software Engineer Intern" },
  });
  fireEvent.change(screen.getByLabelText("Status"), {
    target: { value: "applied" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create application" }));

  expect(
    await screen.findByRole("heading", { name: "Acme · Software Engineer Intern" }),
  ).toBeInTheDocument();
  expect(createApplication).toHaveBeenCalledWith({
    company: "Acme",
    role: "Software Engineer Intern",
    role_type: "unknown",
    season: null,
    year: null,
    status: "applied",
    application_date: null,
  });
});

test("keeps filters after opening a detail and going back", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship]);
  vi.mocked(getApplication).mockResolvedValue(internship);
  vi.mocked(listApplicationEvents).mockResolvedValue([]);
  render(<App />);

  fireEvent.change(await screen.findByLabelText("Search"), {
    target: { value: "acme" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Acme" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "← Back to applications" }),
  );

  expect(await screen.findByLabelText("Search")).toHaveValue("acme");
  expect(listAllApplications).toHaveBeenCalledTimes(2);
});

test("an older list response does not overwrite a newer one", async () => {
  let resolveSlowLoad: (applications: Application[]) => void = () => {};
  const offer = makeApplication({ application_id: "app-1", status: "offer" });
  vi.mocked(listAllApplications)
    .mockResolvedValueOnce([internship])
    .mockReturnValueOnce(
      new Promise((resolve) => {
        resolveSlowLoad = resolve;
      }),
    )
    .mockResolvedValueOnce([offer]);
  vi.mocked(getApplication).mockResolvedValue(internship);
  vi.mocked(listApplicationEvents).mockResolvedValue([]);
  render(<App />);

  async function openAndGoBack() {
    fireEvent.click(await screen.findByRole("button", { name: "Acme" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "← Back to applications" }),
    );
  }
  await openAndGoBack();
  await openAndGoBack();
  expect(await screen.findByRole("cell", { name: "Offer" })).toBeInTheDocument();

  await act(async () => {
    resolveSlowLoad([internship]);
  });

  expect(screen.getByRole("cell", { name: "Offer" })).toBeInTheDocument();
  expect(screen.queryByRole("cell", { name: "Applied" })).not.toBeInTheDocument();
});
