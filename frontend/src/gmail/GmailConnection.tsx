import { useEffect, useState } from "react";

import {
  disconnectGmail,
  getGmailConnection,
  startGmailConnection,
  type GmailConnectionStatus,
} from "../api/client";
import { navigateToGoogle } from "./navigation";

export function GmailConnection() {
  const [status, setStatus] = useState<GmailConnectionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadStatus() {
    setError(null);
    try {
      setStatus(await getGmailConnection());
    } catch {
      setError("Could not load Gmail connection status.");
    }
  }

  useEffect(() => {
    const url = new URL(window.location.href);
    const result = url.searchParams.get("gmail");
    if (result === "connected") setMessage("Gmail connected.");
    if (result === "failed") setMessage("Gmail connection failed. Try again.");
    if (result !== null) {
      url.searchParams.delete("gmail");
      window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    }
    void loadStatus();
  }, []);

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      const { authorization_url } = await startGmailConnection();
      navigateToGoogle(authorization_url);
    } catch {
      setError("Could not start Gmail connection. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!window.confirm("Disconnect Gmail? Existing tracker entries will remain.")) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await disconnectGmail();
      await loadStatus();
      setMessage("Gmail disconnected.");
    } catch {
      setError("Could not disconnect Gmail. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="gmail-connection" aria-labelledby="gmail-title">
      <div>
        <h2 id="gmail-title">Gmail connection</h2>
        {status?.connected ? (
          <p>Connected: <strong>{status.email}</strong></p>
        ) : status === null && error === null ? (
          <p>Loading Gmail status…</p>
        ) : (
          <p>Gmail is not connected.</p>
        )}
        <p className="muted">
          In Google Testing mode, Gmail access may expire after seven days.
          Reconnect if access stops working.
        </p>
        {message && <p role="alert">{message}</p>}
        {error && <p role="alert" className="error">{error}</p>}
      </div>
      <div className="gmail-actions">
        {error && status === null ? (
          <button type="button" onClick={() => void loadStatus()} disabled={busy}>
            Retry Gmail status
          </button>
        ) : status?.connected ? (
          <button type="button" onClick={() => void disconnect()} disabled={busy}>
            Disconnect Gmail
          </button>
        ) : (
          <button type="button" onClick={() => void connect()} disabled={busy || status === null}>
            Connect Gmail
          </button>
        )}
      </div>
    </section>
  );
}
