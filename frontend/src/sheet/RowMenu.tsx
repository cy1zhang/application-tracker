import { useEffect, useRef, useState } from "react";

import { ApiError, deleteApplication } from "../api/client";
import type { Application } from "../api/types";
import { ApplicationDetail } from "../applications/ApplicationDetail";
import { MergeForm } from "../applications/MergeForm";
import { UNKNOWN } from "../applications/labels";

type Props = {
  application: Application;
  candidates?: Application[];
  onChanged: () => Promise<void> | void;
};

export function RowMenu({ application, candidates = [], onChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [drawer, setDrawer] = useState<"history" | "merge" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const label = `${application.company ?? UNKNOWN} · ${application.role ?? UNKNOWN}`;

  useEffect(() => {
    if (drawer) closeRef.current?.focus();
  }, [drawer]);

  function closeDrawer() {
    setDrawer(null);
    triggerRef.current?.focus();
  }

  async function remove() {
    if (!window.confirm(`Delete ${label}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await deleteApplication(application.application_id, application.version);
      setOpen(false);
      await onChanged();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        await onChanged();
        setError("Changed elsewhere. Reload latest");
      } else {
        setError(caught instanceof ApiError ? caught.message : "Could not delete the row.");
      }
    } finally {
      setBusy(false);
    }
  }

  return <div className="sheet-row-menu">
    <button ref={triggerRef} type="button" aria-label={`Row actions for ${label}`} aria-expanded={open} onClick={() => setOpen((current) => !current)}>⋯</button>
    {open && <div className="sheet-row-actions">
      <button type="button" onClick={() => { setDrawer("history"); setOpen(false); }}>History</button>
      <button type="button" onClick={() => { setDrawer("merge"); setOpen(false); }}>Merge duplicate</button>
      <button type="button" disabled={busy} onClick={() => void remove()}>Delete row</button>
    </div>}
    {error && <span role="alert" className="sheet-cell-error">{error}</span>}
    {drawer && <aside className="sheet-row-drawer" aria-label={drawer === "history" ? "Application history" : "Merge duplicate"}>
      <button ref={closeRef} type="button" className="sheet-drawer-close" onClick={closeDrawer}>Close</button>
      {drawer === "history" ? (
        <ApplicationDetail applicationId={application.application_id} otherApplications={candidates} onMerged={async () => { await onChanged(); }} onBack={closeDrawer} />
      ) : (
        <MergeForm retained={application} candidates={candidates} onMerged={async () => { closeDrawer(); await onChanged(); }} onReload={async () => { await onChanged(); }} onCancel={closeDrawer} />
      )}
    </aside>}
  </div>;
}
