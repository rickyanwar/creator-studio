"use client";

import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  getLogs,
  markLogHandled,
  unmarkLogHandled,
  type LogEntry,
} from "@/lib/logsApi";
import { useFanpages } from "@/lib/useFanpages";
import { Icon } from "@iconify/react";
import { useBrandFilter } from "@/contexts/BrandFilterContext";

type Category = "all" | "publish" | "ai" | "access";
type Severity = "all" | "error" | "warning";

const CATEGORY_TABS: { value: Category; label: string }[] = [
  { value: "all", label: "All" },
  { value: "publish", label: "Failed posts" },
  { value: "ai", label: "AI" },
  { value: "access", label: "Logins & access" },
];

const TYPE_META: Record<string, { icon: string; label: string }> = {
  challenge: { icon: "solar:shield-warning-bold-duotone", label: "Challenge" },
  ban: { icon: "solar:shield-cross-bold-duotone", label: "Banned" },
  rate_limit: { icon: "solar:danger-circle-bold-duotone", label: "Rate Limited" },
  session_error: { icon: "solar:key-bold-duotone", label: "Session Error" },
  no_session: { icon: "solar:lock-bold-duotone", label: "No Session" },
  publish_failed: { icon: "solar:close-circle-bold-duotone", label: "Failed Post" },
  ai_failed: { icon: "solar:cpu-bolt-bold-duotone", label: "AI Post Lost" },
  ai_recovered: { icon: "solar:refresh-circle-bold-duotone", label: "9Router Degraded" },
  login_failed: { icon: "solar:shield-warning-bold-duotone", label: "Failed Login" },
  login_success: { icon: "solar:shield-check-bold-duotone", label: "Login" },
};

const ACTION_LABEL: Record<string, string> = {
  challenge: "View",
  ban: "View",
  rate_limit: "View",
  session_error: "View",
  no_session: "View",
  publish_failed: "Open post",
  ai_failed: "AI settings",
  ai_recovered: "AI settings",
};

