import { useEffect, useRef, useState } from "react";

import { ApiError, getApplication, resolveReview } from "../api/client";
import type {
  AiProposal,
  Application,
  ApplicationValues,
  ReviewApplicationCreate,
  ReviewItem,
  ReviewResolution,
} from "../api/types";
import { ApplicationForm, buildPatch } from "../applications/ApplicationForm";
import { STATUS_LABELS, formatCycle, formatTimestamp } from "../applications/labels";
import { LocalOllamaPanel } from "./LocalOllamaPanel";

const REASONS: Record<string, string> = {
  ambiguous_application: "The email could match more than one application.",
  unknown_stage: "The email does not establish an application stage.",
  conflicting_stages: "The email mentions different application stages.",
  older_evidence: "A newer change may already cover this email.",
  status_regression: "The proposed stage conflicts with the current status.",
  cycle_conflict: "The proposed hiring cycle conflicts with this application.",
  forwarded_message: "This message forwards an older email.",
  conflicting_roles: "The email mentions more than one role.",
  subject_only_event: "The subject suggests an update, but the message does not confirm it.",
  conflicting_requisitions: "The email mentions different job IDs.",
  nonaffirmative_event: "The wording does not confirm this application update.",
  unparseable_message: "The email could not be read automatically. Open it to review the details.",
  missing_application: "The linked application is unavailable or was merged.",
};

const AI_NOTES: Record<string, string> = {
  ai_unavailable: "AI was not used for this email.",
  ai_budget_exhausted: "AI was not used: daily limit reached.",
  ai_key_invalid: "AI was not used: your API key was rejected.",
};

const AI_FIELD_LABELS: [Exclude<keyof AiProposal, "evidence">, string][] = [
  ["company", "Company"],
  ["role", "Role"],
  ["employment_type", "Type"],
  ["season", "Season"],
  ["year", "Year"],
  ["status", "Status"],
  ["requisition_id", "Job ID"],
];

type ActiveAction = { messageId: string; kind: "edit" | "attach" | "create" };

interface ReviewQueueProps {
  reviews: ReviewItem[];
  applications: Application[];
  onResolved: () => Promise<void>;
  onBack: () => void;
}

