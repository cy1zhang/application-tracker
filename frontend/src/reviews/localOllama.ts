import type { LocalAiInput } from "../api/types";

const BASE = "http://127.0.0.1:11434";

export class OllamaUnreachableError extends Error {
  constructor() {
    super("Could not reach Ollama on this computer.");
    this.name = "OllamaUnreachableError";
  }
}

export async function listLocalModels(signal?: AbortSignal): Promise<string[]> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/api/tags`, {
      signal, credentials: "omit", referrerPolicy: "no-referrer",
    });
  } catch {
    throw new OllamaUnreachableError();
  }
  if (!response.ok) throw new Error("Could not list local Ollama models.");
  const body: unknown = await response.json();
  if (!body || typeof body !== "object" || !("models" in body) || !Array.isArray(body.models)) {
    throw new Error("Ollama returned an invalid model list.");
  }
  return body.models.flatMap((item: unknown) =>
    item && typeof item === "object" && "name" in item && typeof item.name === "string"
      ? [item.name] : [],
  );
}

export async function runLocalModel(
  model: string, input: LocalAiInput, signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  let response: Response;
  try {
    response = await fetch(`${BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal,
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: input.system_prompt },
          { role: "user", content: input.user_prompt },
        ],
        format: input.format,
        stream: false,
        think: false,
        options: { temperature: 0, num_predict: 512 },
      }),
    });
  } catch {
    throw new OllamaUnreachableError();
  }
  if (!response.ok) throw new Error(`Ollama could not analyze this email (${response.status}).`);
  const body: unknown = await response.json();
  const content = body && typeof body === "object" && "message" in body &&
    body.message && typeof body.message === "object" && "content" in body.message
    ? body.message.content : null;
  if (typeof content !== "string" || content.length > 50_000) {
    throw new Error("Ollama returned an invalid response.");
  }
  try {
    const parsed: unknown = JSON.parse(content);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // Do not expose model output, which may repeat private email text.
  }
  throw new Error("Ollama returned invalid JSON.");
}
