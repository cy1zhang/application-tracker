import { useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

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
  const cancelled = useRef(false);
  const original = useRef<Application | null>(null);
  const restoreFocus = useRef(false);
  const editButton = useRef<HTMLButtonElement>(null);

  useLayoutEffect(() => {
    if (!editing && restoreFocus.current) {
      editButton.current?.focus();
      restoreFocus.current = false;
    }
  }, [editing]);

  function startEditing() {
    original.current = { ...application };
    cancelled.current = false;
    setValue(field === "cycle" ? "" : field === "role_type" ? application.role_type ?? "unknown" : String(application[field] ?? ""));
    setSeason(application.season ?? "");
    setYear(application.year === null ? "" : String(application.year));
    setError(null);
    setEditing(true);
  }

  async function save(restoreFocusAfterSave: boolean) {
    if (saving.current || cancelled.current || original.current === null) return;
    const snapshot = original.current;
    const values: ApplicationValues = {
      company: snapshot.company,
      role: snapshot.role,
      role_type: snapshot.role_type ?? "unknown",
      season: snapshot.season,
      year: snapshot.year,
      status: snapshot.status,
      application_date: snapshot.application_date,
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
    const patch = buildPatch(snapshot, values);
    if (patch === null) {
      restoreFocus.current = restoreFocusAfterSave;
      setEditing(false);
      return;
    }
    saving.current = true;
    try {
      const saved = await updateApplication(snapshot.application_id, patch);
      restoreFocus.current = restoreFocusAfterSave;
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
      cancelled.current = true;
      restoreFocus.current = true;
      setEditing(false);
      setError(null);
    } else if (event.key === "Enter") {
      event.preventDefault();
      void save(true);
    } else if (event.key === "Tab" && focusAdjacent(event.shiftKey)) {
      event.preventDefault();
      void save(false);
    }
  }

  return (
    <span data-sheet-cell className="sheet-cell" onKeyDown={handleKeyDown} onBlur={(event) => {
      if (editing && !cancelled.current && !event.currentTarget.contains(event.relatedTarget)) void save(event.relatedTarget === null);
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
        <button ref={editButton} type="button" className="sheet-cell-button" aria-label={field === "company" ? "Edit Company" : undefined} title={`Edit ${LABELS[field]}`} onClick={startEditing}>{displayValue(application, field)}</button>
      )}
      {error && <span role="alert" className="sheet-cell-error">{error}</span>}
    </span>
  );
}
