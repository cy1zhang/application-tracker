import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  getApplication,
  listApplicationEvents,
  updateApplication,
} from "../api/client";
import type {
  Application,
  ApplicationEvent,
  ApplicationValues,
  EventOrigin,
  EventType,
} from "../api/types";
import { ApplicationForm, buildPatch } from "./ApplicationForm";
import { MergeForm } from "./MergeForm";
import {
  FIELD_LABELS,
  ROLE_TYPE_LABELS,
  STATUS_LABELS,
  UNKNOWN,
  formatCycle,
  formatFieldValue,
  formatTimestamp,
} from "./labels";

const EVENT_LABELS: Record<EventType, string> = {
  created: "Created",
  updated: "Updated",
  deleted: "Deleted",
  attached: "Attached email",
  merged: "Merged",
};

const ORIGIN_LABELS: Record<EventOrigin, string> = {
  user: "you",
  rules: "email rules",
  ai: "AI",
};

const FIELD_ORDER = Object.keys(FIELD_LABELS);

interface Loaded {
  application: Application;
  events: ApplicationEvent[];
}

interface ApplicationDetailProps {
  applicationId: string;
  onBack: () => void;
  otherApplications?: Application[];
  onMerged?: () => Promise<void>;
}

export function ApplicationDetail({ applicationId, onBack, otherApplications = [], onMerged }: ApplicationDetailProps) {
  const [data, setData] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [stale, setStale] = useState(false);
  const [merging, setMerging] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [application, events] = await Promise.all([
        getApplication(applicationId),
        listApplicationEvents(applicationId),
      ]);
      setData({ application, events });
    } catch (error) {
      setLoadError(
        error instanceof ApiError ? error.message : "Could not load the application.",
      );
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(values: ApplicationValues) {
    if (data === null) return;
    const patch = buildPatch(data.application, values);
    if (patch === null) {
      setEditing(false);
      return;
    }
    try {
      await updateApplication(data.application.application_id, patch);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setStale(true);
        return;
      }
      throw error;
    }
    setEditing(false);
    await load();
  }

  async function reloadLatest() {
    setStale(false);
    setEditing(false);
    await load();
  }

  return (
    <section className="page" aria-labelledby="detail-title">
      <button type="button" className="link" onClick={onBack}>
        ← Back to applications
      </button>
      {loadError !== null ? (
        <div role="alert" className="error">
          <p>{loadError}</p>
          <button type="button" onClick={() => void load()}>
            Retry
          </button>
        </div>
      ) : data === null ? (
        <p className="muted">Loading application…</p>
      ) : (
        <>
          <div className="page-header">
            <h1 id="detail-title">
              {data.application.company ?? UNKNOWN} · {data.application.role ?? UNKNOWN}
            </h1>
            {!editing && !merging && <div className="detail-actions">
              <button type="button" className="primary" onClick={() => setEditing(true)}>Edit</button>
              {otherApplications.length > 0 && <button type="button" onClick={() => setMerging(true)}>Merge duplicate</button>}
            </div>}
          </div>
          {stale && (
            <div role="alert" className="error">
              <p>
                This application changed since you opened it, so your edits were
                not saved.
              </p>
              <button type="button" onClick={() => void reloadLatest()}>
                Reload latest
              </button>
            </div>
          )}
          {merging ? (
            <MergeForm retained={data.application} candidates={otherApplications.filter((item) => item.application_id !== data.application.application_id)} onMerged={async () => { setMerging(false); await Promise.all([load(), onMerged?.()]); }} onReload={async () => { await Promise.all([load(), onMerged?.()]); }} onCancel={() => setMerging(false)} />
          ) : editing ? (
            <ApplicationForm
              initial={data.application}
              submitLabel="Save changes"
              onSubmit={save}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <ApplicationFields application={data.application} />
          )}
          <h2 id="history-title">History</h2>
          <ol className="history" aria-labelledby="history-title">
            {data.events.map((event) => (
              <HistoryEntry key={event.event_id} event={event} />
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function ApplicationFields({ application }: { application: Application }) {
  const rows: [string, string][] = [
    ["Company", application.company ?? UNKNOWN],
    ["Role", application.role ?? UNKNOWN],
    ["Role type", ROLE_TYPE_LABELS[application.role_type ?? "unknown"]],
    ["Cycle", formatCycle(application.season, application.year)],
    [
      "Status",
      application.status === null ? UNKNOWN : STATUS_LABELS[application.status],
    ],
    ["Application date", application.application_date ?? UNKNOWN],
    ["Last update", formatTimestamp(application.updated_at)],
  ];
  return (
    <dl className="fields">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function HistoryEntry({ event }: { event: ApplicationEvent }) {
  const changes = Object.entries(event.changes)
    .filter(([, change]) => change.before !== change.after)
    .sort(([a], [b]) => FIELD_ORDER.indexOf(a) - FIELD_ORDER.indexOf(b));
  return (
    <li>
      <p className="history-title">
        {EVENT_LABELS[event.event_type]} by {ORIGIN_LABELS[event.origin]}{" "}
        <time dateTime={event.occurred_at}>{formatTimestamp(event.occurred_at)}</time>
      </p>
      <ul>
        {changes.map(([field, change]) => {
          const label = FIELD_LABELS[field] ?? field;
          const after = formatFieldValue(field, change.after);
          return (
            <li key={field}>
              {event.event_type === "created"
                ? `${label}: ${after}`
                : `${label}: ${formatFieldValue(field, change.before)} → ${after}`}
            </li>
          );
        })}
      </ul>
      {event.thread_id ? (
        <a className="history-source" href={`https://mail.google.com/mail/u/0/#all/${encodeURIComponent(event.thread_id)}`} target="_blank" rel="noopener noreferrer">Open source email</a>
      ) : event.source_message_id ? <p className="history-source">Linked to email</p> : null}
      {event.merged_application_id && <p className="history-source">Includes the duplicate application’s history.</p>}
    </li>
  );
}
