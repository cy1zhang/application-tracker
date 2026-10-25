import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import App from "./App";
import {
  ApiError,
  createApplication,
  getApplication,
  listAllApplications,
  listAllReviews,
  listApplicationEvents,
  getAccountStatus,
  getGmailConnection,
  getSettings,
} from "./api/client";
import type { Application } from "./api/types";
import { makeApplication } from "./test/fixtures";

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    listAllApplications: vi.fn(),
    listAllReviews: vi.fn(),
    createApplication: vi.fn(),
    getApplication: vi.fn(),
    listApplicationEvents: vi.fn(),
    updateApplication: vi.fn(),
    getAccountStatus: vi.fn(),
    getGmailConnection: vi.fn(),
    getSettings: vi.fn(),
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

beforeEach(() => {
  vi.mocked(listAllReviews).mockResolvedValue([]);
  vi.mocked(getGmailConnection).mockResolvedValue({ connected: false, email: null });
  vi.mocked(getSettings).mockResolvedValue({
    available_providers: [], hugging_face_host: null, provider: null, key_hint: null, enabled: false,
    key_status: null, requests_today: 0, daily_limit: 50, usage_day: "2026-10-20",
  });
});

test("shows all applications in the sheet by default", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship, coop]);
  render(<App />);

  expect(await screen.findByRole("cell", { name: "Acme" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "Globex" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Applications (2)" })).toBeInTheDocument();
});

test("filters, searches, and explains empty results", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship, coop]);
  render(<App />);
  await screen.findByRole("cell", { name: "Acme" });
  expect(screen.getByRole("cell", { name: "Globex" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Search"), {
    target: { value: "platform" },
  });
  expect(screen.queryByRole("cell", { name: "Acme" })).not.toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "Globex" })).toBeInTheDocument();

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

test("shows a pending review count and opens the review queue", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship]);
  vi.mocked(listAllReviews).mockResolvedValue([{
    message_id: "message-1",
    reason: "ambiguous_application",
    evidence: "Interview invitation",
    received_at: null,
    thread_id: null,
    source_url: null,
    subject: null,
    sender: null,
    application_id: "app-1",
    proposed_status: "interviewing",
    proposed_season: null,
    proposed_year: null,
  }]);
  render(<App />);

  fireEvent.click(await screen.findByRole("tab", { name: "Needs a look (1)" }));
  expect(screen.getByRole("heading", { name: "Needs review" })).toBeInTheDocument();
  expect(screen.getByText("Interview invitation")).toBeInTheDocument();
});

test("reloads reviews when the review queue is opened", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship]);
  render(<App />);
  await screen.findByRole("tab", { name: "Needs a look (0)" });
  expect(listAllReviews).toHaveBeenCalledOnce();

  vi.mocked(listAllReviews).mockResolvedValue([{
    message_id: "message-2",
    reason: "ambiguous_application",
    evidence: "Offer discussion",
    received_at: null,
    thread_id: null,
    source_url: null,
    subject: null,
    sender: null,
    application_id: "app-1",
    proposed_status: "offer",
    proposed_season: null,
    proposed_year: null,
  }]);
  fireEvent.click(screen.getByRole("tab", { name: "Needs a look (0)" }));

  expect(await screen.findByText("Offer discussion")).toBeInTheDocument();
  expect(listAllReviews).toHaveBeenCalledTimes(2);
});

test("retries a failed review load from the sheet", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship]);
  vi.mocked(listAllReviews)
    .mockRejectedValueOnce(new ApiError(500, "Review service unavailable"))
    .mockRejectedValueOnce(new ApiError(500, "Review service unavailable"))
    .mockResolvedValueOnce([]);
  render(<App />);

  fireEvent.click(await screen.findByRole("tab", { name: "Needs a look (0)" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load reviews: Review service unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Retry reviews" }));
  expect(await screen.findByText("No emails need review.")).toBeInTheDocument();
  expect(listAllReviews).toHaveBeenCalledTimes(3);
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

  expect(await screen.findByRole("cell", { name: "Acme" })).toBeInTheDocument();
});

test("shows access denied without tracker controls for an unapproved account", async () => {
  vi.mocked(listAllApplications).mockRejectedValue(new ApiError(403, "Access denied"));
  render(<App email="tester@example.com" onSignOut={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Access denied" })).toBeInTheDocument();
  expect(screen.getByText(/ask the app administrator to approve your account/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New row" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
});

test("creates an application and opens its detail", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([]);
  vi.mocked(createApplication).mockResolvedValue(internship);
  vi.mocked(getApplication).mockResolvedValue(internship);
  vi.mocked(listApplicationEvents).mockResolvedValue([]);
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "New row" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Open details for Acme" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "Open details for Acme" }));
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

test("shows the danger zone only when signed in", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship]);
  const { unmount } = render(<App />);
  await screen.findByRole("cell", { name: "Acme" });
  expect(screen.queryByRole("heading", { name: "Danger zone" })).not.toBeInTheDocument();
  unmount();

  render(<App email="a@example.test" onSignOut={vi.fn()} onAccountDeleted={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  expect(screen.getByRole("heading", { name: "Danger zone" })).toBeInTheDocument();
});

test("hides the danger zone when access is denied", async () => {
  vi.mocked(listAllApplications).mockRejectedValue(new ApiError(403, "Access denied"));
  render(<App email="a@example.test" onSignOut={vi.fn()} />);
  await screen.findByRole("heading", { name: "Access denied" });
  expect(screen.queryByRole("heading", { name: "Danger zone" })).not.toBeInTheDocument();
});

test("keeps account deletion available when applications fail to load", async () => {
  vi.mocked(listAllApplications).mockRejectedValue(new ApiError(500, "Server error"));
  render(<App email="a@example.test" onSignOut={vi.fn()} onAccountDeleted={vi.fn()} />);
  await screen.findByText("Could not load applications: Server error");
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(screen.getByRole("heading", { name: "Danger zone" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Delete account" })).toBeInTheDocument();
});

test("a 423 from any call shows the deleting state, not access denied", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship]);
  vi.mocked(getAccountStatus).mockResolvedValue({ state: "deleting" });
  render(<App email="a@example.test" onSignOut={vi.fn()} onAccountDeleted={vi.fn()} />);
  await screen.findByRole("cell", { name: "Acme" });

  act(() => {
    window.dispatchEvent(new Event("account-deleting"));
  });

  expect(screen.getByText("Account deletion in progress")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Access denied" })).not.toBeInTheDocument();
  expect(screen.queryByRole("cell", { name: "Acme" })).not.toBeInTheDocument();
});

test("a 410 from any call finishes sign-out", async () => {
  const done = vi.fn();
  vi.mocked(listAllApplications).mockResolvedValue([internship]);
  render(<App email="a@example.test" onSignOut={vi.fn()} onAccountDeleted={done} />);
  await screen.findByRole("cell", { name: "Acme" });

  act(() => {
    window.dispatchEvent(new Event("account-deleted"));
  });

  expect(done).toHaveBeenCalledOnce();
});
