"use client";

import useSWR from "swr";
import { listIGSources, listBurners, assignBurnerToSource, deleteIGSource, autoAssignBurners, getCrawlerHealth, getScraperHealth } from "@/lib/api";
import { format, formatDistanceToNowStrict } from "date-fns";
import type { CrawlerHealth, ScraperHealth } from "@/lib/types";
import { useState } from "react";
import { Icon } from "@iconify/react";

type IGSourceRow = {
  id: number;
  ig_username: string;
  burner_id: number | null;
  burner_username: string | null;
  burner_status: string | null;
  is_active: boolean;
  last_checked_at: string | null;
  active_fanpage_count: number;
  last_crawl_error: string | null;
};

type Burner = {
  id: number;
  ig_username: string;
  status: string;
};

const viewerTiers = [
  ["gramsnap", "GramSnap"],
  ["anonyig", "AnonyIG"],
  ["igstoryviewer", "IGStoryViewer"],
] as const;

const tierStyles = {
  healthy: { badge: "badge-green", icon: "solar:check-circle-bold-duotone", text: "OK" },
  degraded: { badge: "badge-yellow", icon: "solar:danger-circle-bold-duotone", text: "Failing" },
  unhealthy: { badge: "badge-red", icon: "solar:danger-triangle-bold-duotone", text: "Broken" },
  unknown: { badge: "badge-gray", icon: "solar:question-circle-bold-duotone", text: "No data yet" },
} as const;

