"use client";

import { Suspense, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { format, formatDistanceToNowStrict } from "date-fns";
import { Icon } from "@iconify/react";
import {
  listIGSources,
  deleteIGSource,
  getCrawlerHealth,
  getScraperHealth,
  triggerCrawl,
} from "@/lib/api";
import type { CrawlerHealth } from "@/lib/types";
import { NewsSourcesPanel } from "@/components/sources/NewsSourcesPanel";

type IGSourceRow = {
  id: number;
  ig_username: string;
  is_active: boolean;
  last_checked_at: string | null;
  active_fanpage_count: number;
  last_crawl_error: string | null;
};

const viewerTiers = [
  ["gramsnap", "GramSnap"],
  ["anonyig", "AnonyIG"],
  ["igstoryviewer", "IGStoryViewer"],
] as const;

const tierStyles = {
  healthy: { className: "bg-pri-soft text-pri-text", text: "OK" },
  degraded: { className: "bg-warn-soft text-warn", text: "Failing" },
  unhealthy: { className: "bg-bad-soft text-bad", text: "Broken" },
  unknown: { className: "bg-surface2 text-text2", text: "No data yet" },
} as const;

export default function SourcesPageWrapper() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-text2">Loading sources…</div>}>
      <SourcesPage />
    </Suspense>
  );
}

function SourcesPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialTab = searchParams.get("tab") === "news" ? "news" : "ig";

  const [activeTab, setActiveTab] = useState<"ig" | "news">(initialTab);
  const [, startTransition] = useTransition();

  const handleTabChange = (tab: "ig" | "news") => {
    setActiveTab(tab);
    startTransition(() => {
      if (tab === "news") {
        router.replace("/sources?tab=news");
      } else {
        router.replace("/sources");
      }
    });
  };

  // ── IG Sources State & API ──
  const { data: sources = [], isLoading, mutate } = useSWR<IGSourceRow[]>(
    "ig-sources",
    () => listIGSources().then((r) => r.data as IGSourceRow[]),
    { refreshInterval: 30000 }
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

  const [deleting, setDeleting] = useState<number | null>(null);
  const [crawling, setCrawling] = useState(false);
  const [crawlStarted, setCrawlStarted] = useState(false);

  // Filters for IG
  const [searchQuery, setSearchQuery] = useState("");
  const [onlyProblems, setOnlyProblems] = useState(false);
  const [notUsedOnly, setNotUsedOnly] = useState(false);

  // News Sources Panel state (for header Add button & tab count)
  const [newsCount, setNewsCount] = useState<number | null>(null);
  const [newsFormOpen, setNewsFormOpen] = useState(false);

  const orphans = sources.filter((s) => s.active_fanpage_count === 0);

  const filteredSources = sources.filter((s) => {
    if (searchQuery) {
      const q = searchQuery.toLowerCase().replace(/^@/, "");
      if (!s.ig_username.toLowerCase().includes(q)) return false;
    }
    if (notUsedOnly && s.active_fanpage_count > 0) return false;
    if (onlyProblems) {
      const hasError = !!s.last_crawl_error;
      const isOrphan = s.active_fanpage_count === 0;
      let isStale = false;
      if (s.last_checked_at) {
        const minsAgo = Math.floor((Date.now() - new Date(s.last_checked_at).getTime()) / 60000);
        isStale = !health?.in_sleep_window && minsAgo > (health?.crawl_interval_minutes ?? 10) * 2;
      }
      if (!hasError && !isOrphan && !isStale) return false;
    }
    return true;
  });

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

  async function handleCrawl() {
    setCrawling(true);
    try {
      await triggerCrawl();
      setCrawlStarted(true);
    } catch (err: any) {
      alert("Crawl trigger failed: " + (err.response?.data?.detail || err.message));
    } finally {
      setCrawling(false);
    }
  }

  return (
    <div className="space-y-5 max-w-[1240px] w-full">
      {/* ── Page Header ── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-[26px] font-bold text-text tracking-tight">Sources</h1>
          <p className="text-sm text-text2">Instagram accounts and news sites that brands link to.</p>
          <p className="text-[13px] text-text2">
            YouTube, Pinterest and FB-photo sources live in{" "}
            <Link href="/brands" className="text-pri-text hover:underline">
              each brand&apos;s settings
            </Link>
            . Viral Radar accounts are on the{" "}
            <Link href="/radar" className="text-pri-text hover:underline">
              Viral radar
            </Link>{" "}
            page.
          </p>
        </div>

        {activeTab === "ig" && (
          <button
            type="button"
            onClick={handleCrawl}
            disabled={crawling}
            className="h-10 px-4 rounded-xl bg-pri text-pri-fg text-sm font-semibold hover:opacity-95 transition-opacity disabled:opacity-50 inline-flex items-center gap-2"
          >
            {crawling ? <Icon icon="svg-spinners:ring-resize" width={16} /> : null}
            <span>Crawl IG sources</span>
          </button>
        )}

        {activeTab === "news" && (
          <button
            type="button"
            onClick={() => setNewsFormOpen(true)}
            className="h-10 px-4 rounded-xl bg-pri text-pri-fg text-sm font-semibold hover:opacity-95 transition-opacity inline-flex items-center gap-1.5"
          >
            <Icon icon="solar:add-circle-bold-duotone" width={18} />
            <span>Add news site</span>
          </button>
        )}
      </div>

      {/* ── Tab Switcher ── */}
      <div role="tablist" aria-label="Source type" className="self-start flex gap-1 p-1 rounded-xl bg-surface2 w-fit">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "ig"}
          onClick={() => handleTabChange("ig")}
          className={`flex items-center gap-2 h-9 px-3.5 rounded-lg text-[13px] font-semibold transition-colors ${
            activeTab === "ig" ? "bg-surface text-text shadow-sm" : "text-text2 hover:text-text"
          }`}
        >
          <span>Instagram</span>
          <span className="text-xs text-text2">{sources.length}</span>
        </button>

        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "news"}
          onClick={() => handleTabChange("news")}
          className={`flex items-center gap-2 h-9 px-3.5 rounded-lg text-[13px] font-semibold transition-colors ${
            activeTab === "news" ? "bg-surface text-text shadow-sm" : "text-text2 hover:text-text"
          }`}
        >
          <span>News sites</span>
          {newsCount !== null && <span className="text-xs text-text2">{newsCount}</span>}
        </button>
      </div>

      {/* ── Instagram Tab ── */}
      {activeTab === "ig" && (
        <div className="space-y-4">
          {/* Crawler health section */}
          <section className="bg-surface border border-line rounded-xl p-4 sm:p-5 space-y-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-[15px] font-semibold text-text">Crawler health</h2>
              <span className="text-[13px] text-text2">Login-free web viewers, tried in order</span>
            </div>
            <p className="text-[13px] text-text2 leading-relaxed">
              The crawl is one pass over every active account: every 30 min, paused 01:00–06:00 WIB. Crawl IG sources starts a pass now.
            </p>

            {crawlStarted && (
              <p
                role="status"
                className="p-3 rounded-xl bg-pri-soft text-pri-text text-[13px] leading-relaxed border border-pri/20"
              >
                Crawl started: one pass over every active account. New posts show up in the queue when the Instagram setting is Review first.
              </p>
            )}

            {scraperHealthError || scraperHealth?.available === false ? (
              <p className="text-xs text-text2">Scraper health data is not available yet.</p>
            ) : scraperHealth?.available ? (
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
                {viewerTiers.map(([key, name]) => {
                  const tier = scraperHealth.tiers[key as keyof typeof scraperHealth.tiers];
                  if (!tier) return null;
                  const style = tierStyles[tier.status];
                  const lastFailure = tier.events?.find((e) => !e.ok);
                  const fallbackReason = tier.last_error_kind + (tier.last_error ? `: ${tier.last_error}` : "");
                  return (
                    <div key={key} className="p-3 rounded-xl bg-surface2 flex flex-col gap-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-text text-sm">{name}</span>
                        <span
                          className={`px-2 py-0.5 rounded-full text-xs font-semibold ${style.className}`}
                        >
                          {tier.status === "degraded" ? `Failing ${tier.consecutive_failures}×` : style.text}
                        </span>
                      </div>
                      <span className="text-xs text-text2">
                        {tier.last_success_at
                          ? `Last success ${formatDistanceToNowStrict(new Date(tier.last_success_at), { addSuffix: true })}`
                          : "No success recorded"}
                      </span>
                      {tier.consecutive_failures > 0 && tier.last_failure_username && (
                        <span className="text-xs text-bad">
                          Last fail @{tier.last_failure_username} — {lastFailure ? lastFailure.reason : fallbackReason}
                        </span>
                      )}

                      <details className="group mt-1">
                        <summary className="cursor-pointer text-[11px] font-medium text-pri-text hover:underline select-none inline-flex items-center gap-1">
                          <Icon icon="solar:alt-arrow-right-bold-duotone" className="group-open:rotate-90 transition-transform" />
                          Recent attempts
                        </summary>
                        <div className="mt-1.5 space-y-1 pl-2 border-l border-line max-h-40 overflow-y-auto text-[11px]">
                          {tier.events && tier.events.length > 0 ? (
                            tier.events.map((ev, i) => (
                              <div key={i} className="flex items-center gap-1.5">
                                <span className="text-text2 tabular-nums">
                                  {formatDistanceToNowStrict(new Date(ev.at))}
                                </span>
                                <span className="font-medium truncate max-w-[80px]">@{ev.username}</span>
                                <span className={`text-[10px] px-1 rounded ${ev.ok ? "text-pri-text bg-pri-soft" : "text-bad bg-bad-soft"}`}>
                                  {ev.ok ? "OK" : "Fail"}
                                </span>
                              </div>
                            ))
                          ) : (
                            <span className="text-text2 italic">None</span>
                          )}
                        </div>
                      </details>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-xs text-text2">Loading scraper health…</p>
            )}
          </section>

          {/* Filters */}
          <div className="flex flex-wrap gap-2 items-center">
            <input
              type="search"
              aria-label="Search accounts"
              placeholder="Search @username"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-9 w-56 px-3 rounded-xl border border-line bg-surface text-text text-[13px] focus:outline-none focus:ring-1 focus:ring-pri"
            />
            <label className="flex items-center gap-2 h-9 px-3 rounded-xl border border-line bg-surface text-[13px] text-text cursor-pointer select-none">
              <input
                type="checkbox"
                checked={onlyProblems}
                onChange={(e) => setOnlyProblems(e.target.checked)}
                className="w-4 h-4 rounded text-pri focus:ring-pri"
              />
              Only problems
            </label>
            <label className="flex items-center gap-2 h-9 px-3 rounded-xl border border-line bg-surface text-[13px] text-text cursor-pointer select-none">
              <input
                type="checkbox"
                checked={notUsedOnly}
                onChange={(e) => setNotUsedOnly(e.target.checked)}
                className="w-4 h-4 rounded text-pri focus:ring-pri"
              />
              Not used by a brand ({orphans.length})
            </label>
          </div>

          {/* Accounts Table */}
          <section className="bg-surface border border-line rounded-xl overflow-hidden">
            {isLoading ? (
              <div className="p-8 text-center text-sm text-text2">Loading accounts…</div>
            ) : filteredSources.length === 0 ? (
              <div className="p-10 text-center text-text2 space-y-2">
                <p className="text-sm">
                  {sources.length === 0
                    ? "No IG sources yet. Add them in each brand's settings."
                    : "No accounts match current filters."}
                </p>
                {sources.length === 0 && (
                  <Link href="/brands" className="inline-block text-xs font-semibold text-pri-text hover:underline">
                    Go to Brands
                  </Link>
                )}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] border-collapse text-sm">
                  <thead>
                    <tr className="text-left text-xs text-text2 border-b border-line bg-surface2/50">
                      <th scope="col" className="py-2.5 px-4 font-semibold">Account</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">Used by</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">Last checked</th>
                      <th scope="col" className="py-2.5 px-3 font-semibold">Status</th>
                      <th scope="col" className="py-2.5 px-4 font-semibold text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {filteredSources.map((s) => {
                      let isStale = false;
                      if (s.last_checked_at) {
                        const minsAgo = Math.floor((Date.now() - new Date(s.last_checked_at).getTime()) / 60000);
                        isStale = !health?.in_sleep_window && minsAgo > (health?.crawl_interval_minutes ?? 10) * 2;
                      }

                      return (
                        <tr key={s.id} className="hover:bg-surface2/40 transition-colors">
                          <td className="py-3 px-4 font-semibold text-text">
                            <a
                              href={`https://instagram.com/${s.ig_username}`}
                              target="_blank"
                              rel="noreferrer"
                              className="hover:text-pri-text"
                            >
                              @{s.ig_username}
                            </a>
                          </td>
                          <td className="py-3 px-3 text-[13px] text-text2">
                            {s.active_fanpage_count > 0 ? (
                              <span className="text-text font-medium">{s.active_fanpage_count} active</span>
                            ) : (
                              <span className="text-warn font-medium">No brand</span>
                            )}
                          </td>
                          <td className="py-3 px-3 text-[13px] text-text2 whitespace-nowrap">
                            {s.last_checked_at ? (
                              <div className="flex items-center gap-1.5">
                                <span title={format(new Date(s.last_checked_at), "MMM d HH:mm")}>
                                  {formatDistanceToNowStrict(new Date(s.last_checked_at), { addSuffix: true })}
                                </span>
                                {isStale && (
                                  <span className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold bg-warn-soft text-warn">
                                    stale
                                  </span>
                                )}
                              </div>
                            ) : (
                              <span>Never</span>
                            )}
                          </td>
                          <td className="py-3 px-3">
                            <div className="flex flex-col gap-1 items-start">
                              <span
                                className={`px-2 py-0.5 rounded-full text-xs font-semibold ${
                                  s.is_active
                                    ? "bg-pri-soft text-pri-text"
                                    : "bg-surface2 text-text2 border border-line"
                                }`}
                              >
                                {s.is_active ? "Active" : "Paused"}
                              </span>
                              {s.last_crawl_error && (
                                <span
                                  className="text-[11px] text-bad bg-bad-soft px-1.5 py-0.5 rounded max-w-[220px] truncate"
                                  title={s.last_crawl_error}
                                >
                                  {s.last_crawl_error}
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="py-3 px-4 text-right whitespace-nowrap">
                            <button
                              type="button"
                              onClick={() => handleDelete(s.id, s.ig_username)}
                              disabled={deleting === s.id}
                              title="Delete source"
                              className="h-8 px-2.5 rounded-lg border border-line bg-surface hover:bg-bad-soft text-text2 hover:text-bad text-xs font-medium transition-colors inline-flex items-center gap-1"
                            >
                              {deleting === s.id ? (
                                <Icon icon="svg-spinners:ring-resize" width={14} />
                              ) : (
                                <Icon icon="solar:trash-bin-trash-bold-duotone" width={15} />
                              )}
                              <span>Delete</span>
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <p className="text-[13px] text-text2">
            Add Instagram accounts in a brand&apos;s settings (Instagram mode). One account can feed several brands.
          </p>
        </div>
      )}

      {/* ── News Sites Tab ── */}
      {activeTab === "news" && (
        <NewsSourcesPanel
          showForm={newsFormOpen}
          setShowForm={setNewsFormOpen}
          onCountChange={setNewsCount}
        />
      )}
    </div>
  );
}