function timeAgo(iso: string) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function LogsPage() {
  const router = useRouter();
  const [category, setCategory] = useState<Category>("all");
  const [severity, setSeverity] = useState<Severity>("all");
  const [days, setDays] = useState(7);
  const { selectedBrandId, setSelectedBrandId } = useBrandFilter();
  const fanpageId = selectedBrandId ?? undefined;
  const [hideHandled, setHideHandled] = useState(true);
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});

  const { data: fanpages = [] } = useFanpages();

  const { data, isLoading, mutate } = useSWR(
    ["logs", category, days, fanpageId],
    () =>
      getLogs({
        category: category === "all" ? undefined : category,
        days,
        fanpage_id: fanpageId,
      }).then((r) => r.data),
    { refreshInterval: 30000 }
  );

  const handleToggleHandled = async (log: LogEntry) => {
    const key = `${log.entry_kind}-${log.entry_id}`;
    if (actionLoading[key]) return;
    setActionLoading((prev) => ({ ...prev, [key]: true }));

    const nextHandled = !log.handled;

    mutate(
      (current) => {
        if (!current) return current;
        return {
          ...current,
          logs: current.logs.map((item) =>
            item.entry_kind === log.entry_kind && item.entry_id === log.entry_id
              ? { ...item, handled: nextHandled }
              : item
          ),
          error_count:
            log.severity === "error"
              ? nextHandled
                ? Math.max(0, current.error_count - 1)
                : current.error_count + 1
              : current.error_count,
          warning_count:
            log.severity === "warning"
              ? nextHandled
                ? Math.max(0, current.warning_count - 1)
                : current.warning_count + 1
              : current.warning_count,
        };
      },
      false
    );

    try {
      if (nextHandled) {
        await markLogHandled(log.entry_kind, log.entry_id);
      } else {
        await unmarkLogHandled(log.entry_kind, log.entry_id);
      }
      await mutate();
    } catch {
      await mutate();
    } finally {
      setActionLoading((prev) => ({ ...prev, [key]: false }));
    }
  };

  const allLogs: LogEntry[] = data?.logs ?? [];
  const logs: LogEntry[] = allLogs.filter((l) => {
    if (hideHandled && l.handled) return false;
    if (severity !== "all" && l.severity !== severity) return false;
    return true;
  });

  const errorCount = data?.error_count ?? 0;
  const warningCount = data?.warning_count ?? 0;
  const aiStats = data?.ai_stats;

  return (
    <div className="space-y-5 max-w-[1240px]">
      {/* ── Page Header ────────────────────────────── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl sm:text-[26px] font-bold text-text tracking-tight">
            Logs and alerts
          </h1>
          <p className="text-sm text-text2">
            Failed posts, AI events and login problems. Mark each one handled when it is fixed.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <select
            aria-label="Period"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-medium focus:outline-none"
          >
            <option value={1}>Last 24 hours</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
          </select>
          <button
            type="button"
            onClick={() => mutate()}
            className="h-10 px-3.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-sm font-medium transition-colors inline-flex items-center gap-2 cursor-pointer"
          >
            <Icon icon="solar:refresh-bold-duotone" width={15} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* ── KPI Summary Cards ───────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="p-3.5 rounded-xl bg-surface border border-line flex flex-col justify-between gap-1">
          <span className="text-xs text-text2">Errors open</span>
          <span className="text-2xl font-bold text-bad tabular-nums">
            {isLoading ? "—" : errorCount}
          </span>
          <span className="text-[11px] text-text2">Unresolved error events</span>
        </div>

        <div className="p-3.5 rounded-xl bg-surface border border-line flex flex-col justify-between gap-1">
          <span className="text-xs text-text2">Warnings open</span>
          <span className="text-2xl font-bold text-warn tabular-nums">
            {isLoading ? "—" : warningCount}
          </span>
          <span className="text-[11px] text-text2">Degraded or retry events</span>
        </div>

        <div className="p-3.5 rounded-xl bg-surface border border-line flex flex-col justify-between gap-1">
          <span className="text-xs text-text2">AI calls ok</span>
          <span className="text-2xl font-bold text-text tabular-nums">
            {aiStats?.total ? `${aiStats.success_rate}%` : "100%"}
          </span>
          <span className="text-[11px] text-text2">
            {aiStats?.total
              ? `${aiStats.success} clean · ${aiStats.recovered} recovered · ${aiStats.failed} lost`
              : "No AI calls recorded"}
          </span>
        </div>
      </div>

      {/* ── Info Note Banner ────────────────────────── */}
      <div
        role="note"
        className="flex gap-2.5 items-start p-3.5 rounded-xl bg-info-soft text-info text-xs leading-relaxed border border-line"
      >
        <Icon icon="solar:info-circle-bold" width={18} className="shrink-0 mt-0.5" />
        <span>
          Channel and phone problems are not listed here. Uncertain posts, posts that need you and phone recovery go to Telegram and to the brand&apos;s attention list on{" "}
          <Link href="/brands" className="underline font-semibold text-info hover:opacity-80">
            Brands
          </Link>
          .
        </span>
      </div>

      {/* ── Filters Row ─────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Category Tabs */}
        <div
          role="tablist"
          aria-label="Category"
          className="flex items-center gap-1 p-1 rounded-xl bg-surface2"
        >
          {CATEGORY_TABS.map((c) => (
            <button
              key={c.value}
              type="button"
              role="tab"
              aria-selected={category === c.value}
              onClick={() => setCategory(c.value)}
              className={`h-8 px-3 rounded-lg text-xs font-semibold transition-colors cursor-pointer ${
                category === c.value
                  ? "bg-surface text-text shadow-sm"
                  : "text-text2 hover:text-text"
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>

        {/* Right filters: Brand select, Severity filter, Hide handled */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Brand select */}
          <select
            aria-label="Filter by brand"
            value={fanpageId ?? ""}
            onChange={(e) =>
              setSelectedBrandId(e.target.value ? Number(e.target.value) : null)
            }
            className="h-8 px-2.5 rounded-lg border border-line bg-surface text-text text-xs font-medium focus:outline-none"
          >
            <option value="">All brands</option>
            {fanpages.map((fp) => (
              <option key={fp.id} value={fp.id}>
                {fp.name}
              </option>
            ))}
          </select>

          {/* Severity filter */}
          <div role="group" aria-label="Severity" className="flex items-center gap-1">
            {(["all", "error", "warning"] as Severity[]).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSeverity(s)}
                aria-pressed={severity === s}
                className={`h-8 px-3 rounded-lg text-xs font-medium transition-colors cursor-pointer capitalize border ${
                  severity === s
                    ? s === "error"
                      ? "bg-bad-soft border-bad text-bad font-semibold"
                      : s === "warning"
                      ? "bg-warn-soft border-warn text-warn font-semibold"
                      : "bg-surface border-line text-text font-semibold shadow-sm"
                    : "bg-surface border-line text-text2 hover:text-text hover:bg-surface2"
                }`}
              >
                {s === "all" ? "All severities" : s === "error" ? "Errors" : "Warnings"}
              </button>
            ))}
          </div>

          {/* Hide handled toggle */}
          <button
            type="button"
            onClick={() => setHideHandled(!hideHandled)}
            aria-pressed={hideHandled}
            className={`h-8 px-2.5 rounded-lg text-xs font-medium transition-colors cursor-pointer inline-flex items-center gap-1.5 border ${
              hideHandled
                ? "bg-surface2 border-line text-text font-semibold shadow-sm"
                : "bg-surface border-line text-text2 hover:text-text hover:bg-surface2"
            }`}
          >
            <Icon
              icon={hideHandled ? "solar:eye-closed-bold-duotone" : "solar:eye-bold-duotone"}
              width={14}
            />
            <span>Hide handled</span>
          </button>
        </div>
      </div>

      {/* ── Log Entries List ────────────────────────── */}
      <section
        aria-label="Log entries"
        className="bg-surface border border-line rounded-[14px] overflow-hidden"
      >
        {isLoading ? (
          <div className="divide-y divide-line">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="flex gap-3.5 p-4 animate-pulse">
                <div className="w-9 h-9 rounded-xl bg-surface2 shrink-0" />
                <div className="flex-1 space-y-2">
                  <div className="h-3.5 bg-surface2 rounded w-1/4" />
                  <div className="h-3 bg-surface2 rounded w-2/3" />
                  <div className="h-3 bg-surface2 rounded w-1/6" />
                </div>
              </div>
            ))}
          </div>
        ) : logs.length === 0 ? (
          <div className="p-12 text-center text-text2 text-sm">
            <div className="w-10 h-10 rounded-full bg-pri-soft text-pri flex items-center justify-center mx-auto mb-2">
              <Icon icon="solar:shield-check-bold-duotone" width={22} />
            </div>
            <p className="font-semibold text-text">Nothing here</p>
            <p className="text-xs text-text2 mt-0.5">All clear for this filter.</p>
          </div>
        ) : (
          <ul className="divide-y divide-line m-0 p-0 list-none">
            {logs.map((log) => {
              const meta = TYPE_META[log.type] ?? {
                icon: "solar:danger-triangle-bold-duotone",
                label: log.type,
              };
              const actionLabel = ACTION_LABEL[log.type] ?? "View";
              const isError = log.severity === "error";
              const isWarning = log.severity === "warning";

              const badgeBg = isError
                ? "var(--bad-soft)"
                : isWarning
                ? "var(--warn-soft)"
                : "var(--info-soft)";
              const badgeFg = isError
                ? "var(--bad)"
                : isWarning
                ? "var(--warn)"
                : "var(--info)";

              const key = `${log.entry_kind}-${log.entry_id}`;
              const isActionBusy = !!actionLoading[key];

              return (
                <li
                  key={log.id}
                  className={`flex flex-wrap items-center gap-3.5 p-3.5 sm:p-4 hover:bg-surface2/50 transition-colors ${
                    log.handled ? "opacity-60" : ""
                  }`}
                >
                  {/* Icon avatar */}
                  <span
                    aria-hidden="true"
                    className="w-9 h-9 rounded-xl shrink-0 flex items-center justify-center"
                    style={{
                      background: badgeBg,
                      color: badgeFg,
                    }}
                  >
                    <Icon icon={meta.icon} width={18} />
                  </span>

                  {/* Body */}
                  <span className="flex-1 min-w-[280px] flex flex-col gap-0.5">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-text">{log.title}</span>
                      <span
                        className="px-2 py-0.5 rounded-md text-[11px] font-semibold"
                        style={{
                          background: badgeBg,
                          color: badgeFg,
                        }}
                      >
                        {meta.label}
                      </span>
                      {log.handled && (
                        <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-surface2 text-text2 border border-line">
                          Handled
                        </span>
                      )}
                    </span>
                    <span className="text-xs text-text2 leading-relaxed">{log.message}</span>
                    <span className="text-[11px] text-text2">
                      {log.account} · {timeAgo(log.occurred_at)}
                    </span>
                  </span>

                  {/* Actions */}
                  <span className="flex items-center gap-2 shrink-0">
                    {log.link && (
                      <button
                        type="button"
                        onClick={() => router.push(log.link!)}
                        className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium cursor-pointer transition-colors"
                      >
                        {actionLabel}
                      </button>
                    )}

                    {log.handled ? (
                      <button
                        type="button"
                        disabled={isActionBusy}
                        onClick={() => handleToggleHandled(log)}
                        className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-medium cursor-pointer transition-colors"
                      >
                        Undo
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={isActionBusy}
                        onClick={() => handleToggleHandled(log)}
                        className="h-8 px-3 rounded-lg border border-line bg-surface2 hover:bg-surface text-text text-xs font-medium cursor-pointer transition-colors"
                      >
                        Mark handled
                      </button>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
