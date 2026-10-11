import { useEffect, useRef, useState } from "react";

import { ApiError, createApplication, listAllApplications } from "./api/client";
import type {
  Application,
  ApplicationCreate,
  ApplicationValues,
} from "./api/types";
import { ApplicationDetail } from "./applications/ApplicationDetail";
import { ApplicationForm } from "./applications/ApplicationForm";
import { ApplicationList } from "./applications/ApplicationList";
import {
  DEFAULT_FILTERS,
  type Filters,
  type SortKey,
} from "./applications/filtering";
import "./styles.css";

type View =
  | { kind: "list" }
  | { kind: "create" }
  | { kind: "detail"; applicationId: string };

function App() {
  const [view, setView] = useState<View>({ kind: "list" });
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sortKey, setSortKey] = useState<SortKey>("updated");
  // Loads can overlap; only the most recently started one may update state.
  const latestLoad = useRef(0);

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
        setLoadError(
          error instanceof ApiError ? error.message : "Could not load applications.",
        );
      }
    }
  }

  useEffect(() => {
    void loadApplications();
  }, []);

  function showList() {
    setView({ kind: "list" });
    void loadApplications();
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
      </header>

      <main className="content">
        {view.kind === "create" && (
          <section className="page" aria-labelledby="create-title">
            <h1 id="create-title">New application</h1>
            <ApplicationForm
              submitLabel="Create application"
              onSubmit={handleCreate}
              onCancel={showList}
            />
          </section>
        )}
        {view.kind === "detail" && (
          <ApplicationDetail applicationId={view.applicationId} onBack={showList} />
        )}
        {view.kind === "list" &&
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
