"use client";

import { useState, useMemo, useEffect } from "react";
import useSWR from "swr";
import Link from "next/link";
import {
  getCrawlerHealth,
  triggerCrawl,
  triggerFanpageSync,
  restartBeat,
} from "@/lib/api";
import {
  getDashboardStats,
  type ExtendedDashboardStats,
  type BrandToday,
  type LatestPost,
} from "@/lib/dashboardApi";
import type { CrawlerHealth } from "@/lib/types";
import { Icon } from "@iconify/react";
import { ModeChip } from "@/components/ui/ModeChip";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { useBrandFilter } from "@/contexts/BrandFilterContext";

const BRAND_PALETTES = [
  "#C62828",
  "#1D4ED8",
  "#7C3AED",
  "#0F766E",
  "#D97706",
  "#2563EB",
  "#059669",
  "#EA580C",
];

function getBrandColor(id: number) {
  return BRAND_PALETTES[Math.abs(id) % BRAND_PALETTES.length];
}

function fmtTimeAgo(isoString: string | null | undefined): string {
  if (!isoString) return "";
  try {
    const diffMs = Date.now() - new Date(isoString).getTime();
    if (diffMs < 0) return "just now";
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } catch {
    return "";
  }
}

function formatNextPost(isoString: string | null | undefined, tz: string): string {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    return d.toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: tz || undefined,
    });
  } catch {
    return "—";
  }
}

const fetcher = () =>
  getDashboardStats().then((r: { data: ExtendedDashboardStats }) => r.data);
const healthFetcher = () => getCrawlerHealth().then((r: { data: CrawlerHealth }) => r.data);

