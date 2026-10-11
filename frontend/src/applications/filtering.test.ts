import { expect, test } from "vitest";

import type { Application } from "../api/types";
import { makeApplication } from "../test/fixtures";
import {
  DEFAULT_FILTERS,
  countByStatus,
  filterApplications,
  sortApplications,
  yearOptions,
  type Filters,
} from "./filtering";

const ALL_TYPES: Filters = { ...DEFAULT_FILTERS, roleType: "all" };

function ids(applications: Application[]): string[] {
  return applications.map((application) => application.application_id);
}

test("defaults to internships only", () => {
  const applications = [
    makeApplication({ application_id: "intern" }),
    makeApplication({ application_id: "coop", role_type: "co_op" }),
    makeApplication({ application_id: "missing", role_type: null }),
  ];

  expect(ids(filterApplications(applications, DEFAULT_FILTERS))).toEqual([
    "intern",
  ]);
});

test("unknown role type matches explicit unknown and missing values", () => {
  const applications = [
    makeApplication({ application_id: "explicit", role_type: "unknown" }),
    makeApplication({ application_id: "missing", role_type: null }),
    makeApplication({ application_id: "intern" }),
  ];

  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, roleType: "unknown" })),
  ).toEqual(["explicit", "missing"]);
});

test("unknown season and year match missing values", () => {
  const applications = [
    makeApplication({ application_id: "known" }),
    makeApplication({ application_id: "no-season", season: null }),
    makeApplication({ application_id: "no-year", year: null }),
  ];

  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, season: "unknown" })),
  ).toEqual(["no-season"]);
  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, year: "unknown" })),
  ).toEqual(["no-year"]);
  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, year: "2027" })),
  ).toEqual(["known", "no-season"]);
});

test("filters by status, including unknown status", () => {
  const applications = [
    makeApplication({ application_id: "applied" }),
    makeApplication({ application_id: "offer", status: "offer" }),
    makeApplication({ application_id: "missing", status: null }),
  ];

  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, status: "offer" })),
  ).toEqual(["offer"]);
  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, status: "unknown" })),
  ).toEqual(["missing"]);
});

test("search is trimmed, case-insensitive, and covers company and role", () => {
  const applications = [
    makeApplication({ application_id: "a", company: "Acme", role: "Data Intern" }),
    makeApplication({
      application_id: "b",
      company: "Globex",
      role: "Platform Engineer",
    }),
    makeApplication({ application_id: "c", company: null, role: "Acme Labs" }),
  ];

  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, search: "  ACME " })),
  ).toEqual(["a", "c"]);
  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, search: "platform" })),
  ).toEqual(["b"]);
});

test("sorts by last update, newest first", () => {
  const applications = [
    makeApplication({ application_id: "old", updated_at: "2026-09-01T00:00:00+00:00" }),
    makeApplication({
      application_id: "new",
      updated_at: "2026-09-02T00:00:00.500000+00:00",
    }),
  ];

  expect(ids(sortApplications(applications, "updated"))).toEqual(["new", "old"]);
});

test("sorts by application date, newest first, with unknown dates last", () => {
  const applications = [
    makeApplication({ application_id: "early", application_date: "2026-09-01" }),
    makeApplication({ application_id: "unknown", application_date: null }),
    makeApplication({ application_id: "late", application_date: "2026-09-15" }),
  ];

  expect(ids(sortApplications(applications, "application_date"))).toEqual([
    "late",
    "early",
    "unknown",
  ]);
});

test("sorts by company case-insensitively with unknown companies last", () => {
  const applications = [
    makeApplication({ application_id: "beta", company: "beta" }),
    makeApplication({ application_id: "unknown", company: null }),
    makeApplication({ application_id: "alpha", company: "Alpha" }),
  ];

  expect(ids(sortApplications(applications, "company"))).toEqual([
    "alpha",
    "beta",
    "unknown",
  ]);
});

test("counts every status, including zero counts", () => {
  const applications = [
    makeApplication(),
    makeApplication(),
    makeApplication({ status: "offer" }),
    makeApplication({ status: null }),
  ];

  expect(countByStatus(applications)).toEqual({
    applied: 2,
    oa_received: 0,
    interviewing: 0,
    offer: 1,
    rejected: 0,
    withdrawn: 0,
  });
});

test("lists distinct known years, newest first", () => {
  const applications = [
    makeApplication({ year: 2026 }),
    makeApplication({ year: null }),
    makeApplication({ year: 2027 }),
    makeApplication({ year: 2026 }),
  ];

  expect(yearOptions(applications)).toEqual([2027, 2026]);
});
