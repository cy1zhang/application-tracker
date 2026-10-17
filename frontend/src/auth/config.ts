export type AuthConfig = {
  apiBaseUrl: string;
  authority: string;
  clientId: string;
  domain: string;
};

export function readAuthConfig(env: Record<string, string | undefined>): AuthConfig | null {
  const values = [
    env.VITE_API_BASE_URL,
    env.VITE_COGNITO_AUTHORITY,
    env.VITE_COGNITO_CLIENT_ID,
    env.VITE_COGNITO_DOMAIN,
  ];
  if (values.every((value) => !value)) return null;
  if (values.some((value) => !value)) {
    throw new Error("Incomplete hosted authentication configuration");
  }
  return {
    apiBaseUrl: values[0]!,
    authority: values[1]!,
    clientId: values[2]!,
    domain: values[3]!,
  };
}

let config: AuthConfig | null = null;
let error: string | null = null;
try {
  config = readAuthConfig(import.meta.env);
} catch (caught) {
  error = caught instanceof Error ? caught.message : "Invalid authentication configuration";
}

export const authConfig = config;
export const authConfigError = error;
