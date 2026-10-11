export type RoleType =
  | "internship"
  | "co_op"
  | "full_time_new_grad"
  | "unknown";
export type Season = "summer" | "fall" | "spring" | "winter";
export type ApplicationStatus =
  | "applied"
  | "oa_received"
  | "interviewing"
  | "offer"
  | "rejected"
  | "withdrawn";
export type EventType = "created" | "updated" | "deleted";
export type EventOrigin = "user" | "rules" | "ai";
export type FieldValue = string | number | null;

export interface Application {
  application_id: string;
  company: string | null;
  role: string | null;
  role_type: RoleType | null;
  season: Season | null;
  year: number | null;
  status: ApplicationStatus | null;
  application_date: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface ApplicationPage {
  items: Application[];
  next_cursor: string | null;
}

export interface ApplicationEvent {
  event_id: string;
  application_id: string;
  event_type: EventType;
  origin: EventOrigin;
  occurred_at: string;
  changes: Record<string, { before: FieldValue; after: FieldValue }>;
}

export interface ApplicationCreate {
  company: string;
  role: string;
  role_type: RoleType;
  season: Season | null;
  year: number | null;
  status: ApplicationStatus;
  application_date: string | null;
}

// What the form produces. Creating requires company, role, and status; editing
// may leave them null when they were already unknown.
export interface ApplicationValues {
  company: string | null;
  role: string | null;
  role_type: RoleType;
  season: Season | null;
  year: number | null;
  status: ApplicationStatus | null;
  application_date: string | null;
}

export type ApplicationPatch = Partial<ApplicationValues> & {
  expected_version: number;
};
