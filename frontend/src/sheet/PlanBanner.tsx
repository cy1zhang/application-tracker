import { useEffect, useState } from "react";

import { ApiError, applyPlan, getPlanReport, getSettings, setAutoApplyMode } from "../api/client";
import type { AutoApplyMode, PlanExpectedCounts, PlanReportResponse } from "../api/types";
import { formatCycle, STATUS_LABELS, UNKNOWN } from "../applications/labels";

type Props = {
  mode: AutoApplyMode;
  onModeChange?: (mode: AutoApplyMode) => void;
  onReload?: () => Promise<void> | void;
};

function expectedCounts(report: PlanReportResponse): PlanExpectedCounts {
  return { analyzed: report.analyzed, create: report.report.counts.create ?? 0, update: report.report.counts.update ?? 0 };
}

export function PlanBanner({ mode, onModeChange, onReload }: Props) {
  const [report, setReport] = useState<PlanReportResponse | null>(null);
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [applying, setApplying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "plan") return;
    let cancelled = false;
    void getPlanReport().then(
      (loaded) => { if (!cancelled) setReport(loaded); },
      (caught: unknown) => { if (!cancelled) setError(caught instanceof Error ? caught.message : "Could not load plan report."); },
    );
    return () => { cancelled = true; };
  }, [mode]);

  useEffect(() => {
    if (!applying || mode !== "plan") return;
    let cancelled = false;
    let checking = false;
    const interval = window.setInterval(() => {
      if (checking) return;
      checking = true;
      void getSettings().then(async (settings) => {
        if (cancelled || settings.auto_apply_mode !== "on") return;
        window.clearInterval(interval);
        window.clearTimeout(timeout);
        setApplying(false);
        onModeChange?.("on");
        await onReload?.();
      }).catch(() => {
        // A transient settings error can be retried at the next interval.
      }).finally(() => {
        checking = false;
      });
    }, 5_000);
    const timeout = window.setTimeout(() => {
      cancelled = true;
      window.clearInterval(interval);
      setApplying(false);
      setError("Still applying. Reload to check the latest status.");
    }, 120_000);
    return () => { cancelled = true; window.clearInterval(interval); window.clearTimeout(timeout); };
  }, [applying, mode, onModeChange, onReload]);

  async function startTracking() {
    setBusy(true);
    setError(null);
    try {
      await setAutoApplyMode("plan");
      onModeChange?.("plan");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start automatic tracking.");
    } finally {
      setBusy(false);
    }
  }

  async function confirmApply() {
    if (!report) return;
    setBusy(true);
    setError(null);
    try {
      const current = await getPlanReport();
      if (current.waiting !== 0 || JSON.stringify(expectedCounts(current)) !== JSON.stringify(expectedCounts(report))) {
        setReport(current);
        setError("The plan changed. Review the new counts.");
        return;
      }
      await applyPlan(expectedCounts(current));
      setApplying(true);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409 && caught.message === "plan_changed") {
        setError("The plan changed. Review the new counts.");
        try { setReport(await getPlanReport()); }
        catch { setError("The plan changed. Could not reload the new counts."); }
      } else setError(caught instanceof Error ? caught.message : "Could not apply plan.");
    } finally {
      setBusy(false);
    }
  }

  if (mode === "on") return null;
  if (mode === "off") return <div className="sheet-plan">
    <button type="button" disabled={busy} onClick={() => void startTracking()}>Start automatic tracking (plan first)</button>
    {error && <p role="alert">{error}</p>}
  </div>;

  return <section className="sheet-plan" aria-label="AI plan">
    {report && <>
      <p>AI analyzed {report.analyzed} of {report.analyzed + report.waiting} emails</p>
      <button type="button" aria-expanded={open} onClick={() => setOpen((current) => !current)}>Review plan</button>
    </>}
    {applying && <p role="status">Applying…</p>}
    {error && <p role="alert">{error}</p>}
    {open && report && <section aria-label="AI plan report">
      <h2>Plan report</h2>
      <div className="sheet-plan-totals">
        <div><h3>Actions</h3>{Object.entries(report.report.counts).map(([action, count]) => <p key={action}>{action[0].toUpperCase() + action.slice(1)}: {count}</p>)}</div>
        <div><h3>Reasons</h3>{Object.entries(report.report.reasons).map(([reason, count]) => <p key={reason}>{reason}: {count}</p>)}</div>
      </div>
      <div className="sheet-plan-scroll">
        <table><thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Cycle</th><th>Action</th><th>Open email</th></tr></thead>
          <tbody>{report.report.sample.map((line) => <tr key={line.message_id}>
            <td>{line.company ?? UNKNOWN}</td><td>{line.role ?? UNKNOWN}</td>
            <td>{line.status ? STATUS_LABELS[line.status] : UNKNOWN}</td>
            <td>{formatCycle(line.season, line.year)}</td>
            <td>{line.action[0].toUpperCase() + line.action.slice(1)}</td>
            <td>{line.source_url && <a href={line.source_url} target="_blank" rel="noopener noreferrer">Open email</a>}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {!applying && report.waiting === 0 && (confirming ? <div>
        <p>Create {report.report.counts.create ?? 0} and update {report.report.counts.update ?? 0} applications?</p>
        <button type="button" disabled={busy} onClick={() => void confirmApply()}>Confirm apply</button>
        <button type="button" disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
      </div> : <button type="button" onClick={() => setConfirming(true)}>Apply</button>)}
    </section>}
  </section>;
}