export default function DashboardPage() {
  const { data, isLoading, mutate } = useSWR("dashboard-stats", fetcher, {
    refreshInterval: 30000,
  });
  const { data: health, mutate: mutateHealth } = useSWR("crawler-health", healthFetcher, {
    refreshInterval: 30000,
  });
  const { selectedBrandId } = useBrandFilter();

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  const [loadingSync, setLoadingSync] = useState(false);
  const [loadingCrawl, setLoadingCrawl] = useState(false);
  const [crawlMsg, setCrawlMsg] = useState<string | null>(null);
  const [restartingBeat, setRestartingBeat] = useState(false);
  const [restartMsg, setRestartMsg] = useState<string | null>(null);

  const diskPct =
    data && data.disk_total_mb > 0
      ? Math.round((data.disk_used_mb / data.disk_total_mb) * 100)
      : 0;

  async function handleSync() {
    setLoadingSync(true);
    try {
      await triggerFanpageSync();
      mutate();
    } finally {
      setLoadingSync(false);
    }
  }

  async function handleRestartBeat() {
    setRestartingBeat(true);
    setRestartMsg(null);
    try {
      await restartBeat();
      setRestartMsg("Beat restart requested — restarts within ~30s.");
      setTimeout(() => mutateHealth(), 45000);
    } catch {
      setRestartMsg("Restart failed — check VPS manually.");
    } finally {
      setRestartingBeat(false);
    }
  }

  async function handleCrawl() {
    setLoadingCrawl(true);
    setCrawlMsg(null);
    try {
      await triggerCrawl();
      setCrawlMsg("IG crawl queued — Instagram sources will be checked in a few seconds.");
      mutate();
      setTimeout(() => mutateHealth(), 5000);
    } catch {
      setCrawlMsg("Failed to trigger crawl.");
    } finally {
      setLoadingCrawl(false);
    }
  }

  function fmtLastCrawl() {
    if (!health?.last_crawl_at) return "Never";
    const m = health.minutes_since_crawl;
    if (m === null || m === undefined) return "Unknown";
    if (m < 1) return "just now";
    if (m < 60) return `${m} min ago`;
    const h = Math.floor(m / 60);
    const remM = m % 60;
    return remM > 0 ? `${h}h ${remM}m ago` : `${h}h ago`;
  }

  function fmtNextCrawl() {
    if (!health) return "—";
    if (!health.beat_healthy && !health.in_sleep_window) {
      return "Waiting — click Run IG crawl";
    }
    if (health.in_sleep_window) {
      const wakeH = health.sleep_end_wib;
      return `Sleep ends at ${String(wakeH).padStart(2, "0")}:00 WIB`;
    }
    const elapsed = health.minutes_since_crawl ?? 0;
    const interval = health.crawl_interval_minutes || 30;
    const remaining = Math.max(0, interval - (elapsed % interval));
    if (remaining <= 1) return "any moment now";
    return `in ~${remaining} min`;
  }

  const todayFormatted = mounted
    ? new Date().toLocaleDateString("en-GB", {
        weekday: "long",
        day: "numeric",
        month: "long",
      })
    : "Today";

  // Collect attention items from real data
  const attentionItems: Array<{
    id: string;
    title: string;
    detail: string;
    action: string;
    href?: string;
    onClick?: () => void;
    icon: string;
    tone: "ok" | "warn" | "bad" | "info";
  }> = [];

  if (data && data.pending_review > 0) {
    attentionItems.push({
      id: "pending_review",
      title: `${data.pending_review} post${data.pending_review > 1 ? "s" : ""} waiting for review`,
      detail: "Pending review, caption or card design in queue",
      action: "Review",
      href: "/queue",
      icon: "solar:inbox-line-bold-duotone",
      tone: "ok",
    });
  }

  if (data && data.failed_today > 0) {
    attentionItems.push({
      id: "failed_today",
      title: `${data.failed_today} post${data.failed_today > 1 ? "s" : ""} failed today`,
      detail: "Publishing errors encountered today — inspect in activity",
      action: "Inspect",
      href: "/history",
      icon: "solar:danger-triangle-bold-duotone",
      tone: "bad",
    });
  }

  if (data && data.gallery_fetch_stats && data.gallery_fetch_stats.failed > 0) {
    attentionItems.push({
      id: "gallery_failed",
      title: `${data.gallery_fetch_stats.failed} gallery fetch failed`,
      detail: "Image download or processing issue encountered today",
      action: "Gallery",
      href: "/gallery",
      icon: "solar:gallery-bold-duotone",
      tone: "warn",
    });
  }

  if (health && !health.beat_healthy && !health.in_sleep_window) {
    attentionItems.push({
      id: "beat_stale",
      title: "Scheduler heartbeat stale",
      detail: "Beat worker has not checked in — restart scheduler recommended",
      action: "Restart",
      onClick: handleRestartBeat,
      icon: "solar:clock-circle-bold-duotone",
      tone: "bad",
    });
  }

  if (health && health.in_sleep_window) {
    attentionItems.push({
      id: "sleep_window",
      title: "IG crawler in sleep window",
      detail: `Paused ${String(health.sleep_start_wib).padStart(2, "0")}:00–${String(health.sleep_end_wib).padStart(2, "0")}:00 WIB · scheduled publishing active`,
      action: "Sources",
      href: "/sources",
      icon: "solar:moon-sleep-bold-duotone",
      tone: "warn",
    });
  }

  if (data && data.ai_stats && data.ai_stats.failed > 0) {
    attentionItems.push({
      id: "ai_failed",
      title: `${data.ai_stats.failed} AI call${data.ai_stats.failed > 1 ? "s" : ""} lost`,
      detail: "Model failure in fallback chain — review AI activity logs",
      action: "Logs",
      href: "/logs",
      icon: "solar:cpu-bolt-bold-duotone",
      tone: "bad",
    });
  }

  const toneStyles = {
    ok: { bg: "var(--pri-soft)", fg: "var(--pri-text)" },
    warn: { bg: "var(--warn-soft)", fg: "var(--warn)" },
    bad: { bg: "var(--bad-soft)", fg: "var(--bad)" },
    info: { bg: "var(--info-soft)", fg: "var(--info)" },
  };

  const brandsToday = useMemo(() => {
    const list = data?.brands_today || [];
    if (selectedBrandId === null) return list;
    return list.filter((b: BrandToday) => b.fanpage_id === selectedBrandId);
  }, [data?.brands_today, selectedBrandId]);

  return (
    <div className="space-y-6 max-w-[1240px]">
      {/* ── Page Header ────────────────────────────── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl sm:text-[26px] font-bold text-text tracking-tight">Home</h1>
          <p className="text-sm text-text2">
            <span suppressHydrationWarning>{todayFormatted}</span> · what needs you first, then how today is going.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2.5">
          <button
            type="button"
            onClick={handleSync}
            disabled={loadingSync}
            title="Creates or updates a brand for every Repliz Facebook account (new ones start paused) with a Repliz Page channel, marks missing ones Disconnected. Also runs every 6 h."
            className="h-10 px-3.5 rounded-lg border border-line bg-surface text-text hover:bg-surface2 text-sm font-medium transition-colors inline-flex items-center gap-2 cursor-pointer disabled:opacity-60"
          >
            <Icon
              icon="solar:refresh-bold-duotone"
              width={16}
              className={loadingSync ? "animate-spin text-text2" : "text-text2"}
            />
            <span>{loadingSync ? "Syncing…" : "Sync from Repliz"}</span>
          </button>
          <button
            type="button"
            onClick={handleCrawl}
            disabled={loadingCrawl}
            className="h-10 px-4 rounded-lg bg-pri text-pri-fg hover:opacity-90 text-sm font-semibold transition-opacity inline-flex items-center gap-2 cursor-pointer disabled:opacity-60"
            title="Checks Instagram source accounts for new posts"
          >
            <Icon
              icon={loadingCrawl ? "solar:refresh-bold-duotone" : "solar:play-bold-duotone"}
              width={16}
              className={loadingCrawl ? "animate-spin" : ""}
            />
            <span>{loadingCrawl ? "Crawling…" : "Run IG crawl"}</span>
          </button>
        </div>
      </div>

      {/* ── Status Notifications ─────────────────────── */}
      {crawlMsg && (
        <div
          role="status"
          className="flex items-center gap-2.5 px-3.5 py-3 rounded-xl bg-pri-soft text-pri-text text-sm"
        >
          <Icon icon="solar:check-circle-bold-duotone" width={18} className="shrink-0" />
          <span>{crawlMsg}</span>
        </div>
      )}

      {restartMsg && (
        <div
          role="status"
          className={`flex items-center gap-2.5 px-3.5 py-3 rounded-xl text-sm ${
            restartMsg.includes("failed") ? "bg-bad-soft text-bad" : "bg-pri-soft text-pri-text"
          }`}
        >
          <Icon
            icon={
              restartMsg.includes("failed")
                ? "solar:danger-circle-bold-duotone"
                : "solar:check-circle-bold-duotone"
            }
            width={18}
            className="shrink-0"
          />
          <span>{restartMsg}</span>
        </div>
      )}

      {/* ── System Status ───────────────────────────── */}
      <section
        aria-labelledby="sys-heading"
        className="bg-surface border border-line rounded-[14px] p-5"
      >
        <div className="flex items-center justify-between mb-3.5">
          <h2 id="sys-heading" className="text-[15px] font-semibold text-text">
            System
          </h2>
          <span className="text-xs text-text2 font-medium">4 services</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {/* Card 1: IG Crawler */}
          <div className="flex flex-col justify-between gap-2.5 p-3.5 rounded-xl bg-surface2 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-text">IG crawler</span>
              <span
                className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold shrink-0"
                style={{
                  background:
                    health?.in_sleep_window
                      ? "var(--warn-soft)"
                      : health?.beat_healthy
                      ? "var(--pri-soft)"
                      : "var(--bad-soft)",
                  color:
                    health?.in_sleep_window
                      ? "var(--warn)"
                      : health?.beat_healthy
                      ? "var(--pri-text)"
                      : "var(--bad)",
                }}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-current" />
                {health?.in_sleep_window
                  ? "Sleep window"
                  : health?.beat_healthy
                  ? "Running"
                  : "Stale"}
              </span>
            </div>
            <p className="text-xs text-text2 leading-relaxed">
              Last pass <span suppressHydrationWarning>{mounted ? fmtLastCrawl() : "Never"}</span> · next{" "}
              <span suppressHydrationWarning>{mounted ? fmtNextCrawl() : "—"}</span> · {health?.active_sources ?? 0} active sources
            </p>
            <Link
              href="/sources"
              className="text-xs font-semibold text-pri hover:underline self-start inline-flex items-center gap-1"
            >
              <span>View sources</span>
              <Icon icon="solar:arrow-right-linear" width={12} />
            </Link>
          </div>

          {/* Card 2: Scheduler */}
          <div className="flex flex-col justify-between gap-2.5 p-3.5 rounded-xl bg-surface2 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-text">Scheduler</span>
              <span
                className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold shrink-0"
                style={{
                  background: health?.beat_healthy ? "var(--pri-soft)" : "var(--bad-soft)",
                  color: health?.beat_healthy ? "var(--pri-text)" : "var(--bad)",
                }}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-current" />
                {health?.beat_healthy ? "Running" : "Unresponsive"}
              </span>
            </div>
            <p className="text-xs text-text2 leading-relaxed">
              Celery beat heartbeat active. Restart if posts stop being planned automatically.
            </p>
            <button
              type="button"
              onClick={handleRestartBeat}
              disabled={restartingBeat}
              className="text-xs font-semibold text-pri hover:underline self-start cursor-pointer inline-flex items-center gap-1"
            >
              <span>{restartingBeat ? "Restarting…" : "Restart scheduler"}</span>
              <Icon icon="solar:refresh-bold-duotone" width={12} className={restartingBeat ? "animate-spin" : ""} />
            </button>
          </div>

          {/* Card 3: AI Engine */}
          <div className="flex flex-col justify-between gap-2.5 p-3.5 rounded-xl bg-surface2 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-text">AI engine</span>
              <span
                className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold shrink-0"
                style={{
                  background:
                    (data?.ai_stats.success_rate ?? 100) >= 95
                      ? "var(--pri-soft)"
                      : "var(--warn-soft)",
                  color:
                    (data?.ai_stats.success_rate ?? 100) >= 95
                      ? "var(--pri-text)"
                      : "var(--warn)",
                }}
              >
                <span className="w-1.5 h-1.5 rounded-full bg-current" />
                9Router ready
              </span>
            </div>
            <p className="text-xs text-text2 leading-relaxed">
              {data?.ai_stats.total
                ? `${data.ai_stats.success_rate}% success · ${data.ai_stats.total} calls (24h)`
                : "No AI calls recorded in the last 24h"}
            </p>
            <Link
              href="/settings"
              className="text-xs font-semibold text-pri hover:underline self-start inline-flex items-center gap-1"
            >
              <span>AI settings</span>
              <Icon icon="solar:arrow-right-linear" width={12} />
            </Link>
          </div>

          {/* Card 4: Brands */}
          <div className="flex flex-col justify-between gap-2.5 p-3.5 rounded-xl bg-surface2 min-w-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-semibold text-text">Brands</span>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text shrink-0">
                <span className="w-1.5 h-1.5 rounded-full bg-current" />
                {data?.active_fanpages ?? 0} active
              </span>
            </div>
            <p className="text-xs text-text2 leading-relaxed">
              {data?.active_fanpages ?? 0} of {data?.total_fanpages ?? 0} brands actively publishing to Facebook & channels.
            </p>
            <Link
              href="/brands"
              className="text-xs font-semibold text-pri hover:underline self-start inline-flex items-center gap-1"
            >
              <span>View brands</span>
              <Icon icon="solar:arrow-right-linear" width={12} />
            </Link>
          </div>
        </div>
      </section>

      {/* ── Attention + Today Grid ───────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* Left Column: Needs your attention */}
        <section
          aria-labelledby="attn-heading"
          className="lg:col-span-7 xl:col-span-8 bg-surface border border-line rounded-[14px] overflow-hidden"
        >
          <div className="flex justify-between items-center px-5 py-3.5">
            <h2 id="attn-heading" className="text-[15px] font-semibold text-text">
              Needs your attention
            </h2>
            <span className="text-xs text-text2 font-medium">
              {attentionItems.length} {attentionItems.length === 1 ? "item" : "items"}
            </span>
          </div>

          {attentionItems.length === 0 ? (
            <div className="p-8 text-center border-t border-line">
              <div className="w-10 h-10 rounded-full bg-pri-soft text-pri flex items-center justify-center mx-auto mb-2.5">
                <Icon icon="solar:check-circle-bold-duotone" width={22} />
              </div>
              <p className="text-sm font-semibold text-text">All systems clear</p>
              <p className="text-xs text-text2 mt-0.5">
                No items waiting for manual attention right now.
              </p>
            </div>
          ) : (
            <ul className="divide-y divide-line">
              {attentionItems.map((item) => {
                const tone = toneStyles[item.tone];
                return (
                  <li key={item.id} className="flex items-center gap-3.5 px-5 py-3">
                    <span
                      aria-hidden="true"
                      className="w-9 h-9 rounded-xl shrink-0 flex items-center justify-center"
                      style={{ background: tone.bg, color: tone.fg }}
                    >
                      <Icon icon={item.icon} width={18} />
                    </span>
                    <span className="flex-1 min-w-0 flex flex-col gap-0.5">
                      <span className="text-sm font-semibold text-text">{item.title}</span>
                      <span className="text-xs text-text2 truncate">{item.detail}</span>
                    </span>
                    {item.href ? (
                      <Link
                        href={item.href}
                        className="h-9 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold shrink-0 inline-flex items-center"
                      >
                        {item.action}
                      </Link>
                    ) : item.onClick ? (
                      <button
                        type="button"
                        onClick={item.onClick}
                        className="h-9 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold shrink-0 inline-flex items-center cursor-pointer"
                      >
                        {item.action}
                      </button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Right Column: Today Stats */}
        <section
          aria-labelledby="today-heading"
          className="lg:col-span-5 xl:col-span-4 bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5"
        >
          <div className="flex justify-between items-center">
            <h2 id="today-heading" className="text-[15px] font-semibold text-text">
              Today
            </h2>
            <span className="text-xs text-text2 font-medium">Last 24h</span>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Link
              href="/history"
              className="flex flex-col gap-1 p-3 rounded-xl bg-surface2 text-text hover:bg-line/40 transition-colors"
            >
              <span className="text-xs text-text2">Published</span>
              <span className="text-2xl font-bold tabular-nums text-text">
                {isLoading ? "—" : data?.published_today ?? 0}
              </span>
              <span className="text-[11px] text-text2">on active channels</span>
            </Link>

            <Link
              href="/queue"
              className="flex flex-col gap-1 p-3 rounded-xl bg-surface2 text-text hover:bg-line/40 transition-colors"
            >
              <span className="text-xs text-text2">Waiting review</span>
              <span className="text-2xl font-bold tabular-nums text-text">
                {isLoading ? "—" : data?.pending_review ?? 0}
              </span>
              <span className="text-[11px] text-text2">in review queue</span>
            </Link>

            <Link
              href="/brands"
              className="flex flex-col gap-1 p-3 rounded-xl bg-surface2 text-text hover:bg-line/40 transition-colors"
            >
              <span className="text-xs text-text2">Active brands</span>
              <span className="text-2xl font-bold tabular-nums text-text">
                {isLoading ? "—" : data?.active_fanpages ?? 0}
              </span>
              <span className="text-[11px] text-text2">of {data?.total_fanpages ?? 0} total</span>
            </Link>

            <Link
              href="/history"
              className="flex flex-col gap-1 p-3 rounded-xl bg-surface2 text-text hover:bg-line/40 transition-colors"
            >
              <span className="text-xs text-text2">Failed today</span>
              <span
                className={`text-2xl font-bold tabular-nums ${
                  (data?.failed_today ?? 0) > 0 ? "text-bad" : "text-text"
                }`}
              >
                {isLoading ? "—" : data?.failed_today ?? 0}
              </span>
              <span className="text-[11px] text-text2">uncertain separate</span>
            </Link>
          </div>

          {/* AI stats breakdown */}
          <div className="flex flex-col gap-2 pt-1 border-t border-line">
            <div className="flex justify-between items-center text-xs">
              <span className="text-text2">AI calls, last 24 h</span>
              <span className="font-semibold text-text tabular-nums">
                {data?.ai_stats.total
                  ? `${data.ai_stats.success_rate}% ok · ${data.ai_stats.total} calls`
                  : "0 calls"}
              </span>
            </div>
            {/* Segmented bar */}
            {data && data.ai_stats.total > 0 ? (
              <>
                <div
                  aria-hidden="true"
                  className="flex h-2 rounded-full overflow-hidden bg-surface2"
                >
                  <span
                    style={{
                      width: `${(data.ai_stats.success / data.ai_stats.total) * 100}%`,
                      background: "var(--pri)",
                    }}
                  />
                  <span
                    style={{
                      width: `${(data.ai_stats.recovered / data.ai_stats.total) * 100}%`,
                      background: "var(--warn)",
                    }}
                  />
                  <span
                    style={{
                      width: `${(data.ai_stats.failed / data.ai_stats.total) * 100}%`,
                      background: "var(--bad)",
                    }}
                  />
                </div>
                <div className="flex flex-wrap gap-3 text-[11px] text-text2">
                  <span>Clean {data.ai_stats.success}</span>
                  <span>Recovered {data.ai_stats.recovered}</span>
                  <span>Lost {data.ai_stats.failed}</span>
                </div>
              </>
            ) : (
              <div className="h-2 rounded-full bg-surface2" />
            )}
          </div>
        </section>
      </div>

      {/* ── Today by brand ────────────────────────────── */}
      <section
        aria-labelledby="brands-today-heading"
        className="bg-surface border border-line rounded-[14px]"
      >
        <div className="flex flex-wrap justify-between items-center gap-2.5 p-4 sm:px-[18px]">
          <div className="flex flex-col gap-0.5">
            <h2 id="brands-today-heading" className="text-[15px] font-semibold text-text">
              Today by brand
            </h2>
            <span className="text-xs text-text2">
              Every post goes to each active channel. Planned = the Posts/day of the brand&apos;s active channels.
            </span>
          </div>
          <Link
            href="/brands"
            className="text-xs font-semibold text-pri hover:underline shrink-0"
          >
            All brands
          </Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] border-collapse text-sm">
            <thead>
              <tr className="text-left text-text2 text-xs">
                <th scope="col" className="py-2.5 px-[18px] border-t border-line font-semibold">
                  Brand
                </th>
                <th scope="col" className="py-2.5 px-3 border-t border-line font-semibold">
                  Modes on
                </th>
                <th scope="col" className="py-2.5 px-3 border-t border-line font-semibold">
                  Published / planned
                </th>
                <th scope="col" className="py-2.5 px-3 border-t border-line font-semibold">
                  Waiting
                </th>
                <th scope="col" className="py-2.5 px-3 border-t border-line font-semibold">
                  Next post
                </th>
                <th scope="col" className="py-2.5 px-[18px] border-t border-line font-semibold">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-xs text-text2 border-t border-line">
                    Loading brand statistics…
                  </td>
                </tr>
              ) : !brandsToday || brandsToday.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-xs text-text2 border-t border-line">
                    No active brands found. Activate brands in{" "}
                    <Link href="/brands" className="text-pri hover:underline font-semibold">
                      Brands settings
                    </Link>
                    .
                  </td>
                </tr>
              ) : (
                brandsToday.map((brand: BrandToday) => {
                  const pct =
                    brand.daily_target > 0
                      ? Math.min(100, Math.round((brand.published_today / brand.daily_target) * 100))
                      : 0;
                  const nextLabel = mounted ? formatNextPost(brand.next_post, brand.timezone) : "—";
                  const statusKey =
                    brand.failed_today > 0
                      ? "failed"
                      : brand.waiting_review > 0
                      ? "pending_review"
                      : "published";

                  return (
                    <tr key={brand.fanpage_id} className="hover:bg-surface2/50 transition-colors">
                      <td className="py-3 px-[18px] border-t border-line">
                        <Link
                          href="/brands"
                          className="flex items-center gap-2.5 text-text hover:text-pri transition-colors group"
                        >
                          <span
                            aria-hidden="true"
                            className="w-[30px] h-[30px] rounded-lg text-white flex items-center justify-center text-xs font-bold shrink-0"
                            style={{ background: getBrandColor(brand.fanpage_id) }}
                          >
                            {brand.initials || "FP"}
                          </span>
                          <span className="flex flex-col min-w-0">
                            <span className="font-semibold text-sm leading-tight truncate group-hover:text-pri">
                              {brand.name}
                            </span>
                            <span className="text-xs text-text2 truncate">{brand.timezone}</span>
                          </span>
                        </Link>
                      </td>
                      <td className="py-3 px-3 border-t border-line text-text2 text-xs">
                        {brand.modes && brand.modes.length > 0 ? (
                          <div className="flex flex-wrap gap-1 items-center">
                            {brand.modes.slice(0, 3).map((mode: string) => (
                              <ModeChip key={mode} mode={mode} size="sm" />
                            ))}
                            {brand.modes.length > 3 && (
                              <span className="text-xs text-text2 font-medium">
                                +{brand.modes.length - 3}
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="text-text2">—</span>
                        )}
                      </td>
                      <td className="py-3 px-3 border-t border-line">
                        <div className="flex items-center gap-2.5">
                          <span className="tabular-nums font-semibold text-sm min-w-[48px] text-text">
                            {brand.published_today} / {brand.daily_target}
                          </span>
                          <span
                            aria-hidden="true"
                            className="flex-1 max-w-[120px] h-1.5 rounded-full bg-surface2 overflow-hidden"
                          >
                            <span
                              className="block h-full rounded-full transition-all"
                              style={{ width: `${pct}%`, background: "var(--pri)" }}
                            />
                          </span>
                        </div>
                        <span className="text-xs text-text2">
                          {brand.active_channels} {brand.active_channels === 1 ? "channel" : "channels"}
                        </span>
                      </td>
                      <td className="py-3 px-3 border-t border-line tabular-nums font-semibold text-sm text-text">
                        {brand.waiting_review}
                      </td>
                      <td className="py-3 px-3 border-t border-line text-xs text-text2" suppressHydrationWarning>
                        {nextLabel}
                      </td>
                      <td className="py-3 px-[18px] border-t border-line">
                        <StatusBadge status={statusKey} />
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Latest Posts & Storage ───────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-stretch">
        {/* Latest posts */}
        <section
          aria-labelledby="recent-heading"
          className="lg:col-span-7 xl:col-span-8 bg-surface border border-line rounded-[14px] p-5 flex flex-col justify-between gap-3.5"
        >
          <div>
            <div className="flex justify-between items-center mb-3">
              <h2 id="recent-heading" className="text-[15px] font-semibold text-text">
                Latest posts
              </h2>
              <Link href="/history" className="text-xs font-semibold text-pri hover:underline">
                All activity
              </Link>
            </div>
            {isLoading ? (
              <p className="text-xs text-text2 py-6 text-center">Loading latest posts…</p>
            ) : !data?.latest_posts || data.latest_posts.length === 0 ? (
              <p className="text-xs text-text2 py-6 text-center">No published posts yet.</p>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3">
                {data.latest_posts.map((post: LatestPost) => (
                  <Link
                    key={post.id}
                    href="/history"
                    className="flex flex-col gap-1.5 text-text group min-w-0"
                  >
                    <span
                      aria-hidden="true"
                      className="aspect-[4/5] rounded-[10px] bg-surface2 border border-line relative overflow-hidden flex items-center justify-center shrink-0"
                    >
                      {post.thumbnail_url ? (
                        <img
                          src={post.thumbnail_url}
                          alt={post.title || post.fanpage_name}
                          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
                        />
                      ) : (
                        <Icon
                          icon="solar:gallery-bold-duotone"
                          width={28}
                          className="text-text2/40"
                        />
                      )}
                      <span className="absolute left-1.5 bottom-1.5">
                        <StatusBadge
                          status="published"
                          className="shadow-sm !text-[10px] !px-2 !py-0.5"
                        />
                      </span>
                    </span>
                    <span className="text-xs font-semibold text-text truncate group-hover:text-pri transition-colors">
                      {post.fanpage_name}
                    </span>
                    <span className="text-[11px] text-text2 line-clamp-2 leading-tight">
                      {post.title || post.content_type}
                    </span>
                    <span className="text-[10px] text-text2/70 mt-auto" suppressHydrationWarning>
                      {mounted ? fmtTimeAgo(post.published_at) : ""}
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* Storage */}
        {data && data.disk_total_mb > 0 && (
          <section
            aria-labelledby="storage-heading"
            className="lg:col-span-5 xl:col-span-4 bg-surface border border-line rounded-[14px] p-5 flex flex-col justify-between gap-3"
          >
            <div>
              <div className="flex items-center justify-between mb-2">
                <h2 id="storage-heading" className="text-[15px] font-semibold text-text">
                  Storage
                </h2>
                <span className="text-xs text-text2 font-medium">{diskPct}% used</span>
              </div>
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-2xl font-bold text-text tabular-nums">{diskPct}%</span>
                <span className="text-xs text-text2">
                  {data.disk_used_mb >= 1024
                    ? `${(data.disk_used_mb / 1024).toFixed(1)} GB`
                    : `${data.disk_used_mb} MB`}{" "}
                  of{" "}
                  {data.disk_total_mb >= 1024
                    ? `${(data.disk_total_mb / 1024).toFixed(0)} GB`
                    : `${data.disk_total_mb} MB`}{" "}
                  used
                </span>
              </div>
              <div
                aria-hidden="true"
                className="flex h-2.5 rounded-full overflow-hidden bg-surface2 mb-3"
              >
                <span
                  style={{
                    width: `${Math.min(
                      100,
                      (data.disk_gallery_mb / data.disk_total_mb) * 100
                    )}%`,
                    background: "var(--info)",
                  }}
                />
                <span
                  style={{
                    width: `${Math.max(
                      0,
                      ((data.disk_used_mb - data.disk_gallery_mb) / data.disk_total_mb) * 100
                    )}%`,
                    background: "var(--text2)",
                  }}
                />
              </div>
              <div className="flex flex-col gap-1.5 text-xs">
                <div className="flex justify-between items-center">
                  <span className="flex items-center gap-2 text-text2">
                    <span className="w-2.5 h-2.5 rounded-[3px] bg-info shrink-0" />
                    Image gallery
                  </span>
                  <span className="font-medium text-text tabular-nums">
                    {data.disk_gallery_mb >= 1024
                      ? `${(data.disk_gallery_mb / 1024).toFixed(1)} GB`
                      : `${data.disk_gallery_mb} MB`}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="flex items-center gap-2 text-text2">
                    <span className="w-2.5 h-2.5 rounded-[3px] bg-text2 shrink-0" />
                    Media & temp files
                  </span>
                  <span className="font-medium text-text tabular-nums">
                    {Math.max(0, data.disk_used_mb - data.disk_gallery_mb) >= 1024
                      ? `${((data.disk_used_mb - data.disk_gallery_mb) / 1024).toFixed(1)} GB`
                      : `${Math.max(0, data.disk_used_mb - data.disk_gallery_mb)} MB`}
                  </span>
                </div>
                {data.gallery_fetch_stats && (
                  <div className="flex justify-between items-center pt-2 mt-0.5 border-t border-line">
                    <span className="flex items-center gap-2 text-text2">
                      <Icon icon="solar:gallery-wide-bold-duotone" width={14} className="text-text2 shrink-0" />
                      Gallery fetches today
                    </span>
                    <span className="font-medium text-text tabular-nums">
                      {data.gallery_fetch_stats.total}{" "}
                      <span className="text-text2 font-normal">
                        ({data.gallery_fetch_stats.failed > 0
                          ? `${data.gallery_fetch_stats.failed} failed`
                          : `${data.gallery_fetch_stats.success} ok`})
                      </span>
                    </span>
                  </div>
                )}
              </div>
            </div>
            <Link
              href="/gallery"
              className="text-xs font-semibold text-pri hover:underline self-start pt-1"
            >
              Manage gallery
            </Link>
          </section>
        )}
      </div>

      {/* ── Quick Links ─────────────────────────────── */}
      <section
        aria-labelledby="links-heading"
        className="bg-surface border border-line rounded-[14px] p-5 flex flex-col justify-between gap-3"
      >
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 id="links-heading" className="text-[15px] font-semibold text-text">
              Quick Links
            </h2>
            <span className="text-xs text-text2 font-medium">Shortcuts</span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {[
              { href: "/brands", icon: "solar:shop-2-bold-duotone", label: "Manage Brands" },
              { href: "/queue", icon: "solar:clock-circle-bold-duotone", label: "Review Queue" },
              { href: "/sources", icon: "solar:global-bold-duotone", label: "Sources" },
              { href: "/history", icon: "solar:history-bold-duotone", label: "Publishing Activity" },
              { href: "/logs", icon: "solar:document-text-bold-duotone", label: "Logs & Alerts" },
              { href: "/settings", icon: "solar:settings-bold-duotone", label: "Global Settings" },
            ].map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-surface2 hover:bg-line/40 text-text transition-colors group"
              >
                <Icon
                  icon={item.icon}
                  width={16}
                  className="text-text2 group-hover:text-pri transition-colors shrink-0"
                />
                <span className="text-xs font-medium truncate">{item.label}</span>
              </Link>
            ))}
          </div>
        </div>
        <p className="text-[11px] text-text2 pt-1">
          Studio runs automated publishing to Facebook Pages & channels.
        </p>
      </section>
    </div>
  );
}
