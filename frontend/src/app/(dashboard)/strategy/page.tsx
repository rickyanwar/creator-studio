"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  listRecommendations,
  approveRecommendation,
  rejectRecommendation,
  getContentMemory,
  refreshContentMemory,
  listFanpages,
} from "@/lib/api";
import type { StrategyRecommendation, FanpageContentMemory, Fanpage } from "@/lib/types";
import { Icon } from "@iconify/react";

const APPLICABLE_KINDS = ["sleep_window", "daily_cap"];

export default function StrategyPage() {
  const [tab, setTab] = useState<"proposed" | "applied" | "rejected" | "all">("proposed");
  const [selectedFanpageId, setSelectedFanpageId] = useState<number | "">("");

  const { data: fanpagesRes } = useSWR("fanpages", () => listFanpages());
  const fanpages = (fanpagesRes?.data || []) as Fanpage[];

  const { data: recsRes, mutate: mutateRecs } = useSWR(
    ["recs", tab, selectedFanpageId],
    () => listRecommendations({
      status: tab === "all" ? undefined : tab,
      fanpage_id: selectedFanpageId === "" ? undefined : selectedFanpageId,
      limit: 100
    })
  );
  const recs = (recsRes?.data || []) as StrategyRecommendation[];

  // Memory panel
  const [memoryFanpageId, setMemoryFanpageId] = useState<number | "">("");
  const { data: memoryRes, mutate: mutateMemory, isValidating: memoryLoading } = useSWR(
    memoryFanpageId ? ["memory", memoryFanpageId] : null,
    () => getContentMemory(memoryFanpageId as number)
  );
  const memory = memoryRes?.data as FanpageContentMemory | undefined;

  const handleApprove = async (rec: StrategyRecommendation) => {
    if (APPLICABLE_KINDS.includes(rec.kind)) {
      let desc = "this recommendation";
      if (rec.kind === "sleep_window") {
        const fp = fanpages.find(f => f.id === rec.fanpage_id);
        const tz = fp?.timezone || "UTC";
        desc = `${rec.fanpage_name}'s sleep window to ${rec.proposal.publish_sleep_start_hour}:00–${rec.proposal.publish_sleep_end_hour}:00 (${tz})`;
      } else if (rec.kind === "daily_cap") {
        desc = `${rec.fanpage_name}'s daily limit to ${rec.proposal.publish_daily_limit}`;
      }
      if (!confirm(`This will change ${desc}. Proceed?`)) return;
    }
    await approveRecommendation(rec.id);
    mutateRecs();
  };

  const handleReject = async (rec: StrategyRecommendation) => {
    await rejectRecommendation(rec.id);
    mutateRecs();
  };

  const handleRefreshMemory = async () => {
    if (!memoryFanpageId) return;
    await refreshContentMemory(memoryFanpageId as number);
    mutateMemory();
  };

  const formatSleepWindow = (prop: any, fpTz: string) => {
    return `${String(prop.publish_sleep_start_hour).padStart(2, "0")}:00–${String(prop.publish_sleep_end_hour).padStart(2, "0")}:00 (${fpTz})`;
  };

  const formatProposal = (rec: StrategyRecommendation) => {
    if (!APPLICABLE_KINDS.includes(rec.kind)) return JSON.stringify(rec.proposal, null, 2);
    
    const fp = fanpages.find(f => f.id === rec.fanpage_id);
    const tz = fp?.timezone || "UTC";
    
    if (rec.kind === "sleep_window") {
      const cur = rec.current_values || {};
      const curStr = cur.publish_sleep_start_hour != null 
        ? formatSleepWindow(cur, tz) 
        : "None";
      const propStr = formatSleepWindow(rec.proposal, tz);
      return `${curStr} → ${propStr}`;
    }
    
    if (rec.kind === "daily_cap") {
      const cur = rec.current_values?.publish_daily_limit || "None";
      const prop = rec.proposal.publish_daily_limit;
      return `${cur} → ${prop}`;
    }
    return JSON.stringify(rec.proposal, null, 2);
  };

  return (
    <div className="max-w-6xl space-y-8 pb-10">
      <div>
        <h1 className="text-2xl font-bold text-text-primary">Strategy (Hermes)</h1>
        <p className="text-sm text-text-secondary mt-1">Review AI strategy recommendations and content memory.</p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex bg-surface-secondary p-1 rounded-lg">
              {(["proposed", "applied", "rejected", "all"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={`px-4 py-1.5 text-xs font-semibold rounded-md capitalize transition-colors ${
                    tab === t ? "bg-bg-paper text-primary-main shadow-sm" : "text-text-secondary hover:text-text-primary"
                  }`}
                >
                  {t}
                </button>
              ))}
            </div>
            <select
              className="input-rect text-xs w-full sm:w-48"
              value={selectedFanpageId}
              onChange={(e) => setSelectedFanpageId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="">All Fanpages</option>
              {fanpages.map((f) => (
                <option key={f.id} value={f.id}>{f.name}</option>
              ))}
            </select>
          </div>

          <div className="space-y-4">
            {recs.map((rec) => (
              <div key={rec.id} className="card p-4 space-y-3 relative">
                <div className="flex justify-between items-start gap-4">
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-semibold text-text-secondary">{rec.fanpage_name}</span>
                      <span className="text-[10px] uppercase bg-divider-soft px-1.5 py-0.5 rounded text-text-secondary tracking-wider font-semibold">
                        {rec.kind}
                      </span>
                    </div>
                    <h3 className="text-sm font-bold text-text-primary leading-snug">{rec.title}</h3>
                  </div>
                  <div className="text-right flex-shrink-0">
                    {rec.status === "proposed" && <span className="text-[10px] uppercase bg-blue-100 text-blue-800 px-2 py-1 rounded font-bold">Proposed</span>}
                    {rec.status === "applied" && <span className="text-[10px] uppercase bg-emerald-100 text-emerald-800 px-2 py-1 rounded font-bold">Applied</span>}
                    {rec.status === "approved" && <span className="text-[10px] uppercase bg-emerald-100 text-emerald-800 px-2 py-1 rounded font-bold">Approved</span>}
                    {rec.status === "rejected" && <span className="text-[10px] uppercase bg-red-100 text-red-800 px-2 py-1 rounded font-bold">Rejected</span>}
                    {rec.status === "superseded" && <span className="text-[10px] uppercase bg-gray-200 text-gray-700 px-2 py-1 rounded font-bold">Superseded</span>}
                    {rec.status === "failed" && <span className="text-[10px] uppercase bg-red-100 text-red-800 px-2 py-1 rounded font-bold">Failed</span>}
                  </div>
                </div>

                <p className="text-xs text-text-primary bg-bg-paper p-3 rounded border border-hairline whitespace-pre-wrap">
                  {rec.rationale}
                </p>

                {rec.evidence && Object.keys(rec.evidence).length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(rec.evidence).map(([k, v]) => (
                      <span key={k} className="text-[10px] bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300 px-1.5 py-0.5 rounded border border-indigo-100 dark:border-indigo-800">
                        {k}: {String(v)}
                      </span>
                    ))}
                  </div>
                )}

                <div className="bg-surface-secondary p-2 rounded border border-hairline">
                  <span className="text-[10px] text-text-secondary uppercase tracking-wider block mb-1 font-semibold">Proposal</span>
                  {APPLICABLE_KINDS.includes(rec.kind) ? (
                    <div className="text-xs font-mono font-bold text-primary-main">{formatProposal(rec)}</div>
                  ) : (
                    <pre className="text-[10px] font-mono text-text-primary overflow-x-auto">{formatProposal(rec)}</pre>
                  )}
                </div>

                {rec.status === "proposed" && (
                  <div className="flex items-center gap-3 pt-2">
                    <button onClick={() => handleApprove(rec)} className="btn-primary text-xs py-1.5 px-4">
                      Approve
                    </button>
                    <button onClick={() => handleReject(rec)} className="btn-secondary text-xs py-1.5 px-4 text-error-main border-error-main/30 hover:bg-error-main/10">
                      Reject
                    </button>
                  </div>
                )}

                {rec.decided_at && (
                  <div className="text-[10px] text-text-secondary pt-2 border-t border-hairline mt-3">
                    Decided at {new Date(rec.decided_at + "Z").toLocaleString()} by {rec.decided_by || "Unknown"}
                    {rec.apply_error && <span className="text-error-main ml-2">Error: {rec.apply_error}</span>}
                  </div>
                )}
              </div>
            ))}
            {recs.length === 0 && <p className="text-sm text-text-secondary text-center py-8">No recommendations found.</p>}
          </div>
        </div>

        <div className="xl:col-span-1">
          <div className="card sticky top-24 space-y-4">
            <h2 className="text-base font-semibold text-text-primary flex items-center justify-between">
              Content Memory
              <Icon icon="solar:database-bold-duotone" className="text-primary-main" width={18} />
            </h2>
            <select
              className="input-rect text-xs w-full"
              value={memoryFanpageId}
              onChange={(e) => setMemoryFanpageId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="">Select a Fanpage...</option>
              {fanpages.map((f) => (
                <option key={f.id} value={f.id}>{f.name}</option>
              ))}
            </select>

            {memoryFanpageId ? (
              <div className="space-y-4">
                <div className="flex justify-between items-center">
                  <button onClick={handleRefreshMemory} disabled={memoryLoading} className="btn-ghost text-[10px] px-2 py-1">
                    <Icon icon="solar:refresh-bold-duotone" className={memoryLoading ? "animate-spin" : ""} /> Refresh Now
                  </button>
                  <span className="text-[10px] text-text-secondary">
                    Updated: {memory?.auto_updated_at ? new Date(memory.auto_updated_at + "Z").toLocaleString() : "Never"}
                  </span>
                </div>

                <div className="space-y-2">
                  <div className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Auto (Last 90 Days)</div>
                  {memory?.auto ? (
                    <div className="bg-bg-paper p-2 rounded border border-hairline max-h-60 overflow-y-auto">
                      <pre className="text-[10px] font-mono whitespace-pre-wrap text-text-primary">
                        {JSON.stringify(memory.auto, null, 2)}
                      </pre>
                    </div>
                  ) : (
                    <p className="text-xs text-text-secondary italic">No auto memory yet.</p>
                  )}
                </div>

                <div className="space-y-2">
                  <div className="flex justify-between items-center">
                    <div className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Hermes Notes</div>
                    <span className="text-[10px] text-text-secondary">
                      {memory?.notes_updated_at ? new Date(memory.notes_updated_at + "Z").toLocaleString() : ""}
                    </span>
                  </div>
                  {memory?.notes ? (
                    <div className="bg-bg-paper p-2 rounded border border-hairline max-h-60 overflow-y-auto">
                      <pre className="text-[10px] font-mono whitespace-pre-wrap text-text-primary">
                        {JSON.stringify(memory.notes, null, 2)}
                      </pre>
                    </div>
                  ) : (
                    <p className="text-xs text-text-secondary italic">No notes written by Hermes yet.</p>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-xs text-text-secondary text-center py-4">Select a fanpage to view its memory.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
