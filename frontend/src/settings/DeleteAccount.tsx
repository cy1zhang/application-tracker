import { useEffect, useRef, useState } from "react";

import { ApiError, deleteAccount, getAccountStatus } from "../api/client";

const POLL_MS = 3000;

export function DeleteAccount({
  onAccountDeleted,
  resumed = false,
}: {
  onAccountDeleted: () => void;
  resumed?: boolean;
}) {
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(resumed);
  const [error, setError] = useState<string | null>(null);
  const [complete, setComplete] = useState(false);
  const finished = useRef(false);
  const notify = useRef(onAccountDeleted);
  notify.current = onAccountDeleted;

  function finish() {
    if (finished.current) return;
    finished.current = true;
    setComplete(true);
    notify.current();
  }

  useEffect(() => {
    if (!progress || complete) return;
    let active = true;
    const timer = setInterval(async () => {
      try {
        const status = await getAccountStatus();
        if (active && status.state === "deleted") finish();
      } catch (caught) {
        if (active && caught instanceof ApiError && caught.status === 410) finish();
        // Other failures are transient; keep polling.
      }
    }, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [progress, complete]);

  async function submit() {
    if (confirmation !== "DELETE" || busy) return;
    setBusy(true);
    setError(null);
    try {
      const status = await deleteAccount();
      if (status.state === "deleted") finish();
      else setProgress(true);
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status !== 0
          ? `Could not delete the account. ${caught.message}`
          : "Could not delete the account.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (progress) {
    return (
      <section className="danger-zone" aria-labelledby="danger-zone-title">
        <h2 id="danger-zone-title">Account deletion in progress</h2>
        <p role="status">Deleting your data...</p>
        <p className="muted">This finishes in the background within a few minutes. You will be signed out when it is done.</p>
      </section>
    );
  }

  return (
    <section className="danger-zone" aria-labelledby="danger-zone-title">
      <h2 id="danger-zone-title">Danger zone</h2>
      <p>Deleting your account permanently removes:</p>
      <ul>
        <li>your applications and their change history, including email-evidence excerpts</li>
        <li>review items and message references</li>
        <li>sync history</li>
        <li>the Gmail connection (Google access is revoked on a best-effort basis)</li>
        <li>your stored AI key and settings</li>
        <li>your sign-in account</li>
      </ul>
      <p>Not deleted: Your Gmail messages, your Google account, and anything already sent to your AI provider.</p>
      <ul>
        <li>
          If Google cannot be reached, remove this app at your Google account's permissions page.
        </li>
        <li>Operational logs expire after 14 days.</li>
        <li>
          Deleting a single application leaves a minimal message reference until you delete the account.
        </li>
        <li>Signing in again creates a new account that will need approval again.</li>
      </ul>
      <p>
        <strong>This cannot be undone.</strong> Deletion finishes in the background within a few
        minutes.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label>
          Type DELETE to confirm
          <input
            type="text"
            value={confirmation}
            autoComplete="off"
            disabled={busy}
            onChange={(event) => setConfirmation(event.target.value)}
          />
        </label>
        <button type="submit" disabled={confirmation !== "DELETE" || busy}>
          Delete account
        </button>
      </form>
      {error && <p role="alert" className="error">{error}</p>}
    </section>
  );
}
