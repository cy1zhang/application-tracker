import { UserManager, WebStorageStateStore } from "oidc-client-ts";

import { authConfig } from "./config";

export type SignedInUser = { email: string; accessToken: string };

export type AuthSession = {
  getUser(): Promise<SignedInUser | null>;
  completeSignIn(): Promise<SignedInUser>;
  signIn(): Promise<void>;
  signOut(): Promise<void>;
  clearUser(): Promise<void>;
};

function toSignedInUser(user: { access_token: string; profile: { email?: string } }): SignedInUser {
  return { accessToken: user.access_token, email: user.profile.email ?? "" };
}

export function createAuthSession(config: NonNullable<typeof authConfig>): AuthSession {
  const redirectUrl = `${window.location.origin}/`;
  const manager = new UserManager({
    authority: config.authority,
    client_id: config.clientId,
    redirect_uri: redirectUrl,
    post_logout_redirect_uri: redirectUrl,
    response_type: "code",
    scope: "openid email profile",
    extraQueryParams: { identity_provider: "Google" },
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    automaticSilentRenew: true,
  });

  return {
    async getUser() {
      let user = await manager.getUser();
      if (user?.expired) {
        try {
          user = await manager.signinSilent();
        } catch {
          return null;
        }
      }
      return user ? toSignedInUser(user) : null;
    },
    async completeSignIn() {
      return toSignedInUser(await manager.signinRedirectCallback());
    },
    signIn() {
      return manager.signinRedirect();
    },
    async signOut() {
      await manager.removeUser();
      const url = new URL(`${config.domain}/logout`);
      url.searchParams.set("client_id", config.clientId);
      url.searchParams.set("logout_uri", redirectUrl);
      window.location.assign(url.toString());
    },
    clearUser() {
      return manager.removeUser();
    },
  };
}

export const authSession = authConfig ? createAuthSession(authConfig) : null;

export async function getAccessToken(): Promise<string | null> {
  return (await authSession?.getUser())?.accessToken ?? null;
}
