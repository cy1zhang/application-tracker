import { useEffect, useRef, useState } from "react";

import { ApiError, listAllApplications, listAllReviews } from "./api/client";
import type { Application, ReviewItem } from "./api/types";
import { DeleteAccount } from "./settings/DeleteAccount";
import { SettingsPanel } from "./sheet/SettingsPanel";
import { Sheet } from "./sheet/Sheet";
import "./styles.css";

type AppProps = {
  email?: string | null;
  onSignOut?: () => void;
  onAccountDeleted?: () => void;
};

function App({ email, onSignOut, onAccountDeleted }: AppProps) {
  const [deleting, setDeleting] = useState(false);
  const [errorSettingsOpen, setErrorSettingsOpen] = useState(false);
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);
  // Loads can overlap; only the most recently started one may update state.
  const latestLoad = useRef(0);
  const latestReviewLoad = useRef(0);

  async function loadApplications() {
    const load = ++latestLoad.current;
    setLoadError(null);
    try {
      const loaded = await listAllApplications();
      if (load === latestLoad.current) {
        setApplications(loaded);
      }
    } catch (error) {
      if (load === latestLoad.current) {
        if (error instanceof ApiError && error.status === 403) {
          setAccessDenied(true);
        } else {
          setLoadError(
            error instanceof ApiError ? error.message : "Could not load applications.",
          );
        }
      }
    }
  }

  async function loadReviews() {
    const load = ++latestReviewLoad.current;
    try {
      const loaded = await listAllReviews();
      if (load === latestReviewLoad.current) {
        setReviews(loaded);
        setReviewError(null);
      }
    } catch (error) {
      if (load === latestReviewLoad.current) {
        if (error instanceof ApiError && error.status === 403) setAccessDenied(true);
        else setReviewError(error instanceof ApiError ? error.message : "Could not load reviews.");
      }
    }
  }

  async function refreshAfterReview() {
    await Promise.all([loadApplications(), loadReviews()]);
  }

  useEffect(() => {
    const onDeleting = () => setDeleting(true);
    const onDeleted = () => onAccountDeleted?.();
    window.addEventListener("account-deleting", onDeleting);
    window.addEventListener("account-deleted", onDeleted);
    return () => {
      window.removeEventListener("account-deleting", onDeleting);
      window.removeEventListener("account-deleted", onDeleted);
    };
  }, [onAccountDeleted]);

  useEffect(() => {
    void loadApplications();
    void loadReviews();
  }, []);

  return (
    <div className="app-shell">
      <header className="site-header">
        <span className="wordmark-mark" aria-hidden="true">
          AT
        </span>
        <span className="wordmark-name">Application Tracker</span>
        {onSignOut && (
          <div className="account-actions">
            <span>{email}</span>
            <button type="button" onClick={onSignOut}>Sign out</button>
          </div>
        )}
      </header>

      <main className="content">
        {deleting && (
          <DeleteAccount resumed onAccountDeleted={() => onAccountDeleted?.()} />
        )}
        {!deleting && accessDenied && (
          <section className="page" aria-labelledby="access-denied-title">
            <h1 id="access-denied-title">Access denied</h1>
            <p>Ask the app administrator to approve your account.</p>
          </section>
        )}
        {!deleting && !accessDenied &&
          (loadError !== null ? (
            <>
              <div role="alert" className="error">
                <p>Could not load applications: {loadError}</p>
                <button type="button" onClick={() => void loadApplications()}>Retry</button>
              </div>
              {email && onSignOut && (
                <><button type="button" onClick={() => setErrorSettingsOpen((open) => !open)}>Settings</button>
                {errorSettingsOpen && <SettingsPanel reviews={reviews} onReload={refreshAfterReview} onAccountDeleted={() => onAccountDeleted?.()} />}</>
              )}
            </>
          ) : applications === null ? (
            <p className="muted">Loading applications…</p>
          ) : (
            <Sheet
              applications={applications}
              reviews={reviews}
              onReload={refreshAfterReview}
              mode="live"
              reviewError={reviewError}
              onAccountDeleted={email && onSignOut ? () => onAccountDeleted?.() : undefined}
            />
          ))}
      </main>
    </div>
  );
}

export default App;
