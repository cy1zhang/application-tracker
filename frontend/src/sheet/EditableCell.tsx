import { useRef, useState, type KeyboardEvent } from "react";

import { ApiError, updateApplication } from "../api/client";
import type { Application, ApplicationValues, ApplicationStatus, RoleType, Season } from "../api/types";
import { buildPatch } from "../applications/ApplicationForm";
import { ROLE_TYPE_LABELS, SEASON_LABELS, STATUS_LABELS, UNKNOWN, formatCycle } from "../applications/labels";

export type EditableField = "company" | "role" | "role_type" | "cycle" | "status" | "application_date";

type Props = {
  application: Application;
  field: EditableField;
  onSaved: (saved: Application) => Promise<void> | void;
  onConflict: () => Promise<void> | void;
};

const LABELS: Record<EditableField, string> = {
  company: "Company",
  role: "Role",
  role_type: "Type",
  cycle: "Cycle",
  status: "Status",
  application_date: "Applied",
};

function displayValue(application: Application, field: EditableField): string {
  switch (field) {
    case "cycle": return formatCycle(application.season, application.year);
    case "role_type": return ROLE_TYPE_LABELS[application.role_type ?? "unknown"];
    case "status": return application.status === null ? UNKNOWN : STATUS_LABELS[application.status];
    default: return application[field] ?? UNKNOWN;
  }
}

export function EditableCell({ application, field, onSaved, onConflict }: Props) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [season, setSeason] = useState<Season | "">("");
  const [year, setYear] = useState("");
  const [error, setError] = useState<string | null>(null);
  const saving = useRef(false);

  function startEditing() {
    setValue(field === "cycle" ? "" : field === "role_type" ? application.role_type ?? "unknown" : String(application[field] ?? ""));
    setSeason(application.season ?? "");
    setYear(application.year === null ? "" : String(application.year));
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (saving.current) return;
    const values: ApplicationValues = {
      company: application.company,
      role: application.role,
      role_type: application.role_type ?? "unknown",
      season: application.season,
      year: application.year,
      status: application.status,
      application_date: application.application_date,
    };
    if (field === "company" || field === "role") values[field] = value.trim() || null;
    if (field === "role_type") values.role_type = value as RoleType;
    if (field === "status") values.status = (value || null) as ApplicationStatus | null;
    if (field === "application_date") values.application_date = value || null;
    if (field === "cycle") {
      if (year !== "" && !Number.isInteger(Number(year))) {
        setError("Year must be a whole number.");
        return;
      }
      values.season = season || null;
      values.year = year === "" ? null : Number(year);
    }
    const patch = buildPatch(application, values);
    if (patch === null) {
      setEditing(false);
      return;
    }
    saving.current = true;
    try {
      const saved = await updateApplication(application.application_id, patch);
      setEditing(false);
      setError(null);
      await onSaved(saved);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setEditing(false);
        setError("Changed elsewhere. Reload latest");
        await onConflict();
      } else {
        setError(caught instanceof ApiError ? caught.message : "Could not save the application.");
      }
    } finally {
      saving.current = false;
    }
  }

  function focusAdjacent(backward: boolean): boolean {
    const cell = document.activeElement?.closest("td");
    const table = cell?.closest("table");
    if (!cell || !table) return false;
    const cells = Array.from(table.querySelectorAll("tbody td")).filter((item) => item.querySelector("[data-sheet-cell]"));
    const next = cells[cells.indexOf(cell) + (backward ? -1 : 1)];
    const button = next?.querySelector<HTMLButtonElement>("[data-sheet-cell] > button");
    if (!button) return false;
    button.focus();
    return true;
  }

  function handleKeyDown(event: KeyboardEvent<HTMLSpanElement>) {
    if (!editing) {
      if (event.key === "Enter") {
        event.preventDefault();
        startEditing();
      }
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setEditing(false);
      setError(null);
    } else if (event.key === "Enter") {
      event.preventDefault();
      void save();
    } else if (event.key === "Tab" && focusAdjacent(event.shiftKey)) {
      event.preventDefault();
      void save();
    }
  }

  return (
    <span data-sheet-cell className="sheet-cell" onKeyDown={handleKeyDown} onBlur={(event) => {
      if (editing && !event.currentTarget.contains(event.relatedTarget)) void save();
    }}>
      {editing ? field === "cycle" ? (
        <span className="sheet-cycle-editor">
          <select aria-label="Season" value={season} onChange={(event) => setSeason(event.target.value as Season | "")} autoFocus>
            <option value="">{UNKNOWN}</option>
            {(Object.keys(SEASON_LABELS) as Season[]).map((option) => <option key={option} value={option}>{SEASON_LABELS[option]}</option>)}
          </select>
          <input aria-label="Year" type="number" step={1} value={year} onChange={(event) => setYear(event.target.value)} />
        </span>
      ) : field === "role_type" || field === "status" ? (
        <select aria-label={LABELS[field]} value={value} onChange={(event) => setValue(event.target.value)} autoFocus>
          {field === "status" && <option value="">{UNKNOWN}</option>}
          {field === "role_type"
            ? (Object.keys(ROLE_TYPE_LABELS) as RoleType[]).map((option) => <option key={option} value={option}>{ROLE_TYPE_LABELS[option]}</option>)
            : (Object.keys(STATUS_LABELS) as ApplicationStatus[]).map((option) => <option key={option} value={option}>{STATUS_LABELS[option]}</option>)}
        </select>
      ) : (
        <input aria-label={LABELS[field]} type={field === "application_date" ? "date" : "text"} value={value} onChange={(event) => setValue(event.target.value)} autoFocus />
      ) : (
        <button type="button" className="sheet-cell-button" aria-label={field === "company" ? "Edit Company" : undefined} title={`Edit ${LABELS[field]}`} onClick={startEditing}>{displayValue(application, field)}</button>
      )}
      {error && <span role="alert" className="sheet-cell-error">{error}</span>}
    </span>
  );
}
