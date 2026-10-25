import { useState } from "react";

import { reconsiderAiChange, undoAiChange } from "../api/client";
import type { AiChange, Application } from "../api/types";
import { formatFieldValue, formatTimestamp, UNKNOWN } from "../applications/labels";
import { aiChangeError } from "./aiMarkers";

type Props = {
  changes: AiChange[];
  applications: Application[];
  loading: boolean;
  error: string | null;
  onChanged: () => Promise<void> | void;
};

function summary(change: AiChange, applications: Application[]): string {
  if (change.action === "ignored") {
    return `Ignored: ${change.reason.replace(/^not_application:|^filtered:/, "").replaceAll("_", " ")}`;
  }
  const company = applications.find((item) => item.application_id === change.application_id)?.company
    ?? change.changes.company?.after ?? UNKNOWN;
  if (change.action === "created") {
    const role = change.changes.role?.after
      ?? applications.find((item) => item.application_id === change.application_id)?.role ?? UNKNOWN;
    return `Added ${company} · ${role}`;
  }
  const [field, value] = change.changes.status
    ? ["status", change.changes.status] as const
    : Object.entries(change.changes)[0] ?? [];
  return `${company} → ${field ? formatFieldValue(field, value.after) : UNKNOWN}`;
}

export function AiChangesTab({ changes, applications, loading, error, onChanged }: Props) {
  const [visibleCount, setVisibleCount] = useState(50);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reconsidered, setReconsidered] = useState<string[]>([]);

  async function act(change: AiChange, action: "undo" | "reconsider") {
    setBusy(change.message_id);
    setActionError(null);
    try {
      if (action === "undo") await undoAiChange(change.message_id);
      else await reconsiderAiChange(change.message_id);
      if (action === "reconsider") setReconsidered((current) => [...current, change.message_id]);
      await onChanged();
    } catch (caught) {
      setActionError(aiChangeError(caught, change));
    } finally {
      setBusy(null);
    }
  }

  return <div className="ai-changes">
    {actionError && <p role="alert" className="error">{actionError}</p>}
    {error && <p role="alert" className="error">Could not load AI changes: {error}</p>}
    {changes.length === 0 && <p className="empty">{loading ? "Loading AI changes…" : "No AI changes yet."}</p>}
    <ul className="ai-change-list">{changes.slice(0, visibleCount).map((change) => (
      <li key={change.message_id}>
        <span>{summary(change, applications)}</span>
        <small>{formatTimestamp(change.received_at)}</small>
        {change.source_url && <a href={change.source_url} target="_blank" rel="noopener noreferrer">Open email</a>}
        {change.action !== "ignored" && (change.undone_at ? <span>Already undone.</span> : <button type="button" disabled={busy === change.message_id} onClick={() => void act(change, "undo")}>Undo</button>)}
        {change.action === "ignored" && !reconsidered.includes(change.message_id) && <button type="button" disabled={busy === change.message_id} onClick={() => void act(change, "reconsider")}>This was an application</button>}
      </li>
    ))}</ul>
    {visibleCount < changes.length && <button type="button" onClick={() => setVisibleCount((count) => count + 50)}>Show more</button>}
  </div>;
}
