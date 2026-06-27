"use client";

import { useState } from "react";
import useSWR from "swr";
import { getDashboardStats, getCrawlerHealth, triggerCrawl, triggerFanpageSync } from "@/lib/api";
import type { DashboardStats, BurnerStatus, CrawlerHealth } from "@/lib/types";
import { Icon } from "@iconify/react";

const BURNER_STATUS_STYLE: Record<BurnerStatus, string> = {
  active:       "badge-green",
  challenged:   "badge-yellow",
  rate_limited: "badge-yellow",
  banned:       "badge-red",
};

const BURNER_STATUS_ICON: Record<BurnerStatus, string> = {
  active:       "solar:wifi-bold-duotone",
  challenged:   "solar:danger-circle-bold-duotone",
  rate_limited: "solar:danger-circle-bold-duotone",
  banned:       "solar:shield-cross-bold-duotone",
};

const fetcher = () => getDashboardStats().then((r) => r.data as DashboardStats);
const healthFetcher = () => getCrawlerHealth().then((r) => r.data as CrawlerHealth);

/* ── Sparkline paths (80×36 viewBox) ────────────── */
const SPARKLINES = {
  green:  "M0,28 C10,24 18,20 28,16 C38,12 46,14 56,10 C66,6 74,8 80,4",
  purple: "M0,18 C10,10 18,26 30,16 C42,6 50,22 60,14 C68,8 76,18 80,14",
  amber:  "M0,24 C14,20 22,16 34,14 C46,12 54,10 64,8 C72,6 78,9 80,5",
  blue:   "M0,26 C12,22 20,18 32,14 C44,10 54,12 66,8 C74,5 78,7 80,4",
  red:    "M0,8 C12,12 20,18 32,20 C44,22 54,24 64,20 C72,16 78,22 80,26",
};

