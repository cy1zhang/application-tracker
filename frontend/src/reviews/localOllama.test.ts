import { afterEach, expect, test, vi } from "vitest";

import type { LocalAiInput } from "../api/types";
import { listLocalModels, runLocalModel } from "./localOllama";

afterEach(() => vi.unstubAllGlobals());

const input: LocalAiInput = {
  system_prompt: "Synthetic instructions",
  user_prompt: "Synthetic email",
  format: { type: "object", properties: { status: { type: "string" } } },
  generation: 1,
};

test("lists only locally installed model names", async () => {
  const fetcher = vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ models: [{ name: "gemma4:latest" }, { name: "qwen:small" }] }),
  });
  vi.stubGlobal("fetch", fetcher);

  expect(await listLocalModels()).toEqual(["gemma4:latest", "qwen:small"]);
  expect(fetcher.mock.calls[0][0]).toBe("http://127.0.0.1:11434/api/tags");
});

test("sends only the prepared prompt to local Ollama and parses JSON", async () => {
  const fetcher = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ message: { content: '{"status":"interviewing"}' } }),
  });
  vi.stubGlobal("fetch", fetcher);

  expect(await runLocalModel("gemma4:latest", input)).toEqual({ status: "interviewing" });
  const [url, options] = fetcher.mock.calls[0];
  assertUrlAndBody(url, options);
});

function assertUrlAndBody(url: string, options: RequestInit) {
  expect(url).toBe("http://127.0.0.1:11434/api/chat");
  const body = JSON.parse(options.body as string);
  expect(body.model).toBe("gemma4:latest");
  expect(body.messages).toEqual([
    { role: "system", content: "Synthetic instructions" },
    { role: "user", content: "Synthetic email" },
  ]);
  expect(body.format).toEqual(input.format);
  expect(body.stream).toBe(false);
  expect(JSON.stringify(body)).not.toContain("generation");
}

test("rejects malformed model output before posting it to the app", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ message: { content: "not JSON" } }),
  }));
  await expect(runLocalModel("gemma4:latest", input)).rejects.toThrow("invalid JSON");
});
