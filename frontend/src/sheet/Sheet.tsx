import { useState } from "react";

import { createApplication } from "../api/client";
import type { Application, ApplicationCreate, ApplicationValues, ReviewItem } from "../api/types";
import { ApplicationDetail } from "../applications/ApplicationDetail";
import { ApplicationForm } from "../applications/ApplicationForm";
import { ALL, DEFAULT_FILTERS, UNKNOWN_VALUE, filterApplications, yearOptions, type Filters } from "../applications/filtering";
import { ROLE_TYPE_LABELS, SEASON_LABELS, STATUS_LABELS, UNKNOWN } from "../applications/labels";
import { ReviewQueue } from "../reviews/ReviewQueue";
import { SettingsPanel } from "./SettingsPanel";
import { SheetGrid } from "./SheetGrid";
import { sortApplications, type SortKey } from "./sorting";

type Props = {
  applications: Application[];
  reviews: ReviewItem[];
  onReload: () => Promise<void> | void;
  mode: "plan" | "live";
  onAccountDeleted?: () => void;
  reviewError?: string | null;
};

export function Sheet({ applications, reviews, onReload, onAccountDeleted, reviewError }: Props) {
  const [tab, setTab] = useState<"applications" | "reviews" | "ai">("applications");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({ ...DEFAULT_FILTERS, roleType: ALL });
  const [sortKey, setSortKey] = useState<SortKey>("updated_at");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const visible = sortApplications(filterApplications(applications, filters), sortKey, direction);

  function setFilter<K extends keyof Filters>(key: K, value: Filters[K]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function sortBy(key: SortKey) {
    if (key === sortKey) setDirection((current) => current === "asc" ? "desc" : "asc");
    else { setSortKey(key); setDirection("asc"); }
  }

  async function create(values: ApplicationValues) {
    const created = await createApplication(values as ApplicationCreate);
    await onReload();
    setCreating(false);
    setDetailId(created.application_id);
  }

  async function backToGrid() {
    setDetailId(null);
    await onReload();
  }

  return (
    <section className="sheet-shell" aria-label="Application sheet">
      {detailId !== null ? (
        <ApplicationDetail applicationId={detailId} otherApplications={applications.filter((item) => item.application_id !== detailId)} onMerged={async () => { await onReload(); }} onBack={() => void backToGrid()} />
      ) : creating ? (
        <section className="page" aria-labelledby="create-title">
          <h1 id="create-title">New application</h1>
          <ApplicationForm submitLabel="Create application" onSubmit={create} onCancel={() => setCreating(false)} />
        </section>
      ) : (
        <>
          <div className="sheet-toolbar">
            <h1>Applications</h1>
            <div className="sheet-actions">
              {tab === "applications" && <button type="button" className="primary" onClick={() => setCreating(true)}>New row</button>}
              <button type="button" aria-expanded={settingsOpen} onClick={() => setSettingsOpen((open) => !open)}>Settings</button>
            </div>
          </div>
          {settingsOpen && <SettingsPanel reviews={reviews} onReload={onReload} onAccountDeleted={onAccountDeleted} />}
          {tab === "applications" && (
            <div className="sheet-page" role="tabpanel" aria-label="Applications">
              <div className="filters sheet-filters">
                <div className="field"><label htmlFor="sheet-search">Search</label><input id="sheet-search" type="search" placeholder="Company or role" value={filters.search} onChange={(event) => setFilter("search", event.target.value)} /></div>
                <div className="field"><label htmlFor="sheet-role-type">Role type</label><select id="sheet-role-type" value={filters.roleType} onChange={(event) => setFilter("roleType", event.target.value as Filters["roleType"])}><option value={ALL}>All role types</option>{Object.entries(ROLE_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
                <div className="field"><label htmlFor="sheet-season">Season</label><select id="sheet-season" value={filters.season} onChange={(event) => setFilter("season", event.target.value as Filters["season"])}><option value={ALL}>All seasons</option>{Object.entries(SEASON_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}<option value={UNKNOWN_VALUE}>{UNKNOWN}</option></select></div>
                <div className="field"><label htmlFor="sheet-year">Year</label><select id="sheet-year" value={filters.year} onChange={(event) => setFilter("year", event.target.value)}><option value={ALL}>All years</option>{yearOptions(applications).map((year) => <option key={year} value={String(year)}>{year}</option>)}<option value={UNKNOWN_VALUE}>{UNKNOWN}</option></select></div>
                <div className="field"><label htmlFor="sheet-status">Status</label><select id="sheet-status" value={filters.status} onChange={(event) => setFilter("status", event.target.value as Filters["status"])}><option value={ALL}>All statuses</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}<option value={UNKNOWN_VALUE}>{UNKNOWN}</option></select></div>
              </div>
              {visible.length === 0 ? <p className="empty">{applications.length === 0 ? "No applications yet." : "No applications match these filters."}</p> : <SheetGrid applications={visible} sortKey={sortKey} direction={direction} onSort={sortBy} onSelect={setDetailId} onReload={onReload} />}
            </div>
          )}
          {tab === "reviews" && (reviewError ? <div role="alert" className="error"><p>Could not load reviews: {reviewError}</p><button type="button" onClick={() => void onReload()}>Retry reviews</button></div> : <ReviewQueue reviews={reviews} applications={applications} onResolved={async () => { await onReload(); }} onBack={() => setTab("applications")} showOllama={false} />)}
          {tab === "ai" && <p className="empty" role="tabpanel">No AI changes yet.</p>}
          <nav className="sheet-tabs" role="tablist" aria-label="Sheet tabs">
            <button type="button" role="tab" aria-selected={tab === "applications"} onClick={() => setTab("applications")}>Applications ({applications.length})</button>
            <button type="button" role="tab" aria-selected={tab === "reviews"} onClick={() => { setTab("reviews"); void onReload(); }}>Needs a look ({reviews.length})</button>
            <button type="button" role="tab" aria-selected={tab === "ai"} onClick={() => setTab("ai")}>AI changes (0)</button>
          </nav>
        </>
      )}
    </section>
  );
}
