"use client";

import { useEffect, useId, useRef, useState } from "react";
import { calibrateGroupShareAccount, listGroupShareAccounts } from "@/lib/api";
import type { GroupShareDelivery } from "@/lib/types";
import { buttonClass, errorMessage } from "./Fields";

// The backend can wait behind Page phone posts and retry a busy phone, so a calibration can take about 10 minutes.
const POLL_LIMIT_MS = 12 * 60 * 1000;

export function CalibrationPanel({ fanpageId, destinationId, delivery, deviceMatches, prepare, onDelivery, onBusy, onPassed }: {
  fanpageId: number; destinationId: number; delivery: GroupShareDelivery | null; deviceMatches: boolean;
  prepare: () => Promise<GroupShareDelivery | null>;
  onDelivery: (delivery: GroupShareDelivery | null) => void; onBusy: (busy: boolean) => void;
  onPassed?: (delivery: GroupShareDelivery) => Promise<void>;
}) {
  const helpId = useId();
  const onPassedRef = useRef(onPassed);
  onPassedRef.current = onPassed;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [run, setRun] = useState<{ baseline: string; started: number } | null>(null);
  const calibration = deviceMatches ? delivery?.calibration : null;
  const changed = calibration?.paused_reason?.startsWith("recalibrate: FB app changed");
  const status = changed ? "FB app changed — recalibrate" : deviceMatches && delivery?.calibrated_at ? "Calibrated" : "Needs calibration";

  useEffect(() => {
    if (!run) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    function finish(message = "") {
      setError(message);
      setRun(null);
      setBusy(false);
      onBusy(false);
    }
    const deadline = setTimeout(() => {
      cancelled = true;
      clearTimeout(timer);
      finish("Calibration is still pending after 12 minutes. Reopen this editor to check the latest result.");
    }, Math.max(0, POLL_LIMIT_MS - (Date.now() - run.started)));
    async function poll() {
      try {
        const accounts = await listGroupShareAccounts(fanpageId);
        if (cancelled) return;
        const account = accounts.find((item) => item.destination_id === destinationId);
        if (!account) throw new Error("Account no longer exists.");
        onDelivery(account.delivery);
        const latest = account.delivery;
        if (!latest?.calibration?.requested_at && JSON.stringify([latest?.calibration, latest?.calibrated_at]) !== run!.baseline) {
          clearTimeout(deadline);
          let message = "";
          if (latest?.calibrated_at && latest.calibration?.ok === true && onPassedRef.current) {
            try {
              await onPassedRef.current(latest);
            } catch (err) {
              message = `Calibration passed, but turning Active back on failed: ${errorMessage(err)}`;
            }
            if (cancelled) return;
          }
          finish(message);
          return;
        }
      } catch (err) {
        if (cancelled) return;
        setError(errorMessage(err));
      }
      timer = setTimeout(poll, 5000);
    }
    timer = setTimeout(poll, 5000);
    return () => { cancelled = true; clearTimeout(timer); clearTimeout(deadline); };
  }, [run, fanpageId, destinationId, onDelivery, onBusy]);

  async function calibrate() {
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      const saved = await prepare();
      await calibrateGroupShareAccount(fanpageId, destinationId);
      setRun({ baseline: JSON.stringify([saved?.calibration, saved?.calibrated_at]), started: Date.now() });
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
      onBusy(false);
    }
  }

  return <div className="space-y-3 rounded-xl border border-line bg-surface p-4">
    <div className="flex flex-wrap items-center gap-2"><h4 className="font-medium text-text">Phone calibration</h4>
      <span role="status" className="rounded-full border border-line px-2 py-1 text-xs text-text2">{status}</span>
    </div>
    <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
      {[["FB app version", calibration?.fb_version], ["Android version", calibration?.android], ["Screen", calibration?.screen],
        ["Calibrated at", deviceMatches ? delivery?.calibrated_at : null]].map(([label, value]) =>
        <div key={label}><dt className="text-text2">{label}</dt><dd className="break-words text-text">{value || "—"}</dd></div>)}
    </dl>
    <div className="space-y-2"><h5 className="text-sm font-medium text-text">Last result</h5>
      {calibration?.detail && <p className="break-words text-sm text-text2">{calibration.detail}</p>}
      {calibration?.paused_reason && <p className="break-words text-sm text-text2">{calibration.paused_reason}</p>}
      {calibration?.last_attempt && <p className="break-words text-sm text-text2">{calibration.last_attempt.detail}</p>}
      {calibration?.steps?.length ? <ul className="space-y-2 text-sm text-text2">{calibration.steps.map((step, index) =>
        <li key={`${step.name}-${index}`} className="break-words"><span aria-label={step.ok ? "Passed" : "Failed"}>{step.ok ? "✓" : "✗"}</span> {step.name}: {step.detail}</li>)}</ul>
        : <p className="text-sm text-text2">No calibration result yet.</p>}
    </div>
    <p id={helpId} className="text-sm text-text2">Calibration opens a group on this phone and fills a draft, then discards it. Nothing is posted. Run it again after the Facebook app updates.</p>
    <button type="button" className={buttonClass} disabled={busy} aria-describedby={helpId} onClick={calibrate}>Calibrate (rehearsal, never posts)</button>
    {busy && <p role="status" className="text-sm text-text2">Calibrating… Calibration can take up to about 10 minutes; checking every 5 seconds.</p>}
    {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
  </div>;
}
