import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ApiError, deleteAiSettings, getSettings, saveAiSettings } from "../api/client";
import type { AiSettings as AiSettingsData } from "../api/types";
import { AiSettings } from "./AiSettings";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getSettings: vi.fn(), saveAiSettings: vi.fn(), deleteAiSettings: vi.fn() };
});

afterEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});

const unconfigured: AiSettingsData = {
  available_providers: ["openai", "gemini"],
  hugging_face_host: null,
  provider: null,
  key_hint: null,
  enabled: false,
  key_status: null,
  requests_today: 0,
  daily_limit: 50,
  usage_day: "2026-10-20",
};
const configured: AiSettingsData = {
  ...unconfigured,
  provider: "openai",
  key_hint: "1234",
  enabled: true,
  key_status: "ok",
  requests_today: 12,
};
const SECRET = "sk-test-0123456789abcdef";

test("explains that AI is unavailable when no provider is offered", async () => {
  vi.mocked(getSettings).mockResolvedValue({ ...unconfigured, available_providers: [] });
  render(<AiSettings />);

  expect(await screen.findByText(/AI assistance is not available/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Provider")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
});

test("uses a password field and gates Save on consent and a key", async () => {
  vi.mocked(getSettings).mockResolvedValue(unconfigured);
  render(<AiSettings />);

  const key = await screen.findByLabelText("API key");
  expect(key).toHaveAttribute("type", "password");
  expect(key).toHaveAttribute("autocomplete", "off");
  const options = screen.getAllByRole("option").map((option) => option.getAttribute("value"));
  expect(options).toEqual(["openai", "gemini"]);
  const consent = screen.getByLabelText(
    "Email text from candidate messages is sent to your chosen provider and billed by them",
  );
  expect(consent).not.toBeChecked();
  const save = screen.getByRole("button", { name: "Save key" });
  expect(save).toBeDisabled();
  fireEvent.change(key, { target: { value: SECRET } });
  expect(save).toBeDisabled();
  fireEvent.click(consent);
  expect(save).toBeEnabled();
  fireEvent.click(consent);
  expect(save).toBeDisabled();
});

test("explains Hugging Face routing before saving a key", async () => {
  vi.mocked(getSettings).mockResolvedValue({
    ...unconfigured,
    available_providers: ["huggingface"],
    hugging_face_host: "deepinfra",
  } as AiSettingsData);
  render(<AiSettings />);

  expect(await screen.findByRole("option", { name: "Hugging Face" })).toBeInTheDocument();
  expect(screen.getByText(/Hugging Face forwards candidate email text to deepinfra/)).toBeInTheDocument();
});

test("saves the key then clears it from the form", async () => {
  vi.mocked(getSettings).mockResolvedValue(unconfigured);
  vi.mocked(saveAiSettings).mockResolvedValue(configured);
  render(<AiSettings />);

  fireEvent.change(await screen.findByLabelText("API key"), { target: { value: SECRET } });
  fireEvent.click(screen.getByLabelText(/Email text from candidate messages/));
  fireEvent.click(screen.getByRole("button", { name: "Save key" }));

  await waitFor(() =>
    expect(saveAiSettings).toHaveBeenCalledWith({
      provider: "openai",
      api_key: SECRET,
      consent: true,
      enabled: true,
    }),
  );
  expect(await screen.findByText(/ends in 1234/)).toBeInTheDocument();
  expect(screen.queryByDisplayValue(SECRET)).not.toBeInTheDocument();
  expect(document.body.innerHTML).not.toContain(SECRET);
});

test("clears the key and does not echo it when the save fails", async () => {
  vi.mocked(getSettings).mockResolvedValue(unconfigured);
  vi.mocked(saveAiSettings).mockRejectedValue(new ApiError(422, "Provider is not available"));
  render(<AiSettings />);

  fireEvent.change(await screen.findByLabelText("API key"), { target: { value: SECRET } });
  fireEvent.click(screen.getByLabelText(/Email text from candidate messages/));
  fireEvent.click(screen.getByRole("button", { name: "Save key" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Provider is not available");
  expect(screen.getByLabelText("API key")).toHaveValue("");
  expect(document.body.innerHTML).not.toContain(SECRET);
});

test("shows hint, status and usage, and never stores the key", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem");
  vi.mocked(getSettings).mockResolvedValue(configured);
  render(<AiSettings />);

  expect(await screen.findByText(/ends in 1234/)).toBeInTheDocument();
  expect(screen.getByText(/Key status: working/)).toBeInTheDocument();
  expect(
    screen.getByText(/12 of 50 AI requests used today \(UTC\)\. This is a request cap, not a monetary cap; your provider bills you separately\./),
  ).toBeInTheDocument();
  expect(setItem).not.toHaveBeenCalled();
  setItem.mockRestore();
});

test("shows an actionable banner when the key was rejected", async () => {
  vi.mocked(getSettings).mockResolvedValue({ ...configured, key_status: "invalid" });
  render(<AiSettings />);

  expect(await screen.findByRole("alert")).toHaveTextContent("Key rejected, replace it");
  fireEvent.click(screen.getByRole("button", { name: "Replace key" }));
  expect(screen.getByLabelText("API key")).toBeInTheDocument();
});

test("toggles AI without asking for the key again", async () => {
  vi.mocked(getSettings).mockResolvedValue(configured);
  vi.mocked(saveAiSettings).mockResolvedValue({ ...configured, enabled: false });
  render(<AiSettings />);

  fireEvent.click(await screen.findByRole("button", { name: "Disable AI" }));

  await waitFor(() =>
    expect(saveAiSettings).toHaveBeenCalledWith({ provider: "openai", enabled: false }),
  );
  expect(await screen.findByRole("button", { name: "Enable AI" })).toBeInTheDocument();
});

test("replacing the key asks for confirmation first", async () => {
  vi.mocked(getSettings).mockResolvedValue(configured);
  vi.mocked(saveAiSettings).mockResolvedValue(configured);
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
  vi.stubGlobal("confirm", confirm);
  render(<AiSettings />);

  fireEvent.click(await screen.findByRole("button", { name: "Replace key" }));
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: SECRET } });
  fireEvent.click(screen.getByLabelText(/Email text from candidate messages/));
  fireEvent.click(screen.getByRole("button", { name: "Save key" }));
  expect(saveAiSettings).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Save key" }));
  await waitFor(() =>
    expect(saveAiSettings).toHaveBeenCalledWith({
      provider: "openai",
      api_key: SECRET,
      consent: true,
      enabled: true,
    }),
  );
});

test("deleting the key asks for confirmation", async () => {
  vi.mocked(getSettings).mockResolvedValueOnce(configured).mockResolvedValueOnce(unconfigured);
  vi.mocked(deleteAiSettings).mockResolvedValue(undefined);
  vi.stubGlobal("confirm", vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true));
  render(<AiSettings />);

  fireEvent.click(await screen.findByRole("button", { name: "Delete key" }));
  expect(deleteAiSettings).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Delete key" }));

  await waitFor(() => expect(deleteAiSettings).toHaveBeenCalledTimes(1));
  expect(await screen.findByLabelText("API key")).toBeInTheDocument();
});

test("shows a load error with a retry", async () => {
  vi.mocked(getSettings)
    .mockRejectedValueOnce(new ApiError(500, "boom"))
    .mockResolvedValueOnce(unconfigured);
  render(<AiSettings />);

  expect(await screen.findByRole("alert")).toHaveTextContent("Could not load AI settings.");
  fireEvent.click(screen.getByRole("button", { name: "Retry AI settings" }));
  expect(await screen.findByLabelText("API key")).toBeInTheDocument();
});
