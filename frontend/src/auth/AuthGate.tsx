import { useEffect, useRef, useState, type ReactNode } from "react";

import type { AuthSession, SignedInUser } from "./session";

type AppSession = {
  email: string | null;
  onSignOut?: () => void;
  onAccountDeleted?: () => void;
};

export function AuthGate({
  session,
  configurationError,
  children,
}: {
  session: AuthSession | null;
  configurationError?: string | null;
  children: (session: AppSession) => ReactNode;
}) {
  const [user, setUser] = useState<SignedInUser | null>(null);
  const [loading, setLoading] = useState(session !== null);
  const [error, setError] = useState<string | null>(null);
  const [deletedNotice, setDeletedNotice] = useState(false);
  const callbackPromise = useRef<Promise<SignedInUser> | null>(null);

  useEffect(() => {
    if (!session) return;
    let active = true;
    const initialize = async () => {
      try {
        const query = new URLSearchParams(window.location.search);
        const returned = query.has("state") && (query.has("code") || query.has("error"));
        const signedIn = returned
          ? await (callbackPromise.current ??= session.completeSignIn())
          : await session.getUser();
        if (returned) window.history.replaceState({}, "", window.location.pathname);
        if (active) setUser(signedIn);
      } catch {
        if (active) setError("Could not complete sign-in. Please try again.");
      } finally {
        if (active) setLoading(false);
      }
    };
    const requireAuth = () => {
      setUser(null);
      void session.clearUser();
    };
    window.addEventListener("auth-required", requireAuth);
    void initialize();
    return () => {
      active = false;
      window.removeEventListener("auth-required", requireAuth);
    };
  }, [session]);

  if (configurationError) return <p role="alert">{configurationError}</p>;
  if (!session) return children({ email: null });
  if (loading) return <p className="auth-status">Checking sign-in…</p>;
  if (!user) {
    return (
      <main className="auth-screen">
        <span className="wordmark-mark" aria-hidden="true">AT</span>
        <h1>Application Tracker</h1>
        {error && <p role="alert">{error}</p>}
        {deletedNotice && <p role="status">Your account and data have been deleted.</p>}
        <button type="button" onClick={() => void session.signIn()}>
          Sign in with Google
        </button>
      </main>
    );
  }
  return children({
    email: user.email,
    onSignOut: () => {
      setUser(null);
      void session.signOut();
    },
    onAccountDeleted: () => {
      // The Cognito user no longer exists, so skip the hosted logout redirect.
      setUser(null);
      setDeletedNotice(true);
      void session.clearUser();
    },
  });
}
