"use client";

import { useState } from "react";
import useSWR from "swr";
import { getLogs, type ActivityLog } from "@/lib/api";
import { Icon } from "@iconify/react";
import { clsx } from "clsx";
import { useRouter } from "next/navigation";

type Category = "all" | "burner" | "publish";
type Severity  = "all" | "error" | "warning";

const CATEGORY_TABS: { value: Category; label: string; icon: string }[] = [
  { value: "all",     label: "All",     icon: "solar:list-bold-duotone" },
  { value: "burner",  label: "Burner",  icon: "solar:users-group-rounded-bold-duotone" },
  { value: "publish", label: "Publish", icon: "solar:rocket-bold-duotone" },
];

const TYPE_META: Record<string, { icon: string; color: string; label: string }> = {
  challenge:     { icon: "solar:shield-warning-bold-duotone", color: "#FFAB00", label: "Challenge" },
  ban:           { icon: "solar:shield-cross-bold-duotone",   color: "#FF5630", label: "Banned" },
  rate_limit:    { icon: "solar:danger-circle-bold-duotone",  color: "#FFAB00", label: "Rate Limited" },
  session_error: { icon: "solar:key-bold-duotone",            color: "#FF5630", label: "Session Error" },
  no_session:    { icon: "solar:lock-bold-duotone",           color: "#FFAB00", label: "No Session" },
  publish_failed:{ icon: "solar:close-circle-bold-duotone",   color: "#FF5630", label: "Publish Failed" },
};

const ACTION_LABEL: Record<string, string> = {
  challenge:      "Submit OTP",
  ban:            "View Burner",
  rate_limit:     "View Burner",
  session_error:  "Import Session",
  no_session:     "Import Session",
  publish_failed: "View History",
};

