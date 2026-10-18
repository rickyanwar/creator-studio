"use client";

import { useState } from "react";
import useSWR from "swr";
import { getAnalyticsOverview } from "@/lib/analyticsApi";
import type { AnalyticsOverview, AnalyticsHourItem, AnalyticsWeekdayItem } from "@/lib/analyticsApi";
import { getFanpageAnalytics } from "@/lib/api";
import type { FanpageAnalytics, AnalyticsTrend } from "@/lib/types";
import { Icon } from "@iconify/react";
import { useBrandFilter } from "@/contexts/BrandFilterContext";

function HourEngagementChart({
  hours,
  timezoneLabel,
}: {
  hours: AnalyticsHourItem[];
  timezoneLabel: string;
}) {
  const maxHourlyMedian = Math.max(...hours.map((h) => h.eng_median ?? 0), 1);

  return (
    <section
      aria-labelledby="hour-heading"
      className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="hour-heading" className="text-[15px] font-semibold text-text">
          Median engagement by hour ({timezoneLabel})
        </h2>
        <div className="flex flex-wrap items-center gap-3.5 text-xs text-text2">
          <span className="inline-flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-[3px] bg-pri shrink-0" />
            Best hours
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-[3px] bg-info shrink-0" />
            Median
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-[3px] border border-dashed border-text2 shrink-0" />
            Under 5 posts
          </span>
        </div>
      </div>

      <div className="flex gap-2.5 pt-2">
        <div
          aria-hidden="true"
          className="flex flex-col justify-between h-48 text-[11px] text-text2 text-right w-8 shrink-0 py-1"
        >
          <span>{maxHourlyMedian}</span>
          <span>{Math.round(maxHourlyMedian * 0.66)}</span>
          <span>{Math.round(maxHourlyMedian * 0.33)}</span>
          <span>0</span>
        </div>

        <div className="flex-1 flex flex-col gap-1.5 min-w-0">
          <div
            role="img"
            aria-label="Bar chart: engagement by hour"
            className="h-48 flex items-end gap-1 border-b border-line pb-1"
          >
            {hours.map((h) => {
              const isFew = h.posts < 5 || h.eng_median === null;
              const isBest =
                !isFew && (h.eng_median ?? 0) >= maxHourlyMedian * 0.8 && maxHourlyMedian > 0;
              const heightPct = isFew
                ? "10%"
                : `${Math.max(10, Math.round(((h.eng_median || 0) / maxHourlyMedian) * 100))}%`;

              return (
                <div
                  key={h.hour}
                  title={`${String(h.hour).padStart(2, "0")}:00 · median ${
                    h.eng_median ?? 0
                  } · ${h.posts} posts`}
                  className="flex-1 h-full flex items-end group relative"
                >
                  <div
                    className="w-full rounded-t-[4px] transition-all"
                    style={{
                      height: heightPct,
                      background: isFew
                        ? "transparent"
                        : isBest
                        ? "var(--pri)"
                        : "var(--info)",
                      border: isFew ? "1px dashed var(--text2)" : "0",
                    }}
                  />
                </div>
              );
            })}
          </div>
          <div
            aria-hidden="true"
            className="grid grid-cols-8 text-[11px] text-text2 pt-0.5 text-center"
          >
            <span>00</span>
            <span>03</span>
            <span>06</span>
            <span>09</span>
            <span>12</span>
            <span>15</span>
            <span>18</span>
            <span>21</span>
          </div>
        </div>
      </div>
    </section>
  );
}

function WeekdayEngagementChart({
  weekdays,
  className,
}: {
  weekdays: AnalyticsWeekdayItem[];
  className?: string;
}) {
  const maxW = Math.max(...weekdays.map((x) => x.eng_median ?? 0), 1);

  return (
    <section
      aria-labelledby="dow-heading"
      className={`bg-surface border border-line rounded-[14px] p-5 flex flex-col justify-between gap-3 ${
        className ?? ""
      }`}
    >
      <h2 id="dow-heading" className="text-[15px] font-semibold text-text">
        By weekday
      </h2>

      <div className="flex flex-col gap-2.5">
        {weekdays.map((d) => {
          const wPct =
            d.eng_median != null
              ? `${Math.max(6, Math.round((d.eng_median / maxW) * 100))}%`
              : "0%";

          return (
            <div
              key={d.weekday}
              className="grid grid-cols-[36px_1fr_48px] gap-2.5 items-center text-xs"
            >
              <span className="text-text2">{d.label}</span>
              <div
                aria-hidden="true"
                className="h-2.5 rounded-full bg-surface2 overflow-hidden"
              >
                <span
                  className="block h-full bg-info rounded-full"
                  style={{ width: wPct }}
                />
              </div>
              <span className="text-right text-text tabular-nums font-semibold">
                {d.eng_median != null ? d.eng_median : "n<5"}
              </span>
            </div>
          );
        })}
      </div>

      <span className="text-[11px] text-text2 pt-1 border-t border-line">
        Median engagement per post by weekday
      </span>
    </section>
  );
}

