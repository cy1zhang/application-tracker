import { expect, test } from "vitest";

import { makeApplication } from "../test/fixtures";
import { sortApplications } from "./sorting";

test("test_nulls_sort_last_both_directions", () => {
  const missing = makeApplication({ application_id: "missing", company: null, season: null, year: null, status: null, application_date: null, role_type: null });
  const alpha = makeApplication({ application_id: "alpha", company: "Alpha", season: "spring", year: 2027, status: "applied", application_date: "2026-01-10", role_type: "co_op" });
  const zeta = makeApplication({ application_id: "zeta", company: "Zeta", season: "fall", year: 2027, status: "offer", application_date: "2026-12-10", role_type: "internship" });
  const apps = [missing, zeta, alpha];

  for (const key of ["company", "cycle", "status", "application_date", "role_type"] as const) {
    expect(sortApplications(apps, key, "asc").map((app) => app.application_id)).toEqual(["alpha", "zeta", "missing"]);
    expect(sortApplications(apps, key, "desc").map((app) => app.application_id)).toEqual(["zeta", "alpha", "missing"]);
  }
  expect(apps.map((app) => app.application_id)).toEqual(["missing", "zeta", "alpha"]);
});
