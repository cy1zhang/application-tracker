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

const MENU_OPEN_EVENT = "sheet-row-menu-open";

export function RowMenu({ application, candidates = [], onChanged }: Props) {
  const [open, setOpen] = useState(false);
  const [drawer, setDrawer] = useState<"history" | "merge" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const label = `${application.company ?? UNKNOWN} · ${application.role ?? UNKNOWN}`;

  useEffect(() => {
    if (drawer) closeRef.current?.focus();
  }, [drawer]);

  useEffect(() => {
    if (!open) return;
    function handleClick(event: MouseEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    }
    function handleOtherMenuOpen() {
      setOpen(false);
    }
    document.addEventListener("click", handleClick);
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener(MENU_OPEN_EVENT, handleOtherMenuOpen);
    return () => {
      document.removeEventListener("click", handleClick);
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener(MENU_OPEN_EVENT, handleOtherMenuOpen);
    };
  }, [open]);

  useEffect(() => {
    if (!drawer) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setDrawer(null);
        triggerRef.current?.focus();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      const first = focusable?.[0];
      const last = focusable?.[focusable.length - 1];
      if (!first || !last) return;
      const outside = !drawerRef.current?.contains(document.activeElement);
      if (event.shiftKey && (outside || document.activeElement === first)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (outside || document.activeElement === last)) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
  }, [drawer]);

  function closeDrawer() {
    setDrawer(null);
    triggerRef.current?.focus();
  }

  async function remove() {
    if (!window.confirm(`Delete ${label}?`)) return;
    setBusy(true);
    setError(null);
    let deleted = false;
    try {
      await deleteApplication(application.application_id, application.version);
      deleted = true;
      setOpen(false);
      await onChanged();
    } catch (caught) {
      if (deleted) {
        setError("Deleted. Reload to refresh");
      } else if (caught instanceof ApiError && caught.status === 409) {
        await onChanged();
        setError("Changed elsewhere. Reload latest");
      } else {
        setError(caught instanceof ApiError ? caught.message : "Could not delete the row.");
      }
    } finally {
      setBusy(false);
    }
  }

  return <div ref={menuRef} className="sheet-row-menu">
    <button ref={triggerRef} type="button" aria-label={`Row actions for ${label}`} aria-expanded={open} onClick={() => {
      if (open) setOpen(false);
      else {
        document.dispatchEvent(new Event(MENU_OPEN_EVENT));
        setOpen(true);
      }
    }}>⋯</button>
    {open && <div className="sheet-row-actions">
      <button type="button" onClick={() => { setDrawer("history"); setOpen(false); }}>History</button>
      <button type="button" onClick={() => { setDrawer("merge"); setOpen(false); }}>Merge duplicate</button>
      <button type="button" disabled={busy} onClick={() => void remove()}>Delete row</button>
    </div>}
    {error && <span role="alert" className="sheet-cell-error">{error}</span>}
    {drawer && <aside ref={drawerRef} className="sheet-row-drawer" aria-label={drawer === "history" ? "Application history" : "Merge duplicate"}>
      <button ref={closeRef} type="button" className="sheet-drawer-close" onClick={closeDrawer}>Close</button>
      {drawer === "history" ? (
        <ApplicationDetail applicationId={application.application_id} otherApplications={candidates} onMerged={async () => { await onChanged(); }} onBack={closeDrawer} />
      ) : (
        <MergeForm retained={application} candidates={candidates} onMerged={async () => { closeDrawer(); await onChanged(); }} onReload={async () => { await onChanged(); }} onCancel={closeDrawer} />
      )}
    </aside>}
  </div>;
}