export default function AnalyticsPage() {
  const [days, setDays] = useState<number>(28);
  const { selectedBrandId: selectedFanpageId, setSelectedBrandId: setSelectedFanpageId } = useBrandFilter();

  const { data: overview, error: overviewError } = useSWR(
    ["analytics-overview", days],
    ([, d]) => getAnalyticsOverview(d).then((r) => r.data as AnalyticsOverview)
  );

  const { data: detail, error: detailError, isLoading: detailLoading } = useSWR(
    selectedFanpageId ? ["analytics-fanpage", selectedFanpageId, days] : null,
    ([, id, d]) => getFanpageAnalytics(id, d).then((r) => r.data as FanpageAnalytics)
  );

  function renderStatusBanner(metrics: AnalyticsOverview["metrics"] | undefined) {
    if (!metrics) return null;
    if (!metrics.enabled) {
      return (
        <div
          role="alert"
          className="bg-warn-soft text-warn px-4 py-3 rounded-xl text-sm font-medium border border-line"
        >
          Post metrics are off. They need the Repliz Gold plan — enable them in Settings.
        </div>
      );
    }
    if (metrics.plan_status === "plan_required") {
      return (
        <div
          role="alert"
          className="bg-warn-soft text-warn px-4 py-3 rounded-xl text-sm font-medium border border-line"
        >
          Repliz says the current plan has no statistics access (Gold needed). Metrics resume automatically after upgrading.
        </div>
      );
    }
    if (metrics.plan_status === "error" && metrics.last_error) {
      return (
        <div
          role="alert"
          className="bg-bad-soft text-bad px-4 py-3 rounded-xl text-sm font-medium border border-line"
        >
          {metrics.last_error}
        </div>
      );
    }
    return null;
  }

  function renderTrendBadge(trend?: AnalyticsTrend) {
    if (!trend) return null;
    const { label, change_pct } = trend;
    if (label === "rising") {
      return (
        <span className="inline-flex items-center gap-1 bg-pri-soft text-pri-text px-2 py-0.5 rounded-full text-xs font-semibold">
          <Icon icon="solar:arrow-right-up-bold" width={11} />
          {change_pct != null ? `${change_pct > 0 ? "+" : ""}${change_pct.toFixed(1)}%` : "Rising"}
        </span>
      );
    }
    if (label === "falling") {
      return (
        <span className="inline-flex items-center gap-1 bg-bad-soft text-bad px-2 py-0.5 rounded-full text-xs font-semibold">
          <Icon icon="solar:arrow-right-down-bold" width={11} />
          {change_pct != null ? `${change_pct.toFixed(1)}%` : "Falling"}
        </span>
      );
    }
    if (label === "flat") {
      return (
        <span className="inline-flex items-center gap-1 bg-surface2 text-text2 px-2 py-0.5 rounded-full text-xs font-semibold">
          <Icon icon="solar:arrow-right-bold" width={11} />
          {change_pct != null ? `${change_pct.toFixed(1)}%` : "Flat"}
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 bg-surface2 text-text2 px-2 py-0.5 rounded-full text-xs font-semibold">
        n/a
      </span>
    );
  }

  // Pre-calculate line chart points for detail.daily
  const dailyPoints = detail?.daily ?? [];
  const maxDaily = Math.max(...dailyPoints.map((d) => d.engagement), 1);
  const minDaily = Math.min(...dailyPoints.map((d) => d.engagement), 0);
  const dailyRange = Math.max(1, maxDaily - minDaily);
  const polylineCoords =
    dailyPoints.length > 1
      ? dailyPoints
          .map((d, i) => {
            const x = Math.round(i * (600 / (dailyPoints.length - 1)));
            const y = Math.round(170 - ((d.engagement - minDaily) / dailyRange) * 140);
            return `${x},${y}`;
          })
          .join(" ")
      : "";

  return (
    <div className="space-y-5 max-w-[1240px]">
      {/* ── Page Header ────────────────────────────── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl sm:text-[26px] font-bold text-text tracking-tight">
            Analytics
          </h1>
          <p className="text-sm text-text2">
            {detail
              ? `Facebook engagement from Repliz, in this brand's time zone (${detail.fanpage.timezone}). Posts made only by a phone may have no numbers.`
              : "Facebook engagement from Repliz across all brands (each post in brand's local time zone)."}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          {/* Brand select */}
          <select
            aria-label="Brand"
            value={selectedFanpageId ?? ""}
            onChange={(e) =>
              setSelectedFanpageId(e.target.value ? Number(e.target.value) : null)
            }
            className="h-10 px-3 rounded-lg border border-line bg-surface text-text text-sm font-medium focus:outline-none"
          >
            <option value="">All brands (compare)</option>
            {overview?.fanpages.map((fp) => (
              <option key={fp.fanpage_id} value={fp.fanpage_id}>
                {fp.name}
              </option>
            ))}
          </select>

          {/* Range picker */}
          <div
            role="group"
            aria-label="Range"
            className="flex items-center gap-1 p-1 rounded-xl bg-surface2"
          >
            {[7, 28, 90].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                aria-pressed={days === d}
                className={`h-8 px-3 rounded-lg text-xs font-semibold transition-colors cursor-pointer ${
                  days === d
                    ? "bg-surface text-text shadow-sm"
                    : "text-text2 hover:text-text"
                }`}
              >
                {d} days
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* ── Status Banner ────────────────────────────── */}
      {renderStatusBanner(detail?.metrics || overview?.metrics)}

      {/* ── Loading State ───────────────────────────── */}
      {((!overview && !overviewError) || (selectedFanpageId && detailLoading)) && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 animate-pulse">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-28 rounded-[14px] bg-surface2 border border-line" />
          ))}
        </div>
      )}

      {/* ── All Brands (Compare) View ───────────────── */}
      {!selectedFanpageId && overview && (
        <div className="space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {overview.fanpages.map((fp) => (
              <div
                key={fp.fanpage_id}
                onClick={() => setSelectedFanpageId(fp.fanpage_id)}
                className="bg-surface border border-line rounded-[14px] p-5 hover:border-pri cursor-pointer transition-colors flex flex-col justify-between gap-4 group"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-base font-bold text-text group-hover:text-pri transition-colors">
                      {fp.name}
                    </h3>
                    <p className="text-xs text-text2 mt-0.5">{fp.timezone}</p>
                  </div>
                  {renderTrendBadge(fp.trend)}
                </div>

                <div className="grid grid-cols-3 gap-2 pt-3 border-t border-line">
                  <div>
                    <span className="text-xs text-text2 block">Posts</span>
                    <span className="text-lg font-bold text-text tabular-nums">
                      {fp.posts}
                    </span>
                  </div>
                  <div>
                    <span className="text-xs text-text2 block">Median Eng</span>
                    <span className="text-lg font-bold text-text tabular-nums">
                      {fp.eng_final_median ?? "—"}
                    </span>
                  </div>
                  <div>
                    <span className="text-xs text-text2 block">Best Hour</span>
                    <span className="text-lg font-bold text-text tabular-nums">
                      {fp.best_hour_local !== null
                        ? `${String(fp.best_hour_local).padStart(2, "0")}:00`
                        : "—"}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Section: Median engagement by hour across all brands */}
          {overview.by_hour && (
            <HourEngagementChart
              hours={overview.by_hour}
              timezoneLabel="all brands · brand local time"
            />
          )}

          {/* Row: Weekday breakdown across all brands */}
          {overview.by_weekday && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-12 gap-5 items-stretch">
              <WeekdayEngagementChart
                weekdays={overview.by_weekday}
                className="lg:col-span-5 xl:col-span-4"
              />
            </div>
          )}

          <p className="text-xs text-text2 text-center pt-2">
            Click any brand card or use the selector above to inspect individual brand performance.
          </p>
        </div>
      )}

      {/* ── Brand Detail View ───────────────────────── */}
      {selectedFanpageId && detail && (
        <div className="space-y-5">
          {/* Back button */}
          <button
            type="button"
            onClick={() => setSelectedFanpageId(null)}
            className="text-xs font-semibold text-text2 hover:text-text inline-flex items-center gap-1 cursor-pointer"
          >
            <Icon icon="solar:arrow-left-linear" width={14} />
            <span>Back to all brands</span>
          </button>

          {/* KPI Cards (4 cards) */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="p-4 rounded-[14px] bg-surface border border-line flex flex-col justify-between gap-1.5">
              <span className="text-xs text-text2">Posts</span>
              <span className="text-2xl font-bold text-text tabular-nums">
                {detail.totals.posts}
              </span>
              <span className="text-xs text-text2">
                {detail.totals.posts_with_metrics} with metrics from Repliz
              </span>
            </div>

            <div className="p-4 rounded-[14px] bg-surface border border-line flex flex-col justify-between gap-1.5">
              <span className="text-xs text-text2">Total engagement</span>
              <span className="text-2xl font-bold text-text tabular-nums">
                {detail.totals.engagement.toLocaleString()}
              </span>
              <div className="flex items-center gap-1.5">
                {renderTrendBadge(detail.trend)}
              </div>
            </div>

            <div className="p-4 rounded-[14px] bg-surface border border-line flex flex-col justify-between gap-1.5">
              <span className="text-xs text-text2">Median per post</span>
              <span className="text-2xl font-bold text-text tabular-nums">
                {overview?.fanpages.find((f) => f.fanpage_id === selectedFanpageId)
                  ?.eng_final_median ?? "—"}
              </span>
              <span className="text-xs text-text2">
                Across {detail.totals.posts_with_metrics} tracked posts
              </span>
            </div>

            <div className="p-4 rounded-[14px] bg-surface border border-line flex flex-col justify-between gap-1.5">
              <span className="text-xs text-text2">Likes · comments · shares</span>
              <span className="text-lg sm:text-xl font-bold text-text tabular-nums truncate">
                {detail.totals.likes.toLocaleString()} · {detail.totals.comments.toLocaleString()} ·{" "}
                {detail.totals.shares.toLocaleString()}
              </span>
              <span className="text-xs text-text2">
                {overview?.fanpages.find((f) => f.fanpage_id === selectedFanpageId)
                  ?.best_hour_local !== null
                  ? `Best hour ${String(
                      overview?.fanpages.find((f) => f.fanpage_id === selectedFanpageId)
                        ?.best_hour_local
                    ).padStart(2, "0")}:00`
                  : "Engagement breakdown"}
              </span>
            </div>
          </div>

          {/* Section: Median engagement by hour */}
          <HourEngagementChart
            hours={detail.by_hour}
            timezoneLabel={`brand time · ${detail.fanpage.timezone}`}
          />

          {/* Row: Daily engagement & Weekday breakdown */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-stretch">
            {/* Daily engagement */}
            <section
              aria-labelledby="daily-heading"
              className="lg:col-span-7 xl:col-span-8 bg-surface border border-line rounded-[14px] p-5 flex flex-col justify-between gap-3"
            >
              <h2 id="daily-heading" className="text-[15px] font-semibold text-text">
                Daily engagement
              </h2>

              <div className="w-full h-48 relative flex items-center">
                {dailyPoints.length > 1 ? (
                  <svg
                    viewBox="0 0 600 180"
                    preserveAspectRatio="none"
                    role="img"
                    aria-label="Line chart: daily engagement trend"
                    className="w-full h-full"
                  >
                    <line x1="0" y1="45" x2="600" y2="45" stroke="var(--line)" />
                    <line x1="0" y1="90" x2="600" y2="90" stroke="var(--line)" />
                    <line x1="0" y1="135" x2="600" y2="135" stroke="var(--line)" />
                    <line x1="0" y1="179" x2="600" y2="179" stroke="var(--line)" />
                    <polyline
                      fill="none"
                      stroke="var(--pri)"
                      strokeWidth="2.5"
                      strokeLinejoin="round"
                      strokeLinecap="round"
                      points={polylineCoords}
                    />
                  </svg>
                ) : (
                  <div className="w-full text-center text-xs text-text2 py-12">
                    Insufficient daily data for trend line
                  </div>
                )}
              </div>

              {dailyPoints.length > 0 && (
                <div
                  aria-hidden="true"
                  className="flex justify-between text-[11px] text-text2 pt-1 border-t border-line"
                >
                  <span>
                    {new Date(dailyPoints[0].date).toLocaleDateString("en-GB", {
                      day: "numeric",
                      month: "short",
                    })}
                  </span>
                  {dailyPoints.length > 4 && (
                    <span>
                      {new Date(
                        dailyPoints[Math.floor(dailyPoints.length / 2)].date
                      ).toLocaleDateString("en-GB", {
                        day: "numeric",
                        month: "short",
                      })}
                    </span>
                  )}
                  <span>
                    {new Date(dailyPoints[dailyPoints.length - 1].date).toLocaleDateString(
                      "en-GB",
                      { day: "numeric", month: "short" }
                    )}
                  </span>
                </div>
              )}
            </section>

            {/* By weekday */}
            <WeekdayEngagementChart
              weekdays={detail.by_weekday}
              className="lg:col-span-5 xl:col-span-4"
            />
          </div>

          {/* Row: Content Type & Top Posts */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
            {/* By content type */}
            <section
              aria-labelledby="types-heading"
              className="lg:col-span-5 bg-surface border border-line rounded-[14px] p-5"
            >
              <h2 id="types-heading" className="text-[15px] font-semibold text-text mb-2.5">
                By content type
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm border-collapse">
                  <thead>
                    <tr className="text-xs text-text2 border-b border-line">
                      <th scope="col" className="pb-2 font-semibold">
                        Type
                      </th>
                      <th scope="col" className="pb-2 font-semibold text-right">
                        Posts
                      </th>
                      <th scope="col" className="pb-2 font-semibold text-right">
                        Median
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line text-xs">
                    {detail.by_content_type.map((t) => (
                      <tr key={t.content_type}>
                        <td className="py-2.5 text-text font-medium">{t.content_type}</td>
                        <td className="py-2.5 text-right text-text2 tabular-nums">{t.posts}</td>
                        <td className="py-2.5 text-right text-text font-bold tabular-nums">
                          {t.eng_median != null ? t.eng_median : "n<5"}
                        </td>
                      </tr>
                    ))}
                    {detail.by_content_type.length === 0 && (
                      <tr>
                        <td colSpan={3} className="py-6 text-center text-text2">
                          No content types recorded
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            {/* Top posts */}
            <section
              aria-labelledby="top-heading"
              className="lg:col-span-7 bg-surface border border-line rounded-[14px] p-5"
            >
              <h2 id="top-heading" className="text-[15px] font-semibold text-text mb-2.5">
                Top posts
              </h2>
              <ol className="divide-y divide-line m-0 p-0 list-none flex flex-col">
                {detail.top_posts.map((p, i) => (
                  <li key={p.job_id} className="flex items-center gap-3 py-2.5 text-xs">
                    <span className="w-5 text-text2 tabular-nums font-medium shrink-0">
                      #{i + 1}
                    </span>
                    <span
                      aria-hidden="true"
                      className="w-9 h-11 rounded-lg bg-surface2 border border-line shrink-0 flex items-center justify-center text-text2"
                    >
                      <Icon icon="solar:gallery-bold-duotone" width={16} />
                    </span>
                    <span className="flex-1 min-w-0 flex flex-col gap-0.5">
                      <span className="font-semibold text-text truncate" title={p.title}>
                        {p.title || "(Untitled post)"}
                      </span>
                      <span className="text-[11px] text-text2">
                        {p.content_type} ·{" "}
                        {new Date(p.scheduled_for).toLocaleDateString("en-GB", {
                          day: "numeric",
                          month: "short",
                        })}
                      </span>
                    </span>
                    <span className="font-bold text-text tabular-nums text-sm shrink-0">
                      {p.engagement.toLocaleString()}
                    </span>
                  </li>
                ))}
                {detail.top_posts.length === 0 && (
                  <li className="py-6 text-center text-text2">No top posts recorded yet</li>
                )}
              </ol>
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
