import { useEffect, useState } from "react";

import {
  ApiError,
  disconnectGmail,
  getGmailConnection,
  getLatestSync,
  startGmailConnection,
  startSync,
  type GmailConnectionStatus,
} from "../api/client";
import type { SyncRun } from "../api/types";
import { navigateToGoogle } from "./navigation";

const ACTIVE_POLL_MS = 15_000;
const IDLE_POLL_MS = 60_000;

export function GmailConnection() {
  const [status, setStatus] = useState<GmailConnectionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<SyncRun | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [syncBusy, setSyncBusy] = useState(false);

  async function loadSync() {
    try {
      setRun(await getLatestSync());
      setSyncError(null);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setRun(null);
      } else {
        setSyncError("Could not load sync status.");
      }
    }
  }

  async function loadStatus() {
    setError(null);
    try {
      const connection = await getGmailConnection();
      setStatus(connection);
      if (connection.connected) {
        await loadSync();
      } else {
        setRun(null);
        setSyncError(null);
      }
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

  useEffect(() => {
    if (!status?.connected) return;
    const active = run?.status === "pending" || run?.status === "running";
    const timer = window.setInterval(
      () => { void loadSync(); },
      active ? ACTIVE_POLL_MS : IDLE_POLL_MS,
    );
    return () => window.clearInterval(timer);
  }, [status?.connected, run?.status]);

  async function beginSync() {
    setSyncBusy(true);
    setSyncError(null);
    try {
      setRun(await startSync());
    } catch {
      setSyncError("Could not start sync. Try again.");
    } finally {
      setSyncBusy(false);
    }
  }

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
        {status?.connected && (
          <div className="sync-status">
            <p role="status">{syncLabel(run)}</p>
            {run?.last_success_at && (
              <p className="muted">Last successful sync: {new Date(run.last_success_at).toLocaleString()}</p>
            )}
            {syncError && <p role="alert" className="error">{syncError}</p>}
          </div>
        )}
        {message && <p role="alert">{message}</p>}
        {error && <p role="alert" className="error">{error}</p>}
      </div>
      <div className="gmail-actions">
        {error && status === null ? (
          <button type="button" onClick={() => void loadStatus()} disabled={busy}>
            Retry Gmail status
          </button>
        ) : status?.connected ? (
          <>
            {run?.status === "reconnect_required" ? (
              <button type="button" onClick={() => void connect()} disabled={busy || syncBusy}>
                Reconnect Gmail
              </button>
            ) : (
              <button
                type="button" onClick={() => void beginSync()}
                disabled={busy || syncBusy || run?.status === "pending" || run?.status === "running"}
              >
                Sync now
              </button>
            )}
            <button type="button" onClick={() => void disconnect()} disabled={busy || syncBusy}>
              Disconnect Gmail
            </button>
          </>
        ) : (
          <button type="button" onClick={() => void connect()} disabled={busy || status === null}>
            Connect Gmail
          </button>
        )}
      </div>
    </section>
  );
}

function syncLabel(run: SyncRun | null): string {
  if (run === null) return "No sync has run yet.";
  const count = `${run.processed_count} messages processed`;
  switch (run.status) {
    case "pending": return `Sync queued (${count}).`;
    case "running": return `Syncing (${count}).`;
    case "completed": return `Sync complete (${count}).`;
    case "partial": return `Sync stopped after ${run.processed_count} messages. Try again.`;
    case "reconnect_required": return "Gmail access expired. Reconnect Gmail to sync again.";
    case "failed": return "Sync could not finish. Try again.";
  }
}
