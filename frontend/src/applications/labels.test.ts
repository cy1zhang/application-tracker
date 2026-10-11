import { expect, test } from "vitest";

import { formatCycle, formatFieldValue } from "./labels";

test("formats every combination of known and unknown cycle parts", () => {
  expect(formatCycle("summer", 2027)).toBe("Summer 2027");
  expect(formatCycle("summer", null)).toBe("Summer (year unknown)");
  expect(formatCycle(null, 2027)).toBe("2027 (season unknown)");
  expect(formatCycle(null, null)).toBe("Unknown");
});

test("formats history values with display labels", () => {
  expect(formatFieldValue("status", "oa_received")).toBe("OA received");
  expect(formatFieldValue("role_type", "co_op")).toBe("Co-op");
  expect(formatFieldValue("season", null)).toBe("Unknown");
  expect(formatFieldValue("year", 2027)).toBe("2027");
  expect(formatFieldValue("company", "Acme")).toBe("Acme");
});
