import { expect, test } from "vitest";

import { readAuthConfig } from "./config";

const deployed = {
  VITE_API_BASE_URL: "https://api.example.test",
  VITE_COGNITO_AUTHORITY: "https://cognito-idp.us-east-1.amazonaws.com/pool",
  VITE_COGNITO_CLIENT_ID: "client-123",
  VITE_COGNITO_DOMAIN: "https://tracker.auth.us-east-1.amazoncognito.com",
};

test("no deployment variables keep local mode", () => {
  expect(readAuthConfig({})).toBeNull();
});

test("all deployment variables select hosted mode", () => {
  expect(readAuthConfig(deployed)).toEqual({
    apiBaseUrl: "https://api.example.test",
    authority: "https://cognito-idp.us-east-1.amazonaws.com/pool",
    clientId: "client-123",
    domain: "https://tracker.auth.us-east-1.amazoncognito.com",
  });
});

test("partial deployment variables fail visibly", () => {
  expect(() =>
    readAuthConfig({ VITE_API_BASE_URL: deployed.VITE_API_BASE_URL }),
  ).toThrow("Incomplete hosted authentication configuration");
});
