import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import type {
  Application,
  ApplicationPatch,
  ApplicationStatus,
  ApplicationValues,
  RoleType,
  Season,
} from "../api/types";
import { ROLE_TYPE_LABELS, SEASON_LABELS, STATUS_LABELS, UNKNOWN } from "./labels";

interface FormState {
  company: string;
  role: string;
  roleType: RoleType;
  season: Season | "";
  year: string;
  status: ApplicationStatus | "";
  applicationDate: string;
}

const EMPTY_FORM: FormState = {
  company: "",
  role: "",
  roleType: "unknown",
  season: "",
  year: "",
  status: "",
  applicationDate: "",
};

const PATCH_FIELDS = [
  "company",
  "role",
  "role_type",
  "season",
  "year",
  "status",
  "application_date",
] as const;

function valuesFromApplication(application: Application): FormState {
  return {
    company: application.company ?? "",
    role: application.role ?? "",
    roleType: application.role_type ?? "unknown",
    season: application.season ?? "",
    year: application.year === null ? "" : String(application.year),
    status: application.status ?? "",
    applicationDate: application.application_date ?? "",
  };
}

// Company, role, and status are required, except that editing may leave one
// blank when it was already unknown on the stored record.
function parseForm(
  state: FormState,
  initial: Application | undefined,
): ApplicationValues | string {
  const company = state.company.trim() || null;
  const role = state.role.trim() || null;
  const status = state.status || null;
  if (company === null && (initial === undefined || initial.company !== null)) {
    return "Enter a company.";
  }
  if (role === null && (initial === undefined || initial.role !== null)) {
    return "Enter a role.";
  }
  if (status === null && (initial === undefined || initial.status !== null)) {
    return "Choose a status.";
  }
  const year = state.year.trim() === "" ? null : Number(state.year);
  if (year !== null && !Number.isInteger(year)) {
    return "Year must be a whole number.";
  }
  return {
    company,
    role,
    role_type: state.roleType,
    season: state.season || null,
    year,
    status,
    application_date: state.applicationDate || null,
  };
}

export function buildPatch(
  original: Application,
  values: ApplicationValues,
): ApplicationPatch | null {
  const comparable = { ...original, role_type: original.role_type ?? "unknown" };
  const changes = Object.fromEntries(
    PATCH_FIELDS.filter((field) => values[field] !== comparable[field]).map(
      (field) => [field, values[field]],
    ),
  ) as Partial<ApplicationValues>;
  if (Object.keys(changes).length === 0) {
    return null;
  }
  return { expected_version: original.version, ...changes };
}

interface ApplicationFormProps {
  initial?: Application;
  submitLabel: string;
  onSubmit: (values: ApplicationValues) => Promise<void>;
  onCancel: () => void;
}

export function ApplicationForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: ApplicationFormProps) {
  const [state, setState] = useState<FormState>(() =>
    initial ? valuesFromApplication(initial) : EMPTY_FORM,
  );
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function update<K extends keyof FormState>(field: K, value: FormState[K]) {
    setState((current) => ({ ...current, [field]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = parseForm(state, initial);
    if (typeof parsed === "string") {
      setError(parsed);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(parsed);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not save the application.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="page" onSubmit={handleSubmit} noValidate>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="application-company">Company</label>
          <input
            id="application-company"
            value={state.company}
            maxLength={200}
            onChange={(event) => update("company", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="application-role">Role</label>
          <input
            id="application-role"
            value={state.role}
            maxLength={200}
            onChange={(event) => update("role", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="application-role-type">Role type</label>
          <select
            id="application-role-type"
            value={state.roleType}
            onChange={(event) =>
              update("roleType", event.target.value as RoleType)
            }
          >
            {(Object.keys(ROLE_TYPE_LABELS) as RoleType[]).map((roleType) => (
              <option key={roleType} value={roleType}>
                {ROLE_TYPE_LABELS[roleType]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="application-season">Season</label>
          <select
            id="application-season"
            value={state.season}
            onChange={(event) =>
              update("season", event.target.value as Season | "")
            }
          >
            <option value="">{UNKNOWN}</option>
            {(Object.keys(SEASON_LABELS) as Season[]).map((season) => (
              <option key={season} value={season}>
                {SEASON_LABELS[season]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="application-year">Year</label>
          <input
            id="application-year"
            type="number"
            step={1}
            inputMode="numeric"
            value={state.year}
            onChange={(event) => update("year", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="application-status">Status</label>
          <select
            id="application-status"
            value={state.status}
            onChange={(event) =>
              update("status", event.target.value as ApplicationStatus | "")
            }
          >
            <option value="">Select a status</option>
            {(Object.keys(STATUS_LABELS) as ApplicationStatus[]).map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="application-date">Application date</label>
          <input
            id="application-date"
            type="date"
            value={state.applicationDate}
            onChange={(event) => update("applicationDate", event.target.value)}
          />
        </div>
      </div>
      {error !== null && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" className="primary" disabled={submitting}>
          {submitLabel}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