export default function SourcesPage() {
  const { data: sources = [], isLoading, mutate } = useSWR<IGSourceRow[]>(
    "ig-sources",
    () => listIGSources().then((r) => r.data as IGSourceRow[]),
    { refreshInterval: 30000 }
  );
  const { data: burnersData } = useSWR<{ burners: Burner[] }>(
    "burners-list",
    () => listBurners().then((r) => r.data)
  );
  const { data: health } = useSWR<CrawlerHealth>(
    "crawler-health",
    () => getCrawlerHealth().then((r) => r.data),
    { refreshInterval: 30000 }
  );
  const { data: scraperHealth, error: scraperHealthError } = useSWR(
    "scraper-health",
    () => getScraperHealth().then((r) => r.data),
    { refreshInterval: 60000 }
  );

  const burners: Burner[] = burnersData?.burners ?? (Array.isArray(burnersData) ? burnersData as Burner[] : []);
  const activeBurners = burners.filter((b) => b.status === "active");

  const [assigning, setAssigning] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [autoAssigning, setAutoAssigning] = useState(false);
  const orphans = sources.filter((s) => s.active_fanpage_count === 0);

  async function handleDelete(sourceId: number, username: string) {
    if (!confirm(`Delete @${username}? This cannot be undone.`)) return;
    setDeleting(sourceId);
    try {
      await deleteIGSource(sourceId);
      mutate();
    } finally {
      setDeleting(null);
    }
  }

  async function handleAutoAssign() {
    setAutoAssigning(true);
    try {
      const res = await autoAssignBurners();
      mutate();
      const count = res.data?.reassigned?.length ?? 0;
      alert(count > 0 ? `Reassigned ${count} source(s) to active burners.` : "All sources already have active burners.");
    } catch {
      alert("Auto-assign failed.");
    } finally {
      setAutoAssigning(false);
    }
  }

  async function handleAssign(sourceId: number, burnerId: string) {
    setAssigning(sourceId);
    try {
      await assignBurnerToSource(sourceId, burnerId ? parseInt(burnerId) : null);
      mutate();
    } finally {
      setAssigning(null);
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1
            className="text-display-md text-ink"
            style={{ fontFamily: "'SF Pro Display', system-ui, sans-serif" }}
          >
            Instagram Sources
          </h1>
          <p className="text-caption text-ink-48 mt-1">
            {sources.length} total sources
            {orphans.length > 0 && (
              <span className="ml-2 text-amber-600">{orphans.length} orphaned (not linked to any fanpage)</span>
            )}
          </p>
        </div>
        {activeBurners.length > 0 && (
          <button
            onClick={handleAutoAssign}
            disabled={autoAssigning}
            className="btn btn-secondary flex items-center gap-2 shrink-0"
          >
            {autoAssigning
              ? <Icon icon="svg-spinners:ring-resize" width={14} />
              : <Icon icon="solar:refresh-bold-duotone" width={14} />}
            Auto-assign Burners
          </button>
        )}
      </div>

      {scraperHealth?.scraper_mode === "viewer" ? (
        <div className="rounded-xl border border-info-main/30 bg-info-lighter p-4 flex items-start gap-3 text-info-darker">
          <Icon icon="solar:info-circle-bold-duotone" className="mt-0.5 shrink-0" width={18} />
          <p className="text-sm">Web Viewer mode is on — burner accounts are not needed.</p>
        </div>
      ) : activeBurners.length === 0 && !isLoading && scraperHealth?.scraper_mode === "auto" ? (
        <div className="rounded-xl border border-info-main/30 bg-info-lighter p-4 flex items-start gap-3 text-info-darker">
          <Icon icon="solar:info-circle-bold-duotone" className="mt-0.5 shrink-0" width={18} />
          <p className="text-sm">No active burner accounts — the crawler uses the Web Viewer automatically (GramSnap → AnonyIG → IGStoryViewer).</p>
        </div>
      ) : activeBurners.length === 0 && !isLoading && scraperHealth?.scraper_mode === "instagrapi" ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 dark:border-warning-main/30 dark:bg-warning-lighter p-4 flex items-start gap-3">
          <Icon icon="solar:danger-triangle-bold-duotone" className="text-amber-500 mt-0.5 shrink-0" width={18} />
          <p className="text-sm text-amber-800 dark:text-warning-darker">
            No active burner accounts found. Go to{" "}
            <a href="/burners" className="font-semibold underline">Burners</a>{" "}
            and import a session first, then come back to assign burners to sources.
          </p>
        </div>
      ) : null}

      <section className="card-sm space-y-3" aria-labelledby="scraper-health-heading">
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 id="scraper-health-heading" className="font-semibold text-ink">Scraper health</h2>
          <span className="text-xs text-ink-48">Mode: {scraperHealth?.scraper_mode === "viewer" ? "Web Viewer" : scraperHealth?.scraper_mode === "instagrapi" ? "Instagrapi" : scraperHealth?.scraper_mode === "auto" ? "Auto" : "—"}</span>
        </div>
        {scraperHealthError || scraperHealth?.available === false ? (
          <p className="text-caption text-ink-48">Scraper health data is not available yet.</p>
        ) : scraperHealth?.available ? (
          <div className="divide-y divide-hairline">
            {viewerTiers.map(([key, name]) => {
              const tier = scraperHealth.tiers[key];
              if (!tier) return null;
              const style = tierStyles[tier.status];
              return (
                <div key={key} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-caption">
                  <span className="w-28 font-medium text-ink">{name}</span>
                  <span className={`${style.badge} gap-1`}>
                    <Icon icon={style.icon} width={14} aria-hidden="true" />
                    {tier.status === "degraded" ? `Failing ${tier.consecutive_failures}×` : style.text}
                  </span>
                  <span className="text-ink-48">Last success: {tier.last_success_at ? formatDistanceToNowStrict(new Date(tier.last_success_at), { addSuffix: true }) : "—"}</span>
                  {tier.consecutive_failures > 0 && (
                    <>
                      <span className="text-ink-48">{tier.consecutive_failures} failures in a row ({tier.distinct_users} accounts)</span>
                      <span className="max-w-[220px] truncate text-ink-48" title={`${tier.last_error_kind}: ${tier.last_error}`}>
                        {tier.last_error_kind}{tier.last_error ? `: ${tier.last_error}` : ""}
                      </span>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="text-caption text-ink-48">Loading scraper health…</p>
        )}
      </section>

      {isLoading ? (
        <div className="text-caption text-ink-48">Loading…</div>
      ) : (
        <div className="card overflow-hidden p-0">
          <table className="w-full text-caption">
            <thead className="bg-parchment border-b border-hairline">
              <tr>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">IG Username</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Assigned Burner</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Fanpages</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Last Checked</th>
                <th className="px-5 py-3 text-left text-ink-80 font-semibold">Status</th>
                <th className="px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {sources.map((s) => (
                <tr key={s.id} className="hover:bg-parchment/50 transition-colors">
                  <td className="px-5 py-3 text-ink font-medium">@{s.ig_username}</td>
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2">
                      <select
                        className="input-rect py-1 text-xs min-w-[160px]"
                        value={s.burner_id ?? ""}
                        disabled={assigning === s.id}
                        onChange={(e) => handleAssign(s.id, e.target.value)}
                      >
                        <option value="">— No burner —</option>
                        {activeBurners.map((b) => (
                          <option key={b.id} value={b.id}>
                            @{b.ig_username}
                          </option>
                        ))}
                      </select>
                      {assigning === s.id && (
                        <Icon icon="svg-spinners:ring-resize" className="text-primary-main" width={14} />
                      )}
                      {s.burner_status && s.burner_status !== "active" && (
                        <span className="badge badge-yellow text-[10px]">{s.burner_status}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    {s.active_fanpage_count > 0 ? (
                      <span className="badge-green">{s.active_fanpage_count} active</span>
                    ) : (
                      <span className="badge-yellow">Orphaned</span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    {s.last_checked_at ? (() => {
                      const date = new Date(s.last_checked_at);
                      const minsAgo = Math.floor((Date.now() - date.getTime()) / 60000);
                      const stale = !health?.in_sleep_window && minsAgo > (health?.crawl_interval_minutes ?? 10) * 2;
                      return (
                        <div className="flex items-center gap-2">
                          <span className="text-ink-48" title={format(date, "MMM d HH:mm")}>
                            {formatDistanceToNowStrict(date, { addSuffix: true })}
                          </span>
                          {stale && (
                            <span className="text-[10px] font-medium text-error-main bg-[rgba(255,86,48,0.1)] px-1.5 py-0.5 rounded-full">
                              stale
                            </span>
                          )}
                        </div>
                      );
                    })() : (
                      <span className="text-ink-48">Never</span>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex flex-col gap-1">
                      <span className={s.is_active ? "badge-green" : "badge-gray"}>
                        {s.is_active ? "Active" : "Inactive"}
                      </span>
                      {s.last_crawl_error && (
                        <span
                          className="text-[10px] font-medium text-error-main bg-[rgba(255,86,48,0.1)] px-1.5 py-0.5 rounded-full truncate max-w-[180px]"
                          title={s.last_crawl_error}
                        >
                          {s.last_crawl_error}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-5 py-3">
                    <button
                      onClick={() => handleDelete(s.id, s.ig_username)}
                      disabled={deleting === s.id}
                      className="text-ink-48 hover:text-red-500 transition-colors"
                      title="Delete source"
                    >
                      {deleting === s.id
                        ? <Icon icon="svg-spinners:ring-resize" width={14} />
                        : <Icon icon="solar:trash-bin-trash-bold-duotone" width={16} />}
                    </button>
                  </td>
                </tr>
              ))}
              {sources.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-5 py-10 text-center text-ink-48">
                    No IG sources yet. Add them via the{" "}
                    <a href="/fanpages" className="text-primary">Fanpages</a> configure page.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
