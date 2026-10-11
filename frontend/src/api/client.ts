import type {
  Application,
  ApplicationCreate,
  ApplicationEvent,
  ApplicationPage,
  ApplicationPatch,
} from "./types";

const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiError(0, "Could not reach the server. Is the backend running?");
  }
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
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
