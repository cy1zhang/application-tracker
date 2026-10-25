import type {
  AccountStatus,
  AiSettings,
  AiSettingsUpdate,
  AiQueuePage,
  AiQueueResult,
  AiChangePage,
  AutoApplyMode,
  Application,
  ApplicationCreate,
  ApplicationEvent,
  ApplicationPage,
  ApplicationPatch,
  LocalAiInput,
  LocalAiSuggestionResult,
  MergeRequest,
  PlanReportResponse,
  ReviewItem,
  ReviewPage,
  ReviewResolution,
  ReviewResolutionResult,
  SyncRun,
} from "./types";
import { authConfig, authConfigError } from "../auth/config";
import { getAccessToken } from "../auth/session";

const API_BASE = authConfig?.apiBaseUrl ?? "/api";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (authConfigError) throw new ApiError(0, authConfigError);
  const headers: Record<string, string> = {};
  if (init?.body) headers["Content-Type"] = "application/json";
  if (authConfig) {
    const token = await getAccessToken();
    if (!token) {
      window.dispatchEvent(new Event("auth-required"));
      throw new ApiError(401, "Authentication required");
    }
    headers.Authorization = `Bearer ${token}`;
  }
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: Object.keys(headers).length ? headers : undefined,
    });
  } catch {
    throw new ApiError(0, "Could not reach the server. Is the backend running?");
  }
  if (!response.ok) {
    if (authConfig && response.status === 401) {
      window.dispatchEvent(new Event("auth-required"));
    }
    if (response.status === 423) window.dispatchEvent(new Event("account-deleting"));
    if (response.status === 410) window.dispatchEvent(new Event("account-deleted"));
    throw new ApiError(response.status, await errorMessage(response));
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") {
      return body.detail;
    }
  } catch {
    // The error body was not JSON; use the generic message.
  }
  return `Request failed with status ${response.status}`;
}

export async function listAllApplications(): Promise<Application[]> {
  const applications: Application[] = [];
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor !== null) {
      query.set("cursor", cursor);
    }
    const page = await request<ApplicationPage>(`/applications?${query}`);
    applications.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor !== null);
  return applications;
}

export function getApplication(id: string): Promise<Application> {
  return request<Application>(`/applications/${encodeURIComponent(id)}`);
}

export function listApplicationEvents(id: string): Promise<ApplicationEvent[]> {
  return request<ApplicationEvent[]>(
    `/applications/${encodeURIComponent(id)}/events`,
  );
}