export default function DashboardPage() {
  const { data, isLoading, mutate } = useSWR("dashboard-stats", fetcher, {
    refreshInterval: 30000,
  });
  const { data: health, mutate: mutateHealth } = useSWR("crawler-health", healthFetcher, {
    refreshInterval: 30000,
  });
  const [loadingSync, setLoadingSync]   = useState(false);
  const [loadingCrawl, setLoadingCrawl] = useState(false);
  const [crawlMsg, setCrawlMsg] = useState<string | null>(null);

  const diskPct = data ? Math.round((data.disk_used_mb / data.disk_total_mb) * 100) : 0;

  async function handleSync() {
    setLoadingSync(true);
    try { await triggerFanpageSync(); mutate(); } finally { setLoadingSync(false); }
  }

  async function handleCrawl() {
    setLoadingCrawl(true);
    setCrawlMsg(null);
    try {
      await triggerCrawl();
      setCrawlMsg("Crawl queued — sources will be checked in a few seconds.");
      mutate();
      setTimeout(() => mutateHealth(), 5000);
    } catch {
      setCrawlMsg("Failed to trigger crawl.");
    } finally {
      setLoadingCrawl(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* ── Page header ────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Dashboard</h1>
          <p className="text-sm text-text-secondary mt-0.5">Overview of your reposter activity</p>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <button onClick={handleSync} disabled={loadingSync} className="btn-ghost">
            <Icon icon="solar:refresh-bold-duotone" width={14} className={loadingSync ? "animate-spin" : ""} />
            <span className="hidden sm:inline">{loadingSync ? "Syncing…" : "Sync"}</span>
          </button>
          <button onClick={handleCrawl} disabled={loadingCrawl} className="btn-primary">
            <Icon icon={loadingCrawl ? "solar:refresh-bold-duotone" : "solar:play-bold-duotone"} width={14} className={loadingCrawl ? "animate-spin" : ""} />
            {loadingCrawl ? "Crawling…" : "Crawl Now"}
          </button>
        </div>
      </div>

      {/* ── KPI cards ──────────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-5">
        <KpiCard
          icon="solar:graph-up-bold-duotone"
          label="Published Today"
          value={isLoading ? "—" : String(data?.published_today ?? 0)}
          trend="+today"
          trendUp={true}
          variant="green"
          sparkline={SPARKLINES.green}
          href="/history"
        />
        <KpiCard
          icon="solar:clock-circle-bold-duotone"
          label="Pending Review"
          value={isLoading ? "—" : String(data?.pending_review ?? 0)}
          trend="queue"
          trendUp={null}
          variant="amber"
          sparkline={SPARKLINES.amber}
          href="/queue"
        />
        <KpiCard
          icon="eva:facebook-fill"
          label="Active Fanpages"
          value={isLoading ? "—" : `${data?.active_fanpages ?? 0}`}
          trend={isLoading ? "" : `of ${data?.total_fanpages ?? 0}`}
          trendUp={true}
          variant="blue"
          sparkline={SPARKLINES.blue}
          href="/fanpages"
        />
        <KpiCard
          icon="solar:close-circle-bold-duotone"
          label="Failed Today"
          value={isLoading ? "—" : String(data?.failed_today ?? 0)}
          trend="errors"
          trendUp={(data?.failed_today ?? 0) === 0}
          variant="red"
          sparkline={SPARKLINES.red}
        />
      </div>

      {/* ── Crawler health bar ─────────────────────── */}
      {health && (
        <CrawlerHealthCard health={health} onCrawlNow={handleCrawl} crawling={loadingCrawl} crawlMsg={crawlMsg} />
      )}

      {/* ── Lower row: burners + disk ──────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Burner accounts — takes 2 columns */}
        <div className="lg:col-span-2 card">
          <div className="flex items-center justify-between mb-5">
            <div>
              <h2 className="text-base font-bold text-text-primary">Burner Accounts</h2>
              <p className="text-xs text-text-secondary mt-0.5">Instagram session status</p>
            </div>
            <a href="/burners" className="text-xs text-primary-main hover:underline font-medium">
              Manage →
            </a>
          </div>

          {isLoading ? (
            <div className="space-y-3">
              {[1, 2].map((i) => (
                <div key={i} className="h-14 bg-bg-paper-hover rounded-lg animate-pulse" />
              ))}
            </div>
          ) : (data?.burners ?? []).length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 text-center">
              <Icon icon="solar:users-group-rounded-bold-duotone" width={40} className="text-text-disabled mb-3" />
              <p className="text-sm text-text-secondary">No burner accounts configured</p>
              <a href="/burners" className="mt-2 text-xs text-primary-main hover:underline">Add one →</a>
            </div>
          ) : (
            <div className="space-y-2">
              {(data?.burners ?? []).map((b) => (
                <div
                  key={b.id}
                  className="flex items-center justify-between gap-3 px-4 py-3 rounded-lg bg-bg-default hover:bg-bg-paper-hover transition-colors"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="w-8 h-8 rounded-full bg-bg-paper-hover flex items-center justify-center flex-shrink-0">
                      <Icon
                        icon={BURNER_STATUS_ICON[b.status]}
                        width={16}
                        className={
                          b.status === "active" ? "text-primary-main"
                          : b.status === "banned" ? "text-error-main"
                          : "text-warning-main"
                        }
                      />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-text-primary truncate">
                        @{b.ig_username}
                      </div>
                      <div className="text-xs text-text-secondary">
                        {b.requests_today} req today
                        {b.cooldown_until && (
                          <span className="ml-2 text-warning-main">
                            cooldown until {new Date(b.cooldown_until).toLocaleTimeString()}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <span className={BURNER_STATUS_STYLE[b.status]}>
                    {b.status.replace("_", " ")}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right column: disk + quick links */}
        <div className="space-y-5">
          {/* Disk usage card */}
          {data && data.disk_total_mb > 0 && (
            <div className="card">
              <div className="flex items-center gap-2 mb-4">
                <Icon icon="solar:server-minimalistic-bold-duotone" width={18} className="text-text-secondary" />
                <h2 className="text-sm font-bold text-text-primary">Media Storage</h2>
              </div>

              {/* Radial-style progress */}
              <div className="flex items-center gap-4">
                <div className="relative w-16 h-16 flex-shrink-0">
                  <svg viewBox="0 0 36 36" className="w-16 h-16 -rotate-90">
                    <circle cx="18" cy="18" r="15.9" fill="none" stroke="rgba(145,158,171,0.12)" strokeWidth="3" />
                    <circle
                      cx="18" cy="18" r="15.9"
                      fill="none"
                      stroke={diskPct > 80 ? "#FF5630" : "#00A76F"}
                      strokeWidth="3"
                      strokeDasharray={`${diskPct} 100`}
                      strokeLinecap="round"
                    />
                  </svg>
                  <span className="absolute inset-0 flex items-center justify-center text-xs font-bold text-text-primary">
                    {diskPct}%
                  </span>
                </div>
                <div>
                  <div className="text-lg font-bold text-text-primary">
                    {data.disk_used_mb >= 1024
                      ? `${(data.disk_used_mb / 1024).toFixed(1)} GB`
                      : `${data.disk_used_mb} MB`}
                  </div>
                  <div className="text-xs text-text-secondary">
                    of {data.disk_total_mb >= 1024
                      ? `${(data.disk_total_mb / 1024).toFixed(0)} GB`
                      : `${data.disk_total_mb} MB`} used
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Quick links */}
          <div className="card">
            <h2 className="text-sm font-bold text-text-primary mb-4">Quick Actions</h2>
            <div className="space-y-1">
              {[
                { href: "/fanpages", icon: "solar:megaphone-bold-duotone",  label: "Manage Fanpages" },
                { href: "/burners",  icon: "solar:users-group-rounded-bold-duotone", label: "Burner Accounts" },
                { href: "/queue",    icon: "solar:clock-circle-bold-duotone", label: "Review Queue" },
                { href: "/sources",  icon: "solar:global-bold-duotone",      label: "IG Sources" },
              ].map(({ href, icon, label }) => (
                <a
                  key={href}
                  href={href}
                  className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-bg-paper-hover transition-colors group"
                >
                  <Icon icon={icon} width={18} className="text-text-secondary group-hover:text-primary-main transition-colors" />
                  <span className="text-sm text-text-secondary group-hover:text-text-primary transition-colors">{label}</span>
                  <Icon icon="solar:alt-arrow-right-bold-duotone" width={14} className="ml-auto text-text-disabled group-hover:text-text-secondary transition-colors" />
                </a>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────
   Crawler Health Card
   ──────────────────────────────────────────────── */
function CrawlerHealthCard({
  health, onCrawlNow, crawling, crawlMsg,
}: {
  health: CrawlerHealth;
  onCrawlNow: () => void;
  crawling: boolean;
  crawlMsg: string | null;
}) {
  const { beat_healthy, in_sleep_window, minutes_since_crawl, last_crawl_at,
          sleep_start_wib, sleep_end_wib, crawl_interval_minutes } = health;

  const status = in_sleep_window ? "sleep" : beat_healthy ? "ok" : "dead";

  const statusConfig = {
    ok:    { bg: "bg-[rgba(0,167,111,0.08)]", border: "border-[rgba(0,167,111,0.2)]", dot: "bg-primary-main", label: "Running", labelClass: "text-primary-main" },
    sleep: { bg: "bg-[rgba(255,171,0,0.08)]", border: "border-[rgba(255,171,0,0.2)]", dot: "bg-warning-main", label: "Sleep Window", labelClass: "text-warning-main" },
    dead:  { bg: "bg-[rgba(255,86,48,0.08)]",  border: "border-[rgba(255,86,48,0.2)]",  dot: "bg-error-main",   label: "Beat Stopped", labelClass: "text-error-main" },
  }[status];

  function fmtLastCrawl() {
    if (!last_crawl_at) return "Never";
    if (minutes_since_crawl === null) return "Unknown";
    if (minutes_since_crawl < 1) return "just now";
    if (minutes_since_crawl < 60) return `${minutes_since_crawl}m ago`;
    const h = Math.floor(minutes_since_crawl / 60);
    const m = minutes_since_crawl % 60;
    return m > 0 ? `${h}h ${m}m ago` : `${h}h ago`;
  }

  return (
    <div className={`rounded-xl border px-5 py-4 flex flex-wrap items-center gap-4 ${statusConfig.bg} ${statusConfig.border}`}>
      <div className="flex items-center gap-2.5 flex-1 min-w-0">
        <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${statusConfig.dot} ${status === "ok" ? "animate-pulse" : ""}`} />
        <div>
          <div className="flex items-center gap-2">
            <span className={`text-sm font-bold ${statusConfig.labelClass}`}>
              Crawler — {statusConfig.label}
            </span>
            {status === "dead" && (
              <span className="text-xs text-error-main bg-[rgba(255,86,48,0.12)] px-2 py-0.5 rounded-full font-medium">
                Beat process stopped — restart on VPS
              </span>
            )}
            {status === "sleep" && (
              <span className="text-xs text-warning-main">
                {String(sleep_start_wib).padStart(2, "0")}:00–{String(sleep_end_wib).padStart(2, "0")}:00 WIB
              </span>
            )}
          </div>
          <p className="text-xs text-text-secondary mt-0.5">
            Last crawl: <span className="font-medium text-text-primary">{fmtLastCrawl()}</span>
            <span className="mx-1.5 text-text-disabled">·</span>
            Interval: every {crawl_interval_minutes}m
          </p>
        </div>
      </div>

      <div className="flex items-center gap-3 flex-shrink-0">
        {crawlMsg && (
          <span className={`text-xs ${crawlMsg.includes("Failed") ? "text-error-main" : "text-primary-main"}`}>
            {crawlMsg}
          </span>
        )}
        <button
          onClick={onCrawlNow}
          disabled={crawling}
          className="btn-primary text-xs py-1.5 px-3"
        >
          <Icon icon={crawling ? "solar:refresh-bold-duotone" : "solar:play-bold-duotone"} width={13} className={crawling ? "animate-spin" : ""} />
          {crawling ? "Crawling…" : "Run Crawl Now"}
        </button>
      </div>
    </div>
  );
}

/* ────────────────────────────────────────────────
   KPI Card
   ──────────────────────────────────────────────── */
const VARIANTS = {
  //         card bg    text       icon bg    icon color  spark
  green:  { bg: "#D3F9E4", text: "#004B50", iconBg: "#52C07A", iconColor: "#007867", sparkColor: "#00A76F" },
  purple: { bg: "#EDE9FE", text: "#27097A", iconBg: "#A78BFA", iconColor: "#5119B7", sparkColor: "#7C3AED" },
  amber:  { bg: "#FEF9C3", text: "#7A4100", iconBg: "#FCD34D", iconColor: "#B76E00", sparkColor: "#FFAB00" },
  blue:   { bg: "#D1E9FF", text: "#061B64", iconBg: "#6EB6FF", iconColor: "#0C52CC", sparkColor: "#1877F2" },
  red:    { bg: "#FFE4D6", text: "#7A0916", iconBg: "#F87171", iconColor: "#B71D18", sparkColor: "#FF5630" },
} as const;

function KpiCard({
  icon, label, value, trend, trendUp, variant, sparkline, href,
}: {
  icon: string; label: string; value: string; trend: string;
  trendUp: boolean | null; variant: keyof typeof VARIANTS; sparkline: string; href?: string;
}) {
  const v = VARIANTS[variant];

  const inner = (
    <div
      className="relative rounded-2xl p-5 overflow-hidden flex flex-col min-h-[160px]"
      style={{ background: v.bg }}
    >
      {/* Dot-pattern texture */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage: `radial-gradient(circle, ${v.iconColor}18 1.5px, transparent 1.5px)`,
          backgroundSize: "18px 18px",
          backgroundPosition: "bottom right",
        }}
      />

      {/* Top row: icon (left) + trend (right) */}
      <div className="relative flex items-start justify-between mb-4">
        {/* Icon — distinct bg so it pops from the card */}
        <div
          className="w-12 h-12 rounded-2xl flex items-center justify-center shadow-sm"
          style={{ background: v.iconBg }}
        >
          <Icon icon={icon} width={26} style={{ color: v.iconColor }} />
        </div>

        {/* Trend */}
        {trend && (
          <div className="flex items-center gap-0.5 mt-1" style={{ color: v.text }}>
            {trendUp === true  && <Icon icon="solar:arrow-right-up-bold"   width={13} />}
            {trendUp === false && <Icon icon="solar:arrow-right-down-bold" width={13} />}
            <span className="text-xs font-bold">{trend}</span>
          </div>
        )}
      </div>

      {/* Bottom row: label + value (left) | sparkline (right) */}
      <div className="relative flex items-end justify-between flex-1 gap-2">
        <div>
          <p className="text-sm font-medium mb-1" style={{ color: v.text, opacity: 0.72 }}>{label}</p>
          <p className="text-3xl font-extrabold leading-none" style={{ color: v.text }}>{value}</p>
        </div>

        {/* Sparkline — right side, bottom-aligned */}
        <svg viewBox="0 0 80 36" className="w-24 h-9 flex-shrink-0" preserveAspectRatio="none">
          <path
            d={sparkline}
            fill="none"
            stroke={v.sparkColor}
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity="0.8"
          />
        </svg>
      </div>
    </div>
  );

  if (href) return (
    <a href={href} className="block hover:scale-[1.02] transition-transform duration-200">{inner}</a>
  );
  return inner;
}
