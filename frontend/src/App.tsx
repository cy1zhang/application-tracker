import { useEffect, useRef, useState } from "react";

import { ApiError, createApplication, listAllApplications, listAllReviews } from "./api/client";
import type {
  Application,
  ApplicationCreate,
  ApplicationValues,
  ReviewItem,
} from "./api/types";
import { ApplicationDetail } from "./applications/ApplicationDetail";
import { ApplicationForm } from "./applications/ApplicationForm";
import { ApplicationList } from "./applications/ApplicationList";
import {
  DEFAULT_FILTERS,
  type Filters,
  type SortKey,
} from "./applications/filtering";
import { GmailConnection } from "./gmail/GmailConnection";
import { AiSettings } from "./settings/AiSettings";
import { DeleteAccount } from "./settings/DeleteAccount";
import { ReviewQueue } from "./reviews/ReviewQueue";
import "./styles.css";

type View =
  | { kind: "list" }
  | { kind: "create" }
  | { kind: "reviews" }
  | { kind: "detail"; applicationId: string };

type AppProps = {
  email?: string | null;
  onSignOut?: () => void;
  onAccountDeleted?: () => void;
};

function App({ email, onSignOut, onAccountDeleted }: AppProps) {
  const [deleting, setDeleting] = useState(false);
  const [view, setView] = useState<View>({ kind: "list" });
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sortKey, setSortKey] = useState<SortKey>("updated");
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

  function showReviews() {
    setView({ kind: "reviews" });
    void loadReviews();
  }

  function showList() {
    setView({ kind: "list" });
    void loadApplications();
    void loadReviews();
  }

  async function handleCreate(values: ApplicationValues) {
    // In create mode the form requires company, role, and status.
    const created = await createApplication(values as ApplicationCreate);
    setView({ kind: "detail", applicationId: created.application_id });
  }

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
        {!deleting && !accessDenied && (applications !== null || loadError !== null) && email && onSignOut && (
          <>
            <GmailConnection />
            <AiSettings />
            <DeleteAccount onAccountDeleted={() => onAccountDeleted?.()} />
          </>
        )}
        {!deleting && !accessDenied && applications !== null && (
          <nav className="tracker-nav" aria-label="Tracker views">
            <button type="button" onClick={showList}>Applications</button>
            <button type="button" onClick={showReviews}>Needs review ({reviews.length})</button>
          </nav>
        )}
        {!deleting && accessDenied && (
          <section className="page" aria-labelledby="access-denied-title">
            <h1 id="access-denied-title">Access denied</h1>
            <p>Ask the app administrator to approve your account.</p>
          </section>
        )}
        {!deleting && !accessDenied && view.kind === "create" && (
          <section className="page" aria-labelledby="create-title">
            <h1 id="create-title">New application</h1>
            <ApplicationForm
              submitLabel="Create application"
              onSubmit={handleCreate}
              onCancel={showList}
            />
          </section>
        )}
        {!deleting && !accessDenied && view.kind === "detail" && (
          <ApplicationDetail applicationId={view.applicationId} otherApplications={(applications ?? []).filter((item) => item.application_id !== view.applicationId)} onMerged={loadApplications} onBack={showList} />
        )}
        {!deleting && !accessDenied && view.kind === "reviews" && applications !== null && (
          reviewError ? (
            <div role="alert" className="error"><p>Could not load reviews: {reviewError}</p><button type="button" onClick={() => void loadReviews()}>Retry reviews</button></div>
          ) : <ReviewQueue reviews={reviews} applications={applications} onResolved={refreshAfterReview} onBack={showList} />
        )}
        {!deleting && !accessDenied && view.kind === "list" &&
          (loadError !== null ? (
            <div role="alert" className="error">
              <p>Could not load applications: {loadError}</p>
              <button type="button" onClick={() => void loadApplications()}>
                Retry
              </button>
            </div>
          ) : applications === null ? (
            <p className="muted">Loading applications…</p>
          ) : (
            <ApplicationList
              applications={applications}
              filters={filters}
              onFiltersChange={setFilters}
              sortKey={sortKey}
              onSortChange={setSortKey}
              onSelect={(applicationId) =>
                setView({ kind: "detail", applicationId })
              }
              onCreate={() => setView({ kind: "create" })}
            />
          ))}
      </main>
    </div>
  );
}

export default App;