export function createApplication(
  payload: ApplicationCreate,
): Promise<Application> {
  return request<Application>("/applications", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateApplication(
  id: string,
  patch: ApplicationPatch,
): Promise<Application> {
  return request<Application>(`/applications/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function deleteApplication(id: string, expectedVersion: number): Promise<void> {
  return request<void>(`/applications/${encodeURIComponent(id)}?expected_version=${expectedVersion}`, {
    method: "DELETE",
  });
}

export function mergeApplication(id: string, payload: MergeRequest): Promise<Application> {
  return request<Application>(`/applications/${encodeURIComponent(id)}/merge`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function listAllReviews(): Promise<ReviewItem[]> {
  const reviews: ReviewItem[] = [];
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor !== null) query.set("cursor", cursor);
    const page = await request<ReviewPage>(`/reviews?${query}`);
    reviews.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor !== null);
  return reviews;
}

export function listAiChanges(cursor?: string): Promise<AiChangePage> {
  const query = new URLSearchParams({ limit: "50" });
  if (cursor) query.set("cursor", cursor);
  return request<AiChangePage>(`/ai-changes?${query}`);
}

export function undoAiChange(id: string): Promise<{ undone: boolean }> {
  return request<{ undone: boolean }>(`/ai-changes/${encodeURIComponent(id)}/undo`, { method: "POST" });
}

export function reconsiderAiChange(id: string): Promise<{ queued: boolean } | { action: string }> {
  return request<{ queued: boolean } | { action: string }>(`/ai-changes/${encodeURIComponent(id)}/reconsider`, { method: "POST" });
}

export function resolveReview(
  messageId: string,
  resolution: ReviewResolution,
): Promise<ReviewResolutionResult> {
  return request<ReviewResolutionResult>(
    `/reviews/${encodeURIComponent(messageId)}/resolve`,
    { method: "POST", body: JSON.stringify(resolution) },
  );
}

export function getLocalAiInput(messageId: string, signal?: AbortSignal): Promise<LocalAiInput> {
  return request<LocalAiInput>(
    `/reviews/${encodeURIComponent(messageId)}/local-ai-input`, { signal },
  );
}

export function listAiQueue(limit: number, signal?: AbortSignal): Promise<AiQueuePage> {
  return request<AiQueuePage>(`/ai-queue?limit=${limit}`, { signal });
}

export function getAiQueueInput(messageId: string, signal?: AbortSignal): Promise<LocalAiInput> {
  return request<LocalAiInput>(`/ai-queue/${encodeURIComponent(messageId)}/input`, { signal });
}

export function submitAiQueueResult(
  messageId: string,
  payload: { generation: number; model: string; extraction: Record<string, unknown> },
  signal?: AbortSignal,
): Promise<AiQueueResult> {
  return request<AiQueueResult>(
    `/ai-queue/${encodeURIComponent(messageId)}/result`,
    { method: "POST", body: JSON.stringify(payload), signal },
  );
}

export function submitLocalAiSuggestion(
  messageId: string,
  payload: { generation: number; model: string; extraction: Record<string, unknown> },
  signal?: AbortSignal,
): Promise<LocalAiSuggestionResult> {
  return request<LocalAiSuggestionResult>(
    `/reviews/${encodeURIComponent(messageId)}/local-ai-suggestion`,
    { method: "POST", body: JSON.stringify(payload), signal },
  );
}

export interface GmailConnectionStatus {
  connected: boolean;
  email: string | null;
}

export function getGmailConnection(): Promise<GmailConnectionStatus> {
  return request<GmailConnectionStatus>("/gmail/connection");
}

export function startGmailConnection(): Promise<{ authorization_url: string }> {
  return request<{ authorization_url: string }>("/gmail/connect", {
    method: "POST",
  });
}

export function disconnectGmail(): Promise<void> {
  return request<void>("/gmail/connection", { method: "DELETE" });
}

export function getLatestSync(): Promise<SyncRun> {
  return request<SyncRun>("/syncs/latest");
}

export function startSync(): Promise<SyncRun> {
  return request<SyncRun>("/syncs", { method: "POST" });
}

export async function getSettings(): Promise<AiSettings> {
  const settings = await request<{ ai: AiSettings; auto_apply_mode: AutoApplyMode }>("/settings");
  return { ...settings.ai, auto_apply_mode: settings.auto_apply_mode };
}

export function getPlanReport(): Promise<PlanReportResponse> {
  return request<PlanReportResponse>("/ai-plan/report");
}

export function applyPlan(): Promise<{ queued: boolean }> {
  return request<{ queued: boolean }>("/ai-plan/apply", { method: "POST" });
}

export function setAutoApplyMode(mode: "off" | "plan"): Promise<{ auto_apply_mode: AutoApplyMode }> {
  return request<{ auto_apply_mode: AutoApplyMode }>("/settings/auto-apply", {
    method: "PUT",
    body: JSON.stringify({ mode }),
  });
}

export async function saveAiSettings(payload: AiSettingsUpdate): Promise<AiSettings> {
  return (
    await request<{ ai: AiSettings }>("/settings/ai", {
      method: "PUT",
      body: JSON.stringify(payload),
    })
  ).ai;
}

export function deleteAiSettings(): Promise<void> {
  return request<void>("/settings/ai", { method: "DELETE" });
}

export function getAccountStatus(): Promise<AccountStatus> {
  return request<AccountStatus>("/account");
}

export function deleteAccount(): Promise<AccountStatus> {
  return request<AccountStatus>("/account", {
    method: "DELETE",
    body: JSON.stringify({ confirm: "DELETE" }),
  });
}
