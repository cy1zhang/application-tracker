import { fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { AuthGate } from "./AuthGate";
import type { AuthSession } from "./session";

function fakeSession(user: { email: string; accessToken: string } | null) {
  return {
    getUser: vi.fn().mockResolvedValue(user),
    completeSignIn: vi.fn().mockResolvedValue(user),
    signIn: vi.fn().mockResolvedValue(undefined),
    signOut: vi.fn().mockResolvedValue(undefined),
    clearUser: vi.fn().mockResolvedValue(undefined),
  } satisfies AuthSession;
}

afterEach(() => {
  window.history.replaceState({}, "", "/");
});

test("local mode renders the app without sign-in", () => {
  render(
    <AuthGate session={null}>
      {() => <p>Applications</p>}
    </AuthGate>,
  );

  expect(screen.getByText("Applications")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Sign in with Google" })).toBeNull();
});

test("hosted signed-out view starts Google sign-in", async () => {
  const session = fakeSession(null);
  render(<AuthGate session={session}>{() => <p>Applications</p>}</AuthGate>);

  fireEvent.click(await screen.findByRole("button", { name: "Sign in with Google" }));
  expect(session.signIn).toHaveBeenCalledOnce();
  expect(screen.queryByText("Applications")).toBeNull();
});

test("authorization callback unlocks the app and clears the code", async () => {
  window.history.replaceState({}, "", "/?code=one-time&state=opaque");
  const session = fakeSession({ email: "test@example.test", accessToken: "token" });
  render(
    <AuthGate session={session}>
      {({ email }) => <p>Signed in: {email}</p>}
    </AuthGate>,
  );

  expect(await screen.findByText("Signed in: test@example.test")).toBeInTheDocument();
  expect(session.completeSignIn).toHaveBeenCalledOnce();
  expect(window.location.search).toBe("");
});

test("StrictMode redeems the authorization code only once", async () => {
  window.history.replaceState({}, "", "/?code=one-time&state=opaque");
  const session = fakeSession({ email: "test@example.test", accessToken: "token" });
  render(
    <StrictMode>
      <AuthGate session={session}>{() => <p>Applications</p>}</AuthGate>
    </StrictMode>,
  );

  expect(await screen.findByText("Applications")).toBeInTheDocument();
  expect(session.completeSignIn).toHaveBeenCalledOnce();
});

test("a 401 event clears hosted session and shows sign-in", async () => {
  const session = fakeSession({ email: "test@example.test", accessToken: "token" });
  render(<AuthGate session={session}>{() => <p>Applications</p>}</AuthGate>);
  expect(await screen.findByText("Applications")).toBeInTheDocument();

  window.dispatchEvent(new Event("auth-required"));

  expect(await screen.findByRole("button", { name: "Sign in with Google" })).toBeInTheDocument();
  expect(session.clearUser).toHaveBeenCalledOnce();
});

test("signed-in header action signs out", async () => {
  const session = fakeSession({ email: "test@example.test", accessToken: "token" });
  render(
    <AuthGate session={session}>
      {({ email, onSignOut }) => (
        <button onClick={onSignOut}>Sign out {email}</button>
      )}
    </AuthGate>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Sign out test@example.test" }));
  expect(session.signOut).toHaveBeenCalledOnce();
});
