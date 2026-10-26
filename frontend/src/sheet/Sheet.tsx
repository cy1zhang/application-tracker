import { useEffect, useMemo, useState, type KeyboardEvent } from "react";

import { createApplication, listAiChanges } from "../api/client";
import type { AiChange, Application, ApplicationCreate, ApplicationValues, AutoApplyMode, ReviewItem } from "../api/types";
import { ApplicationDetail } from "../applications/ApplicationDetail";
import { ApplicationForm } from "../applications/ApplicationForm";
import { ALL, DEFAULT_FILTERS, UNKNOWN_VALUE, filterApplications, yearOptions, type Filters } from "../applications/filtering";
import { ROLE_TYPE_LABELS, SEASON_LABELS, STATUS_LABELS, UNKNOWN } from "../applications/labels";
import { ReviewQueue } from "../reviews/ReviewQueue";
import { AiChangesTab } from "./AiChangesTab";
import { EDITABLE_FIELD_LABELS, type EditableField } from "./EditableCell";
import { PlanBanner } from "./PlanBanner";
import { SettingsPanel } from "./SettingsPanel";
import { SheetGrid } from "./SheetGrid";
import { latestAiFieldChanges } from "./aiMarkers";
import { sortApplications, type SortKey } from "./sorting";

type Props = {
  applications: Application[];
  reviews: ReviewItem[];
  onReload: () => Promise<void> | void;
  mode: AutoApplyMode | "live";
  onAccountDeleted?: () => void;
  reviewError?: string | null;
};

const TAB_ORDER = ["applications", "reviews", "unparsed", "ai"] as const;
type SheetTab = typeof TAB_ORDER[number];

