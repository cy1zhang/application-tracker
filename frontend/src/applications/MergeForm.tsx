import { useState, type FormEvent } from "react";

import { ApiError, mergeApplication } from "../api/client";
import type { Application, MergeRequest } from "../api/types";
import { FIELD_LABELS, formatFieldValue } from "./labels";

const FIELDS: (keyof Pick<Application, "company" | "role" | "role_type" | "season" | "year" | "status" | "application_date">)[] = [
  "company", "role", "role_type", "season", "year", "status", "application_date",
];

interface MergeFormProps {
  retained: Application;
  candidates: Application[];
  onMerged: () => Promise<void>;
  onReload?: () => Promise<void>;
  onCancel: () => void;
}

export function MergeForm({ retained, candidates, onMerged, onReload, onCancel }: MergeFormProps) {
  const [duplicateId, setDuplicateId] = useState("");
  const [resolutions, setResolutions] = useState<Record<string, "keep" | "duplicate">>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const duplicate = candidates.find((candidate) => candidate.application_id === duplicateId);
  const conflicts = duplicate
    ? FIELDS.filter((field) => retained[field] !== null && duplicate[field] !== null && retained[field] !== duplicate[field])
    : [];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!duplicate) {
      setError("Choose a duplicate application.");
      return;
    }
    const missing = conflicts.find((field) => !resolutions[field]);
    if (missing) {
      setError(`Choose a value for ${FIELD_LABELS[missing]}.`);
      return;
    }
    setError(null);
    setBusy(true);
    const payload: MergeRequest = {
      duplicate_id: duplicate.application_id,
      expected_keep_version: retained.version,
      expected_duplicate_version: duplicate.version,
      resolutions: Object.fromEntries(conflicts.map((field) => [field, resolutions[field]])),
    };
    try {
      await mergeApplication(retained.application_id, payload);
      await onMerged();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setResolutions({});
        await onReload?.();
      }
      setError(caught instanceof ApiError && caught.status === 409
        ? "One application changed. Latest values are loaded; review the merge choices again."
        : caught instanceof ApiError ? caught.message : "Could not merge applications.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="merge-form" onSubmit={(event) => void submit(event)}>
      <h2>Merge duplicate</h2>
      <p>The selected application will be folded into this one. Both histories remain available here.</p>
      <label htmlFor="duplicate-application">Duplicate application</label>
      <select id="duplicate-application" value={duplicateId} onChange={(event) => {
        setDuplicateId(event.target.value);
        setResolutions({});
        setError(null);
      }}>
        <option value="">Select an application</option>
        {candidates.map((candidate) => (
          <option key={candidate.application_id} value={candidate.application_id}>{candidate.company} · {candidate.role}</option>
        ))}
      </select>
      {duplicate && (
        <>
          {conflicts.length === 0 ? <p>No conflicting fields. Known values will be combined.</p> : <p>Choose which value to keep for each conflict.</p>}
          <div className="merge-conflicts">
            {conflicts.map((field) => (
              <div className="field" key={field}>
                <label htmlFor={`merge-${field}`}>{FIELD_LABELS[field]}</label>
                <select id={`merge-${field}`} value={resolutions[field] ?? ""} onChange={(event) => setResolutions((current) => ({ ...current, [field]: event.target.value as "keep" | "duplicate" }))}>
                  <option value="">Choose a value</option>
                  <option value="keep">Keep: {formatFieldValue(field, retained[field])}</option>
                  <option value="duplicate">Duplicate: {formatFieldValue(field, duplicate[field])}</option>
                </select>
              </div>
            ))}
          </div>
        </>
      )}
      {error && <p role="alert" className="review-error">{error}</p>}
      <div className="form-actions">
        <button type="submit" className="primary" disabled={busy}>Merge applications</button>
        <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}
