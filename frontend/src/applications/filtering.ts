import type {
  Application,
  ApplicationStatus,
  RoleType,
  Season,
} from "../api/types";

export const ALL = "all";
export const UNKNOWN_VALUE = "unknown";

export interface Filters {
  roleType: RoleType | typeof ALL;
  season: Season | typeof UNKNOWN_VALUE | typeof ALL;
  year: string;
  status: ApplicationStatus | typeof UNKNOWN_VALUE | typeof ALL;
  search: string;
}

export const DEFAULT_FILTERS: Filters = {
  roleType: "internship",
  season: ALL,
  year: ALL,
  status: ALL,
  search: "",
};

export type SortKey = "updated" | "application_date" | "company";

export function filterApplications(
  applications: Application[],
  filters: Filters,
): Application[] {
  const search = filters.search.trim().toLocaleLowerCase();
  return applications.filter((application) => {
    const roleType = application.role_type ?? "unknown";
    const season = application.season ?? UNKNOWN_VALUE;
    const year = application.year === null ? UNKNOWN_VALUE : String(application.year);
    const status = application.status ?? UNKNOWN_VALUE;
    if (filters.roleType !== ALL && roleType !== filters.roleType) return false;
    if (filters.season !== ALL && season !== filters.season) return false;
    if (filters.year !== ALL && year !== filters.year) return false;
    if (filters.status !== ALL && status !== filters.status) return false;
    if (search) {
      return [application.company, application.role].some((value) =>
        value?.toLocaleLowerCase().includes(search),
      );
    }
    return true;
  });
}

export function sortApplications(
  applications: Application[],
  key: SortKey,
): Application[] {
  return [...applications].sort((a, b) => {
    switch (key) {
      case "updated":
        return Date.parse(b.updated_at) - Date.parse(a.updated_at);
      case "application_date":
        return compareNullsLast(a.application_date, b.application_date, (x, y) =>
          y.localeCompare(x),
        );
      case "company":
        return compareNullsLast(a.company, b.company, (x, y) =>
          x.localeCompare(y, undefined, { sensitivity: "base" }),
        );
    }
  });
}

function compareNullsLast(
  a: string | null,
  b: string | null,
  compareValues: (a: string, b: string) => number,
): number {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return compareValues(a, b);
}

export function countByStatus(
  applications: Application[],
): Record<ApplicationStatus, number> {
  const counts: Record<ApplicationStatus, number> = {
    applied: 0,
    oa_received: 0,
    interviewing: 0,
    offer: 0,
    rejected: 0,
    withdrawn: 0,
  };
  for (const application of applications) {
    if (application.status !== null) {
      counts[application.status] += 1;
    }
  }
  return counts;
}

export function yearOptions(applications: Application[]): number[] {
  const years = new Set<number>();
  for (const application of applications) {
    if (application.year !== null) {
      years.add(application.year);
    }
  }
  return [...years].sort((a, b) => b - a);
}
