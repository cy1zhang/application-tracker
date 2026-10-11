import type { Application } from "../api/types";

export function makeApplication(
  overrides: Partial<Application> = {},
): Application {
  return {
    application_id: "app-1",
    company: "Acme",
    role: "Software Engineer Intern",
    role_type: "internship",
    season: "summer",
    year: 2027,
    status: "applied",
    application_date: "2026-09-20",
    created_at: "2026-09-20T12:00:00+00:00",
    updated_at: "2026-09-20T12:00:00+00:00",
    version: 1,
    ...overrides,
  };
}
