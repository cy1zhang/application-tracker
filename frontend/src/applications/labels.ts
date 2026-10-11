import type {
  ApplicationStatus,
  FieldValue,
  RoleType,
  Season,
} from "../api/types";

export const UNKNOWN = "Unknown";

export const ROLE_TYPE_LABELS: Record<RoleType, string> = {
  internship: "Internship",
  co_op: "Co-op",
  full_time_new_grad: "Full-time / new grad",
  unknown: UNKNOWN,
};

export const SEASON_LABELS: Record<Season, string> = {
  summer: "Summer",
  fall: "Fall",
  spring: "Spring",
  winter: "Winter",
};

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  applied: "Applied",
  oa_received: "OA received",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export const FIELD_LABELS: Record<string, string> = {
  company: "Company",
  role: "Role",
  role_type: "Role type",
  season: "Season",
  year: "Year",
  status: "Status",
  application_date: "Application date",
};

export function formatCycle(season: Season | null, year: number | null): string {
  if (season !== null && year !== null) {
    return `${SEASON_LABELS[season]} ${year}`;
  }
  if (season !== null) {
    return `${SEASON_LABELS[season]} (year unknown)`;
  }
  if (year !== null) {
    return `${year} (season unknown)`;
  }
  return UNKNOWN;
}

const VALUE_LABELS: Partial<Record<string, Record<string, string>>> = {
  role_type: ROLE_TYPE_LABELS,
  season: SEASON_LABELS,
  status: STATUS_LABELS,
};

export function formatFieldValue(field: string, value: FieldValue): string {
  if (value === null) {
    return UNKNOWN;
  }
  return VALUE_LABELS[field]?.[String(value)] ?? String(value);
}

const timestampFormat = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export function formatTimestamp(iso: string): string {
  return timestampFormat.format(new Date(iso));
}
