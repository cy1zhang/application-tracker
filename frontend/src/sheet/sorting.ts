import type { Application, Season } from "../api/types";

export type SortKey = "company" | "role" | "role_type" | "cycle" | "status" | "application_date" | "updated_at";

const SEASON_ORDER: Record<Season, number> = { spring: 0, summer: 1, fall: 2, winter: 3 };

function compareNullable<T>(a: T | null, b: T | null, direction: number, compare: (left: T, right: T) => number): number {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return direction * compare(a, b);
}

function compareCycle(a: Application, b: Application, direction: number): number {
  const year = compareNullable(a.year, b.year, direction, (left, right) => left - right);
  if (year !== 0) return year;
  return compareNullable(a.season, b.season, direction, (left, right) => SEASON_ORDER[left] - SEASON_ORDER[right]);
}

export function sortApplications(apps: Application[], key: SortKey, dir: "asc" | "desc"): Application[] {
  const direction = dir === "asc" ? 1 : -1;
  return [...apps].sort((a, b) => {
    if (key === "cycle") return compareCycle(a, b, direction);
    return compareNullable(a[key], b[key], direction, (left, right) =>
      String(left).localeCompare(String(right), undefined, { sensitivity: "base" }),
    );
  });
}
