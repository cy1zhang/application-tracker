import { useEffect, useRef, useState } from "react";

import { ApiError, getLocalAiInput, getSettings, submitLocalAiSuggestion } from "../api/client";
import type { ReviewItem } from "../api/types";
import { OllamaUnreachableError, listLocalModels, runLocalModel } from "./localOllama";

const ELIGIBLE_REASONS = new Set([
  "ambiguous_application", "unknown_stage", "subject_only_event",
  "nonaffirmative_event", "conflicting_roles",
]);

const GLOBAL_STATUSES = new Set([0, 401, 410, 423]);
const BUSY_STATUSES = new Set([429, 502]);

// Only failures about this one review are remembered; API outages are not.
function isReviewSpecificFailure(caught: unknown): boolean {
  if (!(caught instanceof ApiError)) return true;
  return caught.status === 422
    || (caught.status === 409 && caught.message === "review_changed")
    || (caught.status === 503 && caught.message === "message_unavailable");
}

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
  if (BUSY_STATUSES.has(caught.status)) return "The app is busy or unreachable. Wait a moment, then start again.";
  if (GLOBAL_STATUSES.has(caught.status)) return "The app could not be reached. Check the connection, then retry.";
  return null;
}

interface Props {
  reviews: ReviewItem[];
  failedIds: Set<string>;
  onSuggested: (review: ReviewItem) => void;
  onFinished: () => Promise<void>;
}

export function LocalOllamaPanel({ reviews, failedIds, onSuggested, onFinished }: Props) {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [connectionError, setConnectionError] = useState(false);
  const [hostedAiOn, setHostedAiOn] = useState(false);
  const [running, setRunning] = useState(false);
  const [counts, setCounts] = useState<{ suggested: number; skipped: number; failed: number } | null>(null);
  const [current, setCurrent] = useState(0);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState("5");
  const [runError, setRunError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
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
    const candidates = reviews.filter(
      (item) => ELIGIBLE_REASONS.has(item.reason) && !item.ai_proposal && !failedIds.has(item.message_id),
    );
    const wanted = Math.min(Math.max(Math.floor(Number(limit)) || 1, 1), Math.max(eligibleCount, 1));
    const pending = candidates.slice(0, wanted);
    const result = { suggested: 0, skipped: reviews.length - candidates.length, failed: 0 };
    const abort = new AbortController();
    controller.current = abort;
    setCounts({ ...result });
    setCurrent(0);
    setTotal(pending.length);
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
          if (isReviewSpecificFailure(caught)) failedIds.add(item.message_id);
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
      <label>Reviews to analyze <input
        type="number" min={1} max={Math.max(eligibleCount, 1)} value={limit}
        onChange={(event) => setLimit(event.target.value)} disabled={running}
      /></label>
      <div className="review-actions">
        <button type="button" disabled={!model || running} onClick={() => void start()}>Start analysis</button>
        {running && <button type="button" onClick={() => controller.current?.abort()}>Stop</button>}
      </div>
      {running && <p role="status">Analyzing {current} of {total} reviews…</p>}
      {counts && <p role="status">{counts.suggested} suggested, {counts.skipped} skipped, {counts.failed} failed</p>}
      {counts && <p className="muted">Skipped includes reviews outside the AI reasons, reviews that already have a suggestion, and reviews that failed earlier in this session. Failed reviews are not retried until you reload this page.</p>}
      {runError && <p role="alert" className="review-error">{runError}</p>}
    </div>
  );
}
