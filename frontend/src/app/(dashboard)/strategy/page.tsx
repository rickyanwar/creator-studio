"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  listRecommendations,
  approveRecommendation,
  rejectRecommendation,
  getContentMemory,
  refreshContentMemory,
} from "@/lib/api";
import type { StrategyRecommendation, FanpageContentMemory } from "@/lib/types";
import { Icon } from "@iconify/react";
import { useFanpages } from "@/lib/useFanpages";
import { useBrandFilter } from "@/contexts/BrandFilterContext";

const APPLICABLE_KINDS = ["sleep_window", "daily_cap", "best_hours"];

export default function StrategyPage() {
  const [tab, setTab] = useState<"proposed" | "applied" | "rejected" | "all">("proposed");
  const { selectedBrandId, setSelectedBrandId } = useBrandFilter();
  const selectedFanpageId = selectedBrandId ?? "";

  const { data: fanpages = [] } = useFanpages();

  const { data: recsRes, mutate: mutateRecs, isLoading: recsLoading } = useSWR(
    ["recs", tab, selectedFanpageId],
    () =>
      listRecommendations({
        status: tab === "all" ? undefined : tab,
        fanpage_id: selectedFanpageId === "" ? undefined : selectedFanpageId,
        limit: 100,
      })
  );
  const recs = (recsRes?.data || []) as StrategyRecommendation[];

  // Memory panel
  const [memoryFanpageId, setMemoryFanpageId] = useState<number | "">("");
  const {
    data: memoryRes,
    mutate: mutateMemory,
    isValidating: memoryLoading,
  } = useSWR(
    memoryFanpageId ? ["memory", memoryFanpageId] : null,
    () => getContentMemory(memoryFanpageId as number)
  );
  const memory = memoryRes?.data as FanpageContentMemory | undefined;

  const handleApprove = async (rec: StrategyRecommendation) => {
    if (APPLICABLE_KINDS.includes(rec.kind)) {
      let desc = "this recommendation";
      if (rec.kind === "sleep_window") {
        const fp = fanpages.find((f) => f.id === rec.fanpage_id);
        const tz = fp?.timezone || "UTC";
        desc = `${rec.fanpage_name}'s sleep window to ${
          rec.proposal?.publish_sleep_start_hour ?? "?"
        }:00–${rec.proposal?.publish_sleep_end_hour ?? "?"}:00 (${tz})`;
      } else if (rec.kind === "daily_cap") {
        desc = `${rec.fanpage_name}'s daily limit to ${
          rec.proposal?.publish_daily_limit ?? "?"
        }`;
      } else if (rec.kind === "best_hours") {
        desc = `${rec.fanpage_name}'s posting hour weights`;
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

  const formatSleepWindow = (prop: Record<string, unknown> | null | undefined, fpTz: string) => {
    if (!prop || prop.publish_sleep_start_hour == null || prop.publish_sleep_end_hour == null) {
      return `Unknown (${fpTz})`;
    }
    return `${String(prop.publish_sleep_start_hour).padStart(2, "0")}:00–${String(
      prop.publish_sleep_end_hour
    ).padStart(2, "0")}:00 (${fpTz})`;
  };

  const getNowAndProposed = (rec: StrategyRecommendation) => {
    const fp = fanpages.find((f) => f.id === rec.fanpage_id);
    const tz = fp?.timezone || "UTC";

    if (rec.kind === "sleep_window") {
      const cur = (rec.current_values || {}) as Record<string, unknown>;
      const curStr =
        cur.publish_sleep_start_hour != null ? formatSleepWindow(cur, tz) : "None";
      const propStr = formatSleepWindow(
        rec.proposal as Record<string, unknown> | null | undefined,
        tz
      );
      return { now: curStr, prop: propStr };
    }

    if (rec.kind === "daily_cap") {
      const cur = rec.current_values?.publish_daily_limit ?? "None";
      const prop = rec.proposal?.publish_daily_limit ?? "None";
      return { now: `${cur} posts/day`, prop: `${prop} posts/day` };
    }

    if (rec.kind === "best_hours") {
      const cur = rec.current_values?.publish_hour_weights as Record<string, number> | undefined;
      const curStr = cur && Object.keys(cur).length > 0 ? "Hour weights applied" : "Default spread";
      const prop = rec.proposal || {};
      let propStr = "Hour weights";
      if (Array.isArray(prop.best_hours)) {
        propStr = `Hours: ${prop.best_hours.map((h: unknown) => `${h}:00`).join(", ")}`;
      } else if (prop.weights && typeof prop.weights === "object") {
        propStr = `${Object.keys(prop.weights).length} hour weights`;
      }
      return { now: curStr, prop: propStr };
    }

    const cur = rec.current_values;
    const prop = rec.proposal;
    return {
      now: cur ? JSON.stringify(cur) : "Current settings",
      prop: prop ? JSON.stringify(prop) : "Recommended",
    };
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "proposed":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-info-soft text-info">
            <span className="w-1.5 h-1.5 rounded-full bg-current" />
            Proposed
          </span>
        );
      case "applied":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
            <span className="w-1.5 h-1.5 rounded-full bg-current" />
            Applied
          </span>
        );
      case "approved":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
            <span className="w-1.5 h-1.5 rounded-full bg-current" />
            Approved · info only
          </span>
        );
      case "rejected":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-surface2 text-text2">
            <span className="w-1.5 h-1.5 rounded-full bg-current" />
            Rejected
          </span>
        );
      case "failed":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-bad-soft text-bad">
            <span className="w-1.5 h-1.5 rounded-full bg-current" />
            Failed
          </span>
        );
      case "superseded":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-surface2 text-text2">
            <span className="w-1.5 h-1.5 rounded-full bg-current" />
            Superseded
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-surface2 text-text2">
            {status}
          </span>
        );
    }
  };

  const selectedFanpage = fanpages.find((f) => f.id === memoryFanpageId);

  return (
    <div className="space-y-5 max-w-[1240px]">
      {/* ── Page Header ────────────────────────────── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl sm:text-[26px] font-bold text-text tracking-tight">Strategy</h1>
          <p className="text-sm text-text2">
            Hermes studies what works and suggests changes. Nothing changes until you approve.
          </p>
          <p className="text-xs text-text2">
            Quiet hours, Max posts/day, and Best hours are applied when you approve. Other ideas are info only: approving records them and changes nothing.
          </p>
        </div>

        <select
          aria-label="Brand"
          value={selectedFanpageId}
          onChange={(e) =>
            setSelectedBrandId(e.target.value ? Number(e.target.value) : null)
          }
          className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-medium focus:outline-none"
        >
          <option value="">All brands</option>
          {fanpages.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </select>
      </div>

      {/* ── Status Tabs ────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-2">
        <div
          role="tablist"
          aria-label="Status"
          className="flex items-center gap-1 p-1 rounded-xl bg-surface2"
        >
          {[
            { id: "proposed", label: "To decide" },
            { id: "applied", label: "Applied" },
            { id: "rejected", label: "Rejected" },
            { id: "all", label: "All" },
          ].map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => setTab(t.id as typeof tab)}
              className={`h-8 px-3 rounded-lg text-xs font-semibold transition-colors cursor-pointer flex items-center gap-1.5 ${
                tab === t.id
                  ? "bg-surface text-text shadow-sm"
                  : "text-text2 hover:text-text"
              }`}
            >
              <span>{t.label}</span>
              {tab === t.id && (
                <span className="px-1.5 py-0.2 rounded-full bg-surface2 text-text2 text-[10px]">
                  {recs.length}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      <p className="text-xs text-text2 leading-relaxed">
        <strong className="text-text font-semibold">Proposed</strong> waits for you ·{" "}
        <strong className="text-text font-semibold">Applied</strong> the setting was changed ·{" "}
        <strong className="text-text font-semibold">Approved</strong> info only, nothing changed ·{" "}
        <strong className="text-text font-semibold">Rejected</strong> ·{" "}
        <strong className="text-text font-semibold">Failed</strong> could not be applied ·{" "}
        <strong className="text-text font-semibold">Superseded</strong> a newer proposal replaced it
      </p>

      {/* ── Main Layout: Recommendations & Memory ───── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* Left Column: Proposals List */}
        <div className="lg:col-span-7 xl:col-span-8 flex flex-col gap-4">
          {recsLoading && (
            <div className="space-y-4 animate-pulse">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-44 rounded-[14px] bg-surface2 border border-line" />
              ))}
            </div>
          )}

          {!recsLoading && recs.length === 0 && (
            <div className="p-12 text-center bg-surface border border-line rounded-[14px]">
              <div className="w-10 h-10 rounded-full bg-surface2 text-text2 flex items-center justify-center mx-auto mb-2">
                <Icon icon="solar:lightbulb-bold-duotone" width={20} />
              </div>
              <p className="text-sm font-semibold text-text">No recommendations</p>
              <p className="text-xs text-text2 mt-0.5">
                Hermes has not proposed any changes for this filter.
              </p>
            </div>
          )}

          {recs.map((rec) => {
            const comparison = getNowAndProposed(rec);
            const isApplicable = APPLICABLE_KINDS.includes(rec.kind);

            return (
              <article
                key={rec.id}
                className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-text2">
                      {rec.fanpage_name}
                    </span>
                    <span className="px-2 py-0.5 rounded-md border border-line text-[11px] font-semibold text-text uppercase tracking-wider bg-surface2">
                      {rec.kind}
                    </span>
                  </div>
                  {getStatusBadge(rec.status)}
                </div>

                <h2 className="text-base font-semibold text-text">{rec.title}</h2>

                {/* Now vs Proposed Comparison */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  <div className="p-3 rounded-xl bg-surface2">
                    <span className="text-[11px] text-text2 block mb-0.5">Now</span>
                    <span className="text-sm font-semibold text-text break-words">
                      {comparison.now}
                    </span>
                  </div>
                  <div className="p-3 rounded-xl bg-pri-soft text-pri-text">
                    <span className="text-[11px] text-pri-text block mb-0.5 opacity-80">
                      Proposed
                    </span>
                    <span className="text-sm font-semibold text-pri-text break-words">
                      {comparison.prop}
                    </span>
                  </div>
                </div>

                {/* Why / Rationale */}
                <p className="text-xs text-text2 leading-relaxed whitespace-pre-wrap">
                  {rec.rationale}
                </p>

                {/* Evidence items */}
                {rec.evidence && Object.keys(rec.evidence).length > 0 && (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {Object.entries(rec.evidence).map(([k, v]) => (
                      <span
                        key={k}
                        className="px-2 py-0.5 rounded-md bg-surface2 border border-line text-[11px] text-text2 font-mono"
                      >
                        {k}: {String(v)}
                      </span>
                    ))}
                  </div>
                )}

                {/* Note */}
                <div className="p-2.5 rounded-xl bg-surface2 text-text2 text-xs leading-relaxed">
                  {isApplicable
                    ? "Changes the brand's settings (quiet hours, daily cap, or best hours) when approved."
                    : "Info only: approving records the idea. Update brand settings manually if desired."}
                </div>

                {/* Actions / Decided footer */}
                {rec.status === "proposed" && (
                  <div className="flex items-center justify-end gap-2 pt-2 border-t border-line">
                    <button
                      type="button"
                      onClick={() => handleReject(rec)}
                      className="h-9 px-3.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium cursor-pointer transition-colors"
                    >
                      Reject
                    </button>
                    <button
                      type="button"
                      onClick={() => handleApprove(rec)}
                      className="h-9 px-3.5 rounded-lg bg-pri text-pri-fg hover:opacity-90 text-xs font-semibold cursor-pointer transition-opacity"
                    >
                      {isApplicable ? "Approve and apply" : "Approve (info only)"}
                    </button>
                  </div>
                )}

                {rec.decided_at && (
                  <div className="text-[11px] text-text2 pt-2 border-t border-line flex flex-wrap items-center justify-between gap-1">
                    <span>
                      Decided {new Date(rec.decided_at + "Z").toLocaleDateString("en-GB", {
                        day: "numeric",
                        month: "short",
                        year: "numeric",
                      })}{" "}
                      by {rec.decided_by || "Admin"}
                    </span>
                    {rec.apply_error && (
                      <span className="text-bad font-medium">Error: {rec.apply_error}</span>
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>

        {/* Right Column: Hermes Memory */}
        <aside
          aria-labelledby="mem-heading"
          className="lg:col-span-5 xl:col-span-4 bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5 sticky top-20"
        >
          <div className="flex items-center justify-between gap-2">
            <h2 id="mem-heading" className="text-[15px] font-semibold text-text">
              What Hermes knows
            </h2>
            <Icon icon="solar:database-bold-duotone" width={18} className="text-pri" />
          </div>

          <select
            aria-label="Brand Memory"
            value={memoryFanpageId}
            onChange={(e) =>
              setMemoryFanpageId(e.target.value ? Number(e.target.value) : "")
            }
            className="h-9 px-2.5 rounded-lg border border-line bg-surface2 text-text text-xs font-medium focus:outline-none"
          >
            <option value="">Select a brand…</option>
            {fanpages.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>

          {memoryFanpageId ? (
            <div className="flex flex-col gap-3.5 pt-1">
              <div className="flex items-center justify-between gap-2 text-xs">
                <button
                  type="button"
                  onClick={handleRefreshMemory}
                  disabled={memoryLoading}
                  className="h-8 px-2.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium inline-flex items-center gap-1.5 cursor-pointer disabled:opacity-60"
                >
                  <Icon
                    icon="solar:refresh-bold-duotone"
                    width={13}
                    className={memoryLoading ? "animate-spin" : ""}
                  />
                  <span>Refresh now</span>
                </button>
                <span className="text-[11px] text-text2">
                  {memory?.auto_updated_at
                    ? `Updated ${new Date(memory.auto_updated_at + "Z").toLocaleDateString(
                        "en-GB",
                        { day: "numeric", month: "short" }
                      )}`
                    : "Last 90 days"}
                </span>
              </div>

              {/* Best hours */}
              {memory?.auto && typeof memory.auto === "object" && "best_hours" in memory.auto && (
                <div className="space-y-1.5">
                  <span className="text-xs font-semibold text-text">
                    Best hours ({selectedFanpage?.timezone || "Brand time"})
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {Array.isArray((memory.auto as any).best_hours) &&
                      (memory.auto as any).best_hours.map((h: number) => (
                        <span
                          key={h}
                          className="px-2.5 py-0.5 rounded-full bg-pri-soft text-pri-text text-xs font-semibold"
                        >
                          {String(h).padStart(2, "0")}:00
                        </span>
                      ))}
                  </div>
                </div>
              )}

              {/* Hermes notes */}
              <div className="space-y-1.5">
                <span className="text-xs font-semibold text-text">Hermes notes</span>
                {memory?.notes ? (
                  <p className="text-xs text-text2 leading-relaxed bg-surface2 p-3 rounded-xl border border-line">
                    {typeof memory.notes === "string"
                      ? memory.notes
                      : JSON.stringify(memory.notes, null, 2)}
                  </p>
                ) : (
                  <p className="text-xs text-text2 italic">No notes written by Hermes yet.</p>
                )}
              </div>

              {/* Raw memory accordion */}
              <details className="border-t border-line pt-2 text-xs">
                <summary className="font-semibold text-text cursor-pointer hover:text-pri select-none">
                  Show raw memory (JSON)
                </summary>
                <pre className="mt-2 p-2.5 rounded-xl bg-surface2 border border-line font-mono text-[11px] text-text overflow-x-auto whitespace-pre-wrap max-h-56">
                  {JSON.stringify(memory?.auto ?? {}, null, 2)}
                </pre>
              </details>
            </div>
          ) : (
            <p className="text-xs text-text2 text-center py-6">
              Select a brand above to view its content memory and patterns.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
