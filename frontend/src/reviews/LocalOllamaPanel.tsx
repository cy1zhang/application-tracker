import { useEffect, useRef, useState } from "react";

import { ApiError, getLocalAiInput, getSettings, submitLocalAiSuggestion } from "../api/client";
import type { ReviewItem } from "../api/types";
import { OllamaUnreachableError, listLocalModels, runLocalModel } from "./localOllama";

const ELIGIBLE_REASONS = new Set([
  "ambiguous_application", "unknown_stage", "subject_only_event",
  "nonaffirmative_event", "conflicting_roles",
]);

const GLOBAL_STATUSES = new Set([0, 401, 410, 423]);

function globalStopMessage(caught: unknown): string | null {
  if (caught instanceof OllamaUnreachableError) {
    return "Could not reach Ollama on this computer. Start Ollama, then start again.";
  }
  if (!(caught instanceof ApiError)) return null;
  if (caught.status === 503 && caught.message === "gmail_connection_unavailable") {
    return "Gmail is unavailable. Check the Gmail connection, then retry.";
  }
  if (caught.status === 409 && caught.message === "gmail_connection_changed") {
    return "The Gmail connection changed. Refreshing the review queue; start again for remaining items.";
  }
  if (caught.status === 401) return "Sign in again, then start the analysis again.";
  if (GLOBAL_STATUSES.has(caught.status)) return "The app could not be reached. Check the connection, then retry.";
  return null;
}

interface Props {
  reviews: ReviewItem[];
  onSuggested: (review: ReviewItem) => void;
  onFinished: () => Promise<void>;
}

export function LocalOllamaPanel({ reviews, onSuggested, onFinished }: Props) {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [connectionError, setConnectionError] = useState(false);
  const [hostedAiOn, setHostedAiOn] = useState(false);
  const [running, setRunning] = useState(false);
  const [counts, setCounts] = useState<{ suggested: number; skipped: number; failed: number } | null>(null);
  const [current, setCurrent] = useState(0);
  const [runError, setRunError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const failedIds = useRef(new Set<string>());
  const eligibleCount = reviews.filter((item) => ELIGIBLE_REASONS.has(item.reason) && !item.ai_proposal).length;

  useEffect(() => {
    let mounted = true;
    void listLocalModels().then((names) => {
      if (!mounted) return;
      setModels(names);
      setModel(names[0] ?? "");
      setConnectionError(names.length === 0);
    }).catch(() => { if (mounted) setConnectionError(true); });
    void getSettings().then((settings) => {
      if (mounted) setHostedAiOn(settings.enabled);
    }).catch(() => { /* Local analysis does not require hosted AI settings. */ });
    return () => {
      mounted = false;
      controller.current?.abort();
    };
  }, []);

  async function start() {
    if (!model || controller.current) return;
    const pending = reviews.filter(
      (item) => ELIGIBLE_REASONS.has(item.reason) && !item.ai_proposal && !failedIds.current.has(item.message_id),
    );
    const result = { suggested: 0, skipped: reviews.length - pending.length, failed: 0 };
    const abort = new AbortController();
    controller.current = abort;
    setCounts({ ...result });
    setCurrent(0);
    setRunError(null);
    setRunning(true);
    for (const [index, item] of pending.entries()) {
      if (abort.signal.aborted) break;
      setCurrent(index + 1);
      try {
        const input = await getLocalAiInput(item.message_id, abort.signal);
        const extraction = await runLocalModel(model, input, abort.signal);
        const saved = await submitLocalAiSuggestion(item.message_id, {
          generation: input.generation, model, extraction,
        }, abort.signal);
        if (saved.saved && saved.review) {
          onSuggested(saved.review);
          result.suggested++;
        } else {
          result.skipped++;
        }
      } catch (caught) {
        if (abort.signal.aborted) break;
        const stop = globalStopMessage(caught);
        if (stop) {
          result.failed++;
          setRunError(stop);
          setCounts({ ...result });
          break;
        }
        if (caught instanceof ApiError && caught.status === 404) {
          result.skipped++;
        } else {
          result.failed++;
          failedIds.current.add(item.message_id);
        }
      }
      setCounts({ ...result });
    }
    controller.current = null;
    setRunning(false);
    try {
      await onFinished();
    } catch {
      setRunError("Could not refresh reviews. Reload the page before deciding on a suggestion.");
    }
  }

  return (
    <div className="local-ollama" aria-label="Local Ollama analysis">
      <h2>Local Ollama suggestions</h2>
      <p>Email text is sent to Ollama on this computer while this tab is open. Suggestions still need your review.</p>
      {hostedAiOn && <p className="review-error">Hosted AI is on. Scheduled Gmail sync may still send email to that provider. Turn it off in AI settings for local-only analysis.</p>}
      {connectionError ? (
        <p role="alert" aria-label="Ollama connection" className="review-error">
          Could not connect to Ollama. Start Ollama, then allow this app origin in <code>OLLAMA_ORIGINS</code>: <code>{window.location.origin}</code>. Restart Ollama and reopen this panel. Keep Ollama bound to 127.0.0.1.
        </p>
      ) : (
        <label>Installed model <select value={model} onChange={(event) => setModel(event.target.value)} disabled={running}>
          {models.map((name) => <option value={name} key={name}>{name}</option>)}
        </select></label>
      )}
      <div className="review-actions">
        <button type="button" disabled={!model || running} onClick={() => void start()}>Start analysis</button>
        {running && <button type="button" onClick={() => controller.current?.abort()}>Stop</button>}
      </div>
      {running && <p role="status">Analyzing {current} of {eligibleCount} eligible reviews…</p>}
      {counts && <p role="status">{counts.suggested} suggested, {counts.skipped} skipped, {counts.failed} failed</p>}
      {counts && <p className="muted">Skipped includes reviews outside the AI reasons, reviews that already have a suggestion, and reviews that failed earlier in this session. Failed reviews are not retried until you reload this page.</p>}
      {runError && <p role="alert" className="review-error">{runError}</p>}
    </div>
  );
}