function timeAgo(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60)    return "just now";
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function LogsPage() {
  const router = useRouter();
  const [category, setCategory] = useState<Category>("all");
  const [severity, setSeverity]  = useState<Severity>("all");
  const [days, setDays]          = useState(7);

  const { data, isLoading, mutate } = useSWR(
    `logs-${category}-${days}`,
    () => getLogs({ category: category === "all" ? undefined : category, days }).then((r) => r.data),
    { refreshInterval: 30000 }
  );

  const logs: ActivityLog[] = (data?.logs ?? []).filter((l) =>
    severity === "all" ? true : l.severity === severity
  );

  const errorCount   = data?.error_count   ?? 0;
  const warningCount = data?.warning_count ?? 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Activity Logs</h1>
          <p className="text-sm text-text-secondary mt-0.5">
            Failed logins, banned accounts, publish errors
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Days filter */}
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="input-rect text-sm py-2 px-3 w-auto"
          >
            <option value={1}>Last 24h</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
          </select>
          <button onClick={() => mutate()} className="btn-ghost">
            <Icon icon="solar:refresh-bold-duotone" width={15} />
            Refresh
          </button>
        </div>
      </div>

      {/* Summary chips */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-[rgba(255,86,48,0.08)]">
          <div className="w-2 h-2 rounded-full bg-[#FF5630]" />
          <span className="text-sm font-semibold text-[#FF5630]">{errorCount} Error{errorCount !== 1 ? "s" : ""}</span>
        </div>
        <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-[rgba(255,171,0,0.08)]">
          <div className="w-2 h-2 rounded-full bg-[#FFAB00]" />
          <span className="text-sm font-semibold text-[#B76E00]">{warningCount} Warning{warningCount !== 1 ? "s" : ""}</span>
        </div>
        {errorCount === 0 && warningCount === 0 && !isLoading && (
          <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-[rgba(0,167,111,0.08)]">
            <Icon icon="solar:check-circle-bold-duotone" width={16} className="text-primary-main" />
            <span className="text-sm font-semibold text-primary-main">All systems healthy</span>
          </div>
        )}
      </div>

      {/* Filters row */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        {/* Category tabs */}
        <div className="flex items-center gap-1 p-1 rounded-lg bg-bg-paper-hover">
          {CATEGORY_TABS.map(({ value, label, icon }) => (
            <button
              key={value}
              onClick={() => setCategory(value)}
              className={clsx(
                "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-semibold transition-colors",
                category === value
                  ? "bg-bg-paper text-text-primary shadow-sm"
                  : "text-text-secondary hover:text-text-primary"
              )}
            >
              <Icon icon={icon} width={14} />
              {label}
            </button>
          ))}
        </div>

        {/* Severity filter */}
        <div className="flex items-center gap-1">
          {(["all", "error", "warning"] as Severity[]).map((s) => (
            <button
              key={s}
              onClick={() => setSeverity(s)}
              className={clsx(
                "px-3 py-1.5 rounded-full text-xs font-semibold capitalize transition-colors",
                severity === s
                  ? s === "error"   ? "bg-[#FF5630] text-white"
                  : s === "warning" ? "bg-[#FFAB00] text-white"
                  :                   "bg-text-primary text-bg-paper"
                  : "text-text-secondary hover:bg-bg-paper-hover"
              )}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Log list */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? (
          <div className="divide-y divide-divider-soft">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="flex gap-4 p-5 animate-pulse">
                <div className="w-10 h-10 rounded-full bg-bg-paper-hover flex-shrink-0" />
                <div className="flex-1 space-y-2">
                  <div className="h-3.5 bg-bg-paper-hover rounded w-1/3" />
                  <div className="h-3 bg-bg-paper-hover rounded w-2/3" />
                  <div className="h-3 bg-bg-paper-hover rounded w-1/4" />
                </div>
              </div>
            ))}
          </div>
        ) : logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="w-16 h-16 rounded-full bg-[rgba(0,167,111,0.08)] flex items-center justify-center mb-4">
              <Icon icon="solar:shield-check-bold-duotone" width={32} className="text-primary-main" />
            </div>
            <p className="text-base font-semibold text-text-primary">No issues found</p>
            <p className="text-sm text-text-secondary mt-1">Everything is running smoothly.</p>
          </div>
        ) : (
          <div className="divide-y divide-divider-soft">
            {logs.map((log) => {
              const meta = TYPE_META[log.type] ?? {
                icon: "solar:info-circle-bold-duotone",
                color: "#919EAB",
                label: log.type,
              };
              const actionLabel = ACTION_LABEL[log.type] ?? "View";

              return (
                <div
                  key={log.id}
                  className="flex items-start gap-4 p-5 hover:bg-bg-paper-hover transition-colors group"
                >
                  {/* Icon avatar */}
                  <div
                    className="w-10 h-10 rounded-full flex-shrink-0 flex items-center justify-center mt-0.5"
                    style={{ background: `${meta.color}18` }}
                  >
                    <Icon icon={meta.icon} width={20} style={{ color: meta.color }} />
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-text-primary">{log.title}</p>
                        <p className="text-xs text-text-secondary mt-0.5 leading-relaxed">{log.message}</p>
                      </div>
                      {/* Severity badge */}
                      <span
                        className={clsx(
                          "flex-shrink-0 text-[11px] font-bold px-2 py-0.5 rounded-full",
                          log.severity === "error"
                            ? "bg-[rgba(255,86,48,0.12)] text-[#FF5630]"
                            : "bg-[rgba(255,171,0,0.12)] text-[#B76E00]"
                        )}
                      >
                        {log.severity}
                      </span>
                    </div>

                    {/* Meta row */}
                    <div className="flex items-center gap-3 mt-2 flex-wrap">
                      <span
                        className="text-[11px] font-semibold px-2 py-0.5 rounded"
                        style={{ background: `${meta.color}14`, color: meta.color }}
                      >
                        {meta.label}
                      </span>
                      <span className="flex items-center gap-1 text-[11px] text-text-disabled">
                        <Icon icon="solar:user-bold-duotone" width={11} />
                        {log.account}
                      </span>
                      <span className="flex items-center gap-1 text-[11px] text-text-disabled">
                        <Icon icon="solar:clock-circle-bold-duotone" width={11} />
                        {timeAgo(log.occurred_at)}
                      </span>
                    </div>
                  </div>

                  {/* Action */}
                  <button
                    onClick={() => router.push(log.link)}
                    className="flex-shrink-0 text-xs font-semibold px-3 py-1.5 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity"
                    style={{ background: "var(--text-primary)", color: "var(--bg-paper)" }}
                  >
                    {actionLabel}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
