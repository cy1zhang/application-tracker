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
export type EventType = "created" | "updated" | "deleted" | "attached" | "merged";
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
  source_message_id?: string | null;
  thread_id?: string | null;
  merged_application_id?: string | null;
  merge_resolutions?: Record<string, "keep" | "duplicate"> | null;
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

export interface ReviewItem {
  message_id: string;
  reason: string;
  subject: string | null;
  sender: string | null;
  evidence: string;
  received_at: string | null;
  thread_id: string | null;
  source_url: string | null;
  application_id: string | null;
  proposed_status: ApplicationStatus | null;
  proposed_season: Season | null;
  proposed_year: number | null;
}

export interface ReviewPage {
  items: ReviewItem[];
  next_cursor: string | null;
}

export interface ReviewApplicationCreate extends Omit<ApplicationCreate, "status"> {
  status: ApplicationStatus | null;
}

export type ReviewResolution =
  | { action: "accept"; expected_version: number }
  | { action: "edit"; application_id: string; patch: ApplicationPatch }
  | { action: "reject" }
  | { action: "attach"; application_id: string; expected_version: number }
  | { action: "create"; application: ReviewApplicationCreate };

export interface ReviewResolutionResult {
  message_id: string;
  action: ReviewResolution["action"];
  application_id: string | null;
}

export interface MergeRequest {
  duplicate_id: string;
  expected_keep_version: number;
  expected_duplicate_version: number;
  resolutions: Record<string, "keep" | "duplicate">;
}

export interface SyncRun {
  run_id: string;
  mode: "import" | "incremental" | "rescan";
  status: "pending" | "running" | "completed" | "partial" | "failed" | "reconnect_required";
  started_at: string;
  import_after: string | null;
  processed_count: number;
  completed_at?: string | null;
  last_success_at: string | null;
}
