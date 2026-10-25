import { useEffect, useRef, useState } from "react";

import {
  ApiError, getAiQueueInput, getLocalAiInput, getSettings, listAiQueue,
  submitAiQueueResult, submitLocalAiSuggestion,
} from "../api/client";
import type { AutoApplyMode, ReviewItem } from "../api/types";
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
  if (caught.status === 409 && caught.message === "auto_apply_off") {
    return "The AI queue is off. Refresh the page before starting again.";
  }
  if (caught.status === 503 && caught.message === "queue_unavailable") {
    return "The AI queue is unavailable. Wait a moment, then start again.";
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
  const [mode, setMode] = useState<AutoApplyMode | null>(null);
  const [running, setRunning] = useState(false);
  const [counts, setCounts] = useState<{ suggested: number; planned: number; executed: number; skipped: number; failed: number } | null>(null);
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
      if (mounted) {
        setHostedAiOn(settings.enabled);
        setMode(settings.auto_apply_mode ?? "off");
      }
    }).catch(() => { if (mounted) setRunError("Could not load AI mode. Reload the page before starting analysis."); });
    return () => {
      mounted = false;
      controller.current?.abort();
    };
  }, []);

  async function start() {
    if (!model || mode === null || controller.current) return;
    const reviewCandidates = reviews.filter(
      (item) => ELIGIBLE_REASONS.has(item.reason) && !item.ai_proposal && !failedIds.has(item.message_id),
    );
    const wanted = Math.min(Math.max(Math.floor(Number(limit)) || 1, 1), mode === "off" ? Math.max(eligibleCount, 1) : 100);
    const result = { suggested: 0, planned: 0, executed: 0, skipped: mode === "off" ? reviews.length - reviewCandidates.length : 0, failed: 0 };
    const abort = new AbortController();
    controller.current = abort;
    setCounts({ ...result });
    setCurrent(0);
    setTotal(0);
    setRunError(null);
    setRunning(true);
    let pending: { message_id: string }[];
    if (mode === "off") {
      pending = reviewCandidates.slice(0, wanted);
    } else {
      try {
        const page = await listAiQueue(Math.min(100, wanted + failedIds.size), abort.signal);
        const available = page.items.filter((item) => !failedIds.has(item.message_id));
        pending = available.slice(0, wanted);
        result.skipped = page.items.length - available.length;
      } catch (caught) {
        controller.current = null;
        setRunning(false);
        if (!abort.signal.aborted) setRunError(globalStopMessage(caught) ?? "Could not load the AI queue. Try again.");
        return;
      }
    }
    setTotal(pending.length);
    for (const [index, item] of pending.entries()) {
      if (abort.signal.aborted) break;
      setCurrent(index + 1);
      try {
        const input = mode === "off"
          ? await getLocalAiInput(item.message_id, abort.signal)
          : await getAiQueueInput(item.message_id, abort.signal);
        const extraction = await runLocalModel(model, input, abort.signal);
        const payload = { generation: input.generation, model, extraction };
        if (mode === "off") {
          const saved = await submitLocalAiSuggestion(item.message_id, payload, abort.signal);
          if (saved.saved && saved.review) {
            onSuggested(saved.review);
            result.suggested++;
          } else {
            result.skipped++;
          }
        } else {
          const saved = await submitAiQueueResult(item.message_id, payload, abort.signal);
          if (saved.executed) result.executed++;
          else if (saved.planned) result.planned++;
          else result.skipped++;
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
      <p>Email text is sent to Ollama on this computer while this tab is open. {mode === "plan" ? "Results are saved as a plan for your review." : mode === "on" ? "Results may update applications automatically." : "Suggestions still need your review."}</p>
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
      <label>{mode === "off" || mode === null ? "Reviews" : "Emails"} to analyze <input
        type="number" min={1} max={mode === "off" || mode === null ? Math.max(eligibleCount, 1) : 100} value={limit}
        onChange={(event) => setLimit(event.target.value)} disabled={running}
      /></label>
      <div className="review-actions">
        <button type="button" disabled={!model || mode === null || running} onClick={() => void start()}>Start analysis</button>
        {running && <button type="button" onClick={() => controller.current?.abort()}>Stop</button>}
      </div>
      {running && <p role="status">Analyzing {current} of {total} {mode === "off" ? "reviews" : "emails"}…</p>}
      {counts && <p role="status">{mode === "off" ? `${counts.suggested} suggested` : mode === "plan" ? `${counts.planned} planned` : `${counts.executed} executed, ${counts.planned} planned`}, {counts.skipped} skipped, {counts.failed} failed</p>}
      {counts && <p className="muted">{mode === "off" ? "Skipped includes reviews outside the AI reasons, reviews that already have a suggestion, and reviews that failed earlier in this session. Failed reviews are not retried until you reload this page." : "Skipped includes queued emails that failed earlier in this session or were no longer available. Failed emails are not retried until you reload this page."}</p>}
      {runError && <p role="alert" className="review-error">{runError}</p>}
    </div>
  );
}
