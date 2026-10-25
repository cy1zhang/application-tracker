import { expect, test } from "vitest";

import { makeApplication } from "../test/fixtures";
import { sortApplications } from "./sorting";

test("test_nulls_sort_last_both_directions", () => {
  const missing = makeApplication({ application_id: "missing", company: null, season: null, year: null });
  const alpha = makeApplication({ application_id: "alpha", company: "Alpha", season: "spring", year: 2027 });
  const zeta = makeApplication({ application_id: "zeta", company: "Zeta", season: "fall", year: 2027 });
  const apps = [missing, zeta, alpha];

  expect(sortApplications(apps, "company", "asc").map((app) => app.application_id)).toEqual(["alpha", "zeta", "missing"]);
  expect(sortApplications(apps, "company", "desc").map((app) => app.application_id)).toEqual(["zeta", "alpha", "missing"]);
  expect(sortApplications(apps, "cycle", "asc").map((app) => app.application_id)).toEqual(["alpha", "zeta", "missing"]);
  expect(sortApplications(apps, "cycle", "desc").map((app) => app.application_id)).toEqual(["zeta", "alpha", "missing"]);
  expect(apps.map((app) => app.application_id)).toEqual(["missing", "zeta", "alpha"]);
});
