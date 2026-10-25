import { useState } from "react";

import { undoAiChange } from "../api/client";
import type { AiChange, Application } from "../api/types";
import { UNKNOWN, formatTimestamp } from "../applications/labels";
import { EditableCell, type EditableField } from "./EditableCell";
import type { SortKey } from "./sorting";
import { aiChangeError } from "./aiMarkers";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "company", label: "Company" },
  { key: "role", label: "Role" },
  { key: "role_type", label: "Type" },
  { key: "cycle", label: "Cycle" },
  { key: "status", label: "Status" },
  { key: "application_date", label: "Applied" },
  { key: "updated_at", label: "Last update" },
];

type Props = {
  applications: Application[];
  sortKey: SortKey;
  direction: "asc" | "desc";
  onSort: (key: SortKey) => void;
  onSelect: (applicationId: string) => void;
  onReload: () => Promise<void> | void;
  markers?: Map<string, Map<string, AiChange>>;
  onAiChanged?: () => Promise<void> | void;
};

function markerFor(application: Application, field: EditableField, markers?: Map<string, Map<string, AiChange>>): AiChange | undefined {
  const fields = markers?.get(application.application_id);
  if (field === "cycle") {
    return [fields?.get("season"), fields?.get("year")]
      .filter((change): change is AiChange => change !== undefined)
      .sort((a, b) => b.received_at.localeCompare(a.received_at))[0];
  }
  return fields?.get(field);
}

function MarkedCell({ application, field, change, onReload, onAiChanged }: {
  application: Application;
  field: EditableField;
  change?: AiChange;
  onReload: () => Promise<void> | void;
  onAiChanged?: () => Promise<void> | void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const label = COLUMNS.find((column) => column.key === field)?.label ?? "Cycle";

  async function undo() {
    if (!change) return;
    setBusy(true);
    setError(null);
    try {
      await undoAiChange(change.message_id);
      await onAiChanged?.();
    } catch (caught) {
      setError(aiChangeError(caught));
    } finally {
      setBusy(false);
    }
  }

  return <span className={change ? "sheet-ai-cell" : undefined}>
    <EditableCell application={application} field={field} onSaved={onReload} onConflict={onReload} />
    {change && <>
      <button type="button" className="sheet-ai-marker" aria-label={`AI change for ${label}`} aria-haspopup="true" />
      <span className="sheet-ai-popover">
        <span>{new Date(change.received_at).toLocaleDateString(undefined, { dateStyle: "medium" })}</span>
        {change.source_url && <a href={change.source_url} target="_blank" rel="noopener noreferrer">Open email</a>}
        <button type="button" disabled={busy} onClick={() => void undo()}>Undo</button>
      </span>
    </>}
    {error && <span role="alert" className="sheet-cell-error">{error}</span>}
  </span>;
}

export function SheetGrid({ applications, sortKey, direction, onSort, onSelect, onReload, markers, onAiChanged }: Props) {
  return (
    <div className="sheet-grid-scroll" role="region" aria-label="Applications grid" tabIndex={0}>
      <table className="sheet-grid">
        <thead><tr>{COLUMNS.map(({ key, label }) => (
          <th key={key} scope="col" aria-sort={sortKey === key ? (direction === "asc" ? "ascending" : "descending") : undefined}>
            <button type="button" onClick={() => onSort(key)}>{label}</button>
          </th>
        ))}</tr></thead>
        <tbody>{applications.map((application) => (
          <tr key={application.application_id}>
            <td>
              <MarkedCell application={application} field="company" change={markerFor(application, "company", markers)} onReload={onReload} onAiChanged={onAiChanged} />
              <button type="button" className="sheet-detail-button" aria-label={`Open details for ${application.company ?? UNKNOWN}`} title="Open details" onClick={() => onSelect(application.application_id)} />
            </td>
            {(["role", "role_type", "cycle", "status", "application_date"] as EditableField[]).map((field) => (
              <td key={field}><MarkedCell application={application} field={field} change={markerFor(application, field, markers)} onReload={onReload} onAiChanged={onAiChanged} /></td>
            ))}
            <td>{formatTimestamp(application.updated_at)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}