export function ReviewQueue({ reviews, applications, onResolved, onBack }: ReviewQueueProps) {
  const [visibleCount, setVisibleCount] = useState(20);
  const [active, setActive] = useState<ActiveAction | null>(null);
  const [attachId, setAttachId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [linkedApplications, setLinkedApplications] = useState<Record<string, Application>>({});
  const [showLocalAi, setShowLocalAi] = useState(false);
  const localFailedIds = useRef(new Set<string>());
  const [localSuggestions, setLocalSuggestions] = useState<Record<string, ReviewItem>>({});
  const displayedReviews = reviews.map((review) => localSuggestions[review.message_id] ?? review);

  useEffect(() => { setLocalSuggestions({}); }, [reviews]);

  useEffect(() => {
    const missingIds = [...new Set(reviews.map((review) => review.application_id).filter(
      (id): id is string => id !== null && !applications.some((item) => item.application_id === id),
    ))];
    let current = true;
    void Promise.all(missingIds.map(async (id) => {
      try {
        return [id, await getApplication(id)] as const;
      } catch {
        return null;
      }
    })).then((resolved) => {
      if (current) setLinkedApplications(Object.fromEntries(resolved.filter((item) => item !== null)));
    });
    return () => { current = false; };
  }, [reviews, applications]);

  async function resolve(messageId: string, payload: ReviewResolution) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await resolveReview(messageId, payload);
      setActive(null);
      await onResolved();
      setNotice("Review saved.");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        await onResolved();
        setActive(null);
        setError("The review or application changed. The latest data is loaded.");
      } else {
        setError(caught instanceof ApiError ? caught.message : "Could not save review.");
      }
    } finally {
      setBusy(false);
    }
  }

  function start(messageId: string, kind: ActiveAction["kind"]) {
    setActive({ messageId, kind });
    setAttachId("");
    setError(null);
    setNotice(null);
  }

  async function edit(review: ReviewItem, application: Application, values: ApplicationValues) {
    const patch = buildPatch(application, values);
    if (patch === null) {
      setError("Change at least one field before saving.");
      return;
    }
    await resolve(review.message_id, {
      action: "edit",
      application_id: application.application_id,
      patch,
    });
  }

  async function create(review: ReviewItem, values: ApplicationValues) {
    const application = values as ReviewApplicationCreate;
    await resolve(review.message_id, { action: "create", application });
  }

  return (
    <section className="page" aria-labelledby="reviews-title">
      <button type="button" className="link" onClick={onBack}>← Back to applications</button>
      <div className="page-header">
        <h1 id="reviews-title">Needs review</h1>
        <span className="review-count">{reviews.length} pending</span>
      </div>
      {reviews.length > 0 && <button type="button" onClick={() => setShowLocalAi((shown) => !shown)}>Analyze with Ollama</button>}
      {showLocalAi && <LocalOllamaPanel reviews={displayedReviews} failedIds={localFailedIds.current} onSuggested={(review) => setLocalSuggestions((current) => ({ ...current, [review.message_id]: review }))} onFinished={onResolved} />}
      {error && <p role="alert" className="review-error">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      {reviews.length === 0 && <p className="empty">No emails need review.</p>}
      <div className="review-list">
        {displayedReviews.slice(0, visibleCount).map((review) => {
          const application = applications.find(
            (item) => item.application_id === review.application_id,
          ) ?? linkedApplications[review.application_id ?? ""];
          const selected = applications.find((item) => item.application_id === attachId);
          const action = active?.messageId === review.message_id ? active.kind : null;
          return (
            <article className="review-item" key={review.message_id}>
              <div className="review-heading">
                <h2>{review.subject || (application ? `${application.company} · ${application.role}` : "Unmatched email")}</h2>
                {review.received_at && <time dateTime={review.received_at}>{formatTimestamp(review.received_at)}</time>}
              </div>
              {review.sender && <p className="review-sender">From: {review.sender}</p>}
              {application && review.subject && <p className="review-application">Application: {application.company} · {application.role}</p>}
              <p>{REASONS[review.reason === "ai_proposal" ? review.rule_reason ?? "" : review.reason] ?? "This email needs your decision."}</p>
              {review.ai_note && AI_NOTES[review.ai_note] && <p className="muted">{AI_NOTES[review.ai_note]}</p>}
              {review.evidence && <blockquote>{review.evidence}</blockquote>}
              {review.ai_proposal && (
                <div className="ai-suggestion">
                  <h3>AI suggestion</h3>
                  <ul>
                    {AI_FIELD_LABELS.filter(([field]) => review.ai_proposal?.[field] != null).map(([field, label]) => (
                      <li key={field}>
                        {label}: {String(review.ai_proposal?.[field])}
                        {review.ai_proposal?.evidence[field] && <blockquote>{review.ai_proposal.evidence[field]}</blockquote>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {(review.proposed_status || review.proposed_season || review.proposed_year) && (
                <p className="review-proposal">
                  Proposed: {review.proposed_status ? STATUS_LABELS[review.proposed_status] : "Stage unknown"}
                  {(review.proposed_season || review.proposed_year) &&
                    ` · ${formatCycle(review.proposed_season, review.proposed_year)}`}
                </p>
              )}
              {review.source_url && (
                <a href={review.source_url} target="_blank" rel="noopener noreferrer">Open email</a>
              )}
              <div className="review-actions">
                {application && (review.proposed_status || review.proposed_season || review.proposed_year) && (
                  <button type="button" disabled={busy} onClick={() => void resolve(review.message_id, { action: "accept", expected_version: application.version, expected_review_version: review.review_version ?? 0 })}>Accept proposal</button>
                )}
                {application && <button type="button" disabled={busy} onClick={() => start(review.message_id, "edit")}>Edit and apply</button>}
                <button type="button" disabled={busy} onClick={() => start(review.message_id, "attach")}>Attach to application</button>
                <button type="button" disabled={busy} onClick={() => start(review.message_id, "create")}>Create application</button>
                <button type="button" disabled={busy} onClick={() => void resolve(review.message_id, { action: "reject" })}>Reject proposal</button>
              </div>
              {action === "edit" && application && (
                <ApplicationForm initial={application} submitLabel="Save review changes" onSubmit={(values) => edit(review, application, values)} onCancel={() => setActive(null)} />
              )}
              {action === "create" && (
                <ApplicationForm allowUnknownStatus submitLabel="Create from email" onSubmit={(values) => create(review, values)} onCancel={() => setActive(null)} />
              )}
              {action === "attach" && (
                <div className="review-attach">
                  <label htmlFor={`attach-${review.message_id}`}>Application</label>
                  <select id={`attach-${review.message_id}`} value={attachId} onChange={(event) => setAttachId(event.target.value)}>
                    <option value="">Select an application</option>
                    {applications.map((item) => (
                      <option key={item.application_id} value={item.application_id}>{item.company} · {item.role}</option>
                    ))}
                  </select>
                  <button type="button" disabled={!selected || busy} onClick={() => selected && void resolve(review.message_id, { action: "attach", application_id: selected.application_id, expected_version: selected.version })}>Attach email</button>
                  <button type="button" onClick={() => setActive(null)}>Cancel</button>
                </div>
              )}
            </article>
          );
        })}
      </div>
      {visibleCount < reviews.length && (
        <button type="button" onClick={() => setVisibleCount((count) => count + 20)}>Show more reviews</button>
      )}
    </section>
  );
}
