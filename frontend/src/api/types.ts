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
export type AutoApplyMode = "off" | "plan" | "on";

export interface PlanLine {
  message_id: string;
  action: "create" | "update" | "ignore" | "needs_look";
  reason: string;
  company: string | null;
  role: string | null;
  status: ApplicationStatus | null;
  season: Season | null;
  year: number | null;
  target: string | null;
  subject: string | null;
  sender: string | null;
  source_url: string | null;
}

export interface PlanReportResponse {
  analyzed: number;
  waiting: number;
  report: {
    counts: Record<string, number>;
    reasons: Record<string, number>;
    sample: PlanLine[];
  };
}

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

export interface AiChange {
  message_id: string;
  received_at: string;
  action: "created" | "updated" | "ignored";
  reason: string;
  application_id: string | null;
  changes: Record<string, { before: FieldValue; after: FieldValue }>;
  subject: string | null;
  sender: string | null;
  source_url: string | null;
  undone_at: string | null;
}

export interface AiChangePage {
  items: AiChange[];
  next_cursor: string | null;
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
  review_version?: number;
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
  ai_proposal?: AiProposal | null;
  rule_reason?: string | null;
  ai_note?: string | null;
  ai_provider?: string | null;
}

export interface AiProposal {
  company?: string | null;
  role?: string | null;
  employment_type?: RoleType | null;
  season?: Season | null;
  year?: number | null;
  status?: ApplicationStatus | null;
  requisition_id?: string | null;
  kind?:
    | null
    | "application_update"
    | "job_alert"
    | "recruiter_outreach"
    | "newsletter_or_event"
    | "other";
  evidence: Record<string, string>;
}

export type AiProviderName = "openai" | "anthropic" | "gemini" | "huggingface";

export interface AiSettings {
  auto_apply_mode?: AutoApplyMode;
  available_providers: AiProviderName[];
  hugging_face_host: string | null;
  provider: AiProviderName | null;
  key_hint: string | null;
  enabled: boolean;
  key_status: "ok" | "invalid" | null;
  requests_today: number;
  daily_limit: number;
  usage_day: string;
}

export interface AiSettingsUpdate {
  provider: AiProviderName;
  enabled: boolean;
  api_key?: string;
  consent?: boolean;
}

export interface ReviewPage {
  items: ReviewItem[];
  next_cursor: string | null;
}

export interface LocalAiInput {
  system_prompt: string;
  user_prompt: string;
  format: Record<string, unknown>;
  generation: number;
}

export interface LocalAiSuggestionResult {
  saved: boolean;
  review: ReviewItem | null;
}

export interface ReviewApplicationCreate extends Omit<ApplicationCreate, "status"> {
  status: ApplicationStatus | null;
}

export type ReviewResolution =
  | { action: "accept"; expected_version: number; expected_review_version: number }
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

export interface AccountStatus {
  state: "active" | "deleting" | "deleted";
}