export function Sheet({ applications, reviews, onReload, mode, onAccountDeleted, reviewError }: Props) {
  const [autoApplyMode, setAutoApplyMode] = useState<AutoApplyMode>(mode === "live" ? "on" : mode);
  const [tab, setTab] = useState<SheetTab>("applications");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({ ...DEFAULT_FILTERS, roleType: ALL });
  const [sortKey, setSortKey] = useState<SortKey>("updated_at");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [aiChanges, setAiChanges] = useState<AiChange[]>([]);
  const [aiLoading, setAiLoading] = useState(true);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiRevision, setAiRevision] = useState(0);
  const [saveErrors, setSaveErrors] = useState<Record<string, { company: string; field: EditableField; message: string }>>({});
  const visible = sortApplications(filterApplications(applications, filters), sortKey, direction);
  const unparsedReviews = reviews.filter((review) => review.reason === "unparseable_message");
  const decisionReviews = reviews.filter((review) => review.reason !== "unparseable_message");
  const markers = useMemo(() => latestAiFieldChanges(aiChanges, applications), [aiChanges, applications]);

  function errorKey(application: Application, field: EditableField) {
    return JSON.stringify([application.application_id, field]);
  }

  function clearSaveError(application: Application, field: EditableField) {
    const key = errorKey(application, field);
    setSaveErrors((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  function recordSaveError(application: Application, field: EditableField, message: string) {
    setSaveErrors((current) => ({ ...current, [errorKey(application, field)]: { company: application.company ?? UNKNOWN, field, message } }));
  }

  useEffect(() => { setAutoApplyMode(mode === "live" ? "on" : mode); }, [mode]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setAiLoading(true);
      setAiError(null);
      setAiChanges([]);
      try {
        let cursor: string | null = null;
        do {
          const page = await listAiChanges(cursor ?? undefined);
          if (cancelled) return;
          setAiChanges((current) => [...current, ...page.items]);
          cursor = page.next_cursor;
        } while (cursor !== null);
      } catch (caught) {
        if (!cancelled) setAiError(caught instanceof Error ? caught.message : "Request failed");
      } finally {
        if (!cancelled) setAiLoading(false);
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [aiRevision]);

  async function aiChanged() {
    await onReload();
    setAiRevision((current) => current + 1);
  }

  function setFilter<K extends keyof Filters>(key: K, value: Filters[K]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function sortBy(key: SortKey) {
    if (key === sortKey) setDirection((current) => current === "asc" ? "desc" : "asc");
    else { setSortKey(key); setDirection("asc"); }
  }

  function selectTab(next: SheetTab) {
    setTab(next);
    if (next === "reviews" || next === "unparsed") void onReload();
  }

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, current: SheetTab) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const nextIndex = (TAB_ORDER.indexOf(current) + (event.key === "ArrowRight" ? 1 : -1) + TAB_ORDER.length) % TAB_ORDER.length;
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex]?.focus();
    selectTab(TAB_ORDER[nextIndex]);
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
      {Object.entries(saveErrors).map(([key, error]) => <div key={key} role="alert" className="error sheet-save-error">
        Couldn't save {EDITABLE_FIELD_LABELS[error.field]} for {error.company}: {error.message}
        <button type="button" aria-label={`Dismiss save error for ${EDITABLE_FIELD_LABELS[error.field]}`} onClick={() => setSaveErrors((current) => {
          const next = { ...current };
          delete next[key];
          return next;
        })}>Dismiss</button>
      </div>)}
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
          {settingsOpen && <SettingsPanel reviews={reviews} onReload={onReload} onAccountDeleted={onAccountDeleted} autoApplyMode={autoApplyMode} onModeChange={setAutoApplyMode} />}
          {autoApplyMode === "plan" && <PlanBanner mode={autoApplyMode} onModeChange={setAutoApplyMode} onReload={onReload} />}
          <div id="sheet-panel-applications" className="sheet-page" role="tabpanel" aria-labelledby="sheet-tab-applications" tabIndex={0} hidden={tab !== "applications"}>
            {tab === "applications" && <>
              <div className="filters sheet-filters">
                <div className="field"><label htmlFor="sheet-search">Search</label><input id="sheet-search" type="search" placeholder="Company or role" value={filters.search} onChange={(event) => setFilter("search", event.target.value)} /></div>
                <div className="field"><label htmlFor="sheet-role-type">Role type</label><select id="sheet-role-type" value={filters.roleType} onChange={(event) => setFilter("roleType", event.target.value as Filters["roleType"])}><option value={ALL}>All role types</option>{Object.entries(ROLE_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
                <div className="field"><label htmlFor="sheet-season">Season</label><select id="sheet-season" value={filters.season} onChange={(event) => setFilter("season", event.target.value as Filters["season"])}><option value={ALL}>All seasons</option>{Object.entries(SEASON_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}<option value={UNKNOWN_VALUE}>{UNKNOWN}</option></select></div>
                <div className="field"><label htmlFor="sheet-year">Year</label><select id="sheet-year" value={filters.year} onChange={(event) => setFilter("year", event.target.value)}><option value={ALL}>All years</option>{yearOptions(applications).map((year) => <option key={year} value={String(year)}>{year}</option>)}<option value={UNKNOWN_VALUE}>{UNKNOWN}</option></select></div>
                <div className="field"><label htmlFor="sheet-status">Status</label><select id="sheet-status" value={filters.status} onChange={(event) => setFilter("status", event.target.value as Filters["status"])}><option value={ALL}>All statuses</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}<option value={UNKNOWN_VALUE}>{UNKNOWN}</option></select></div>
              </div>
              {visible.length === 0 ? <p className="empty">{applications.length === 0 ? "No applications yet." : "No applications match these filters."}</p> : <SheetGrid applications={visible} allApplications={applications} markers={markers} onAiChanged={aiChanged} sortKey={sortKey} direction={direction} onSort={sortBy} onSelect={setDetailId} onReload={onReload} onSaveError={recordSaveError} onEditStart={clearSaveError} />}
            </>}
          </div>
          <div id="sheet-panel-reviews" role="tabpanel" aria-labelledby="sheet-tab-reviews" tabIndex={0} hidden={tab !== "reviews"}>{tab === "reviews" && (reviewError ? <div role="alert" className="error"><p>Could not load reviews: {reviewError}</p><button type="button" onClick={() => void onReload()}>Retry reviews</button></div> : <ReviewQueue reviews={decisionReviews} applications={applications} onResolved={async () => { await onReload(); }} onBack={() => setTab("applications")} showOllama={false} />)}</div>
          <div id="sheet-panel-unparsed" role="tabpanel" aria-labelledby="sheet-tab-unparsed" tabIndex={0} hidden={tab !== "unparsed"}>{tab === "unparsed" && (reviewError ? <div role="alert" className="error"><p>Could not load unparsed mail: {reviewError}</p><button type="button" onClick={() => void onReload()}>Retry reviews</button></div> : <ReviewQueue reviews={unparsedReviews} applications={applications} onResolved={async () => { await onReload(); }} onBack={() => setTab("applications")} showOllama={false} title="Unparsed mail" emptyMessage="No unparsed emails to inspect." dismissLabel="Dismiss email" />)}</div>
          <div id="sheet-panel-ai" role="tabpanel" aria-labelledby="sheet-tab-ai" tabIndex={0} hidden={tab !== "ai"}>{tab === "ai" && <AiChangesTab changes={aiChanges} applications={applications} loading={aiLoading} error={aiError} onChanged={aiChanged} />}</div>
          <nav className="sheet-tabs" role="tablist" aria-label="Sheet tabs">
            <button id="sheet-tab-applications" type="button" role="tab" aria-selected={tab === "applications"} aria-controls="sheet-panel-applications" tabIndex={tab === "applications" ? 0 : -1} onClick={() => selectTab("applications")} onKeyDown={(event) => handleTabKeyDown(event, "applications")}>Applications ({applications.length})</button>
            <button id="sheet-tab-reviews" type="button" role="tab" aria-selected={tab === "reviews"} aria-controls="sheet-panel-reviews" tabIndex={tab === "reviews" ? 0 : -1} onClick={() => selectTab("reviews")} onKeyDown={(event) => handleTabKeyDown(event, "reviews")}>Needs a look ({decisionReviews.length})</button>
            <button id="sheet-tab-unparsed" type="button" role="tab" aria-selected={tab === "unparsed"} aria-controls="sheet-panel-unparsed" tabIndex={tab === "unparsed" ? 0 : -1} onClick={() => selectTab("unparsed")} onKeyDown={(event) => handleTabKeyDown(event, "unparsed")}>Unparsed mail ({unparsedReviews.length})</button>
            <button id="sheet-tab-ai" type="button" role="tab" aria-selected={tab === "ai"} aria-controls="sheet-panel-ai" tabIndex={tab === "ai" ? 0 : -1} onClick={() => selectTab("ai")} onKeyDown={(event) => handleTabKeyDown(event, "ai")}>AI changes ({aiChanges.length})</button>
          </nav>
        </>
      )}
    </section>
  );
}
