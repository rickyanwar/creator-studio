"use client";

import { useState } from "react";
import useSWR from "swr";
import { getAnalyticsOverview, getFanpageAnalytics } from "@/lib/api";
import type { AnalyticsOverview, FanpageAnalytics } from "@/lib/types";
import { Icon } from "@iconify/react";

export default function AnalyticsPage() {
  const [days, setDays] = useState<number>(28);
  const [selectedFanpageId, setSelectedFanpageId] = useState<number | null>(null);

  const { data: overview, error: overviewError } = useSWR(
    ["analytics-overview", days],
    ([_, d]) => getAnalyticsOverview(d).then((r) => r.data as AnalyticsOverview)
  );

  const { data: detail, error: detailError } = useSWR(
    selectedFanpageId ? ["analytics-fanpage", selectedFanpageId, days] : null,
    ([_, id, d]) => getFanpageAnalytics(id, d).then((r) => r.data as FanpageAnalytics)
  );

  function renderStatusBanner(metrics: AnalyticsOverview["metrics"]) {
    if (!metrics.enabled) {
      return (
        <div className="bg-warning-main/10 border border-warning-main/20 text-warning-main px-4 py-3 rounded-lg text-sm font-medium mb-6">
          Post metrics are off. They need the Repliz Gold plan — enable them in Settings.
        </div>
      );
    }
    if (metrics.plan_status === "plan_required") {
      return (
        <div className="bg-warning-main/10 border border-warning-main/20 text-warning-main px-4 py-3 rounded-lg text-sm font-medium mb-6">
          Repliz says the current plan has no statistics access (Gold needed). Metrics resume automatically after the upgrade.
        </div>
      );
    }
    if (metrics.plan_status === "error" && metrics.last_error) {
      return (
        <div className="bg-error-main/10 border border-error-main/20 text-error-main px-4 py-3 rounded-lg text-sm font-medium mb-6">
          {metrics.last_error}
        </div>
      );
    }
    return null;
  }

  function renderTrendBadge(trend: AnalyticsOverview["fanpages"][0]["trend"]) {
    const { label, change_pct } = trend;
    if (label === "rising") {
      return (
        <span className="inline-flex items-center gap-1 bg-[rgba(0,167,111,0.16)] text-primary-main px-2 py-0.5 rounded text-xs font-bold">
          <Icon icon="solar:arrow-right-up-bold" width={12} />
          {change_pct?.toFixed(1)}%
        </span>
      );
    }
    if (label === "falling") {
      return (
        <span className="inline-flex items-center gap-1 bg-[rgba(255,86,48,0.16)] text-error-main px-2 py-0.5 rounded text-xs font-bold">
          <Icon icon="solar:arrow-right-down-bold" width={12} />
          {change_pct?.toFixed(1)}%
        </span>
      );
    }
    if (label === "flat") {
      return (
        <span className="inline-flex items-center gap-1 bg-divider-soft text-text-secondary px-2 py-0.5 rounded text-xs font-bold">
          <Icon icon="solar:arrow-right-bold" width={12} />
          {change_pct?.toFixed(1)}%
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1 bg-divider-soft text-text-disabled px-2 py-0.5 rounded text-xs font-bold">
        n/a
      </span>
    );
  }

  return (
    <div className="space-y-6 pb-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Analytics</h1>
          <p className="text-sm text-text-secondary mt-1">Fanpage performance and engagement metrics</p>
        </div>
        <div className="flex gap-2">
          {[7, 28, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                days === d
                  ? "bg-primary-main text-white"
                  : "bg-surface-secondary text-text-secondary hover:bg-bg-paper-hover border border-border-default hover:border-border-hover"
              }`}
            >
              {d} Days
            </button>
          ))}
        </div>
      </div>

      {!overview && !overviewError && (
        <div className="animate-pulse space-y-4">
          <div className="h-24 bg-bg-paper-hover rounded-xl" />
        </div>
      )}

      {overview && (
        <>
          {renderStatusBanner(overview.metrics)}

          {!selectedFanpageId && (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
              {overview.fanpages.map((fp) => (
                <div
                  key={fp.fanpage_id}
                  onClick={() => setSelectedFanpageId(fp.fanpage_id)}
                  className="card cursor-pointer hover:border-primary-main transition-colors group"
                >
                  <div className="flex items-start justify-between mb-4">
                    <div>
                      <h3 className="text-lg font-bold text-text-primary group-hover:text-primary-main transition-colors">
                        {fp.name}
                      </h3>
                      <p className="text-xs text-text-secondary mt-0.5">{fp.timezone}</p>
                    </div>
                    {renderTrendBadge(fp.trend)}
                  </div>
                  
                  <div className="grid grid-cols-3 gap-4 border-t border-hairline pt-4">
                    <div>
                      <p className="text-xs text-text-secondary mb-1">Posts</p>
                      <p className="text-lg font-semibold text-text-primary">{fp.posts}</p>
                    </div>
                    <div>
                      <p className="text-xs text-text-secondary mb-1">Median Eng</p>
                      <p className="text-lg font-semibold text-text-primary">
                        {fp.eng_final_median ?? "—"}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-text-secondary mb-1">Best Hour</p>
                      <p className="text-lg font-semibold text-text-primary">
                        {fp.best_hour_local !== null ? `${fp.best_hour_local}:00` : "—"}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {selectedFanpageId && (
        <div className="space-y-6">
          <button
            onClick={() => setSelectedFanpageId(null)}
            className="text-sm font-medium text-text-secondary hover:text-text-primary flex items-center gap-1"
          >
            <Icon icon="solar:arrow-left-bold-duotone" />
            Back to Overview
          </button>

          {!detail && !detailError && (
            <div className="animate-pulse space-y-4">
              <div className="h-24 bg-bg-paper-hover rounded-xl" />
              <div className="h-64 bg-bg-paper-hover rounded-xl" />
            </div>
          )}

          {detail && (
            <>
              {/* KPI Row */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-5">
                <div className="card">
                  <p className="text-sm text-text-secondary mb-1">Total Posts</p>
                  <div className="flex items-end gap-2">
                    <p className="text-3xl font-extrabold text-text-primary">{detail.totals.posts}</p>
                    <p className="text-sm text-text-secondary mb-1">({detail.totals.posts_with_metrics} tracked)</p>
                  </div>
                </div>
                <div className="card">
                  <p className="text-sm text-text-secondary mb-1">Total Engagement</p>
                  <p className="text-3xl font-extrabold text-text-primary">{detail.totals.engagement.toLocaleString()}</p>
                </div>
                <div className="card">
                  <p className="text-sm text-text-secondary mb-1">Median Engagement</p>
                  <div className="flex items-center gap-2">
                    <p className="text-3xl font-extrabold text-text-primary">
                      {overview?.fanpages.find((f) => f.fanpage_id === selectedFanpageId)?.eng_final_median ?? "—"}
                    </p>
                    {renderTrendBadge(detail.trend)}
                  </div>
                </div>
                <div className="card">
                  <p className="text-sm text-text-secondary mb-1">Likes / Comments / Shares</p>
                  <p className="text-lg font-bold text-text-primary mt-1">
                    {detail.totals.likes.toLocaleString()} / {detail.totals.comments.toLocaleString()} / {detail.totals.shares.toLocaleString()}
                  </p>
                </div>
              </div>

              {/* Charts Row */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                {/* 24 Hour Bars */}
                <div className="card">
                  <h3 className="text-sm font-bold text-text-primary mb-1">Engagement by Hour</h3>
                  <p className="text-xs text-text-secondary mb-6">local time ({detail.fanpage.timezone})</p>
                  
                  <div className="h-48 flex items-end gap-1">
                    {detail.by_hour.map((h) => {
                      const maxEng = Math.max(...detail.by_hour.map((x) => x.eng_median ?? 0), 1);
                      const height = h.eng_median === null ? "10%" : `${Math.max(10, ((h.eng_median || 0) / maxEng) * 100)}%`;
                      const isNull = h.eng_median === null;
                      
                      return (
                        <div key={h.hour} className="flex-1 flex flex-col items-center group relative">
                          {/* Tooltip */}
                          <div className="absolute bottom-full mb-2 hidden group-hover:block z-10 bg-[rgba(22,28,36,0.9)] text-white text-xs rounded py-1 px-2 whitespace-nowrap">
                            <p className="font-bold">{h.hour}:00</p>
                            <p>{isNull ? "n < 5" : `Median: ${h.eng_median}`}</p>
                            <p>{h.posts} posts</p>
                          </div>
                          
                          {/* Bar */}
                          <div
                            className={`w-full rounded-t-sm transition-colors ${
                              isNull ? "bg-divider-soft" : "bg-primary-main hover:opacity-80"
                            }`}
                            style={{ height }}
                          ></div>
                          
                          {/* Label */}
                          <div className="text-[10px] text-text-secondary mt-2">
                            {h.hour % 6 === 0 ? h.hour : ""}
                          </div>
                          {isNull && (
                            <div className="absolute bottom-full text-[9px] text-text-disabled mb-0.5">
                              n&lt;5
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Weekday Bars */}
                <div className="card">
                  <h3 className="text-sm font-bold text-text-primary mb-1">Engagement by Day</h3>
                  <p className="text-xs text-text-secondary mb-6">local time ({detail.fanpage.timezone})</p>
                  
                  <div className="h-48 flex items-end gap-2">
                    {detail.by_weekday.map((w) => {
                      const maxEng = Math.max(...detail.by_weekday.map((x) => x.eng_median ?? 0), 1);
                      const height = w.eng_median === null ? "10%" : `${Math.max(10, ((w.eng_median || 0) / maxEng) * 100)}%`;
                      const isNull = w.eng_median === null;
                      
                      return (
                        <div key={w.weekday} className="flex-1 flex flex-col items-center group relative">
                          <div className="absolute bottom-full mb-2 hidden group-hover:block z-10 bg-[rgba(22,28,36,0.9)] text-white text-xs rounded py-1 px-2 whitespace-nowrap">
                            <p className="font-bold">{w.label}</p>
                            <p>{isNull ? "n < 5" : `Median: ${w.eng_median}`}</p>
                            <p>{w.posts} posts</p>
                          </div>
                          
                          <div
                            className={`w-full rounded-t-sm transition-colors ${
                              isNull ? "bg-divider-soft" : "bg-[#1877F2] hover:opacity-80"
                            }`}
                            style={{ height }}
                          ></div>
                          
                          <div className="text-xs text-text-secondary mt-2">
                            {w.label}
                          </div>
                          {isNull && (
                            <div className="absolute bottom-full text-[9px] text-text-disabled mb-0.5">
                              n&lt;5
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>

              {/* Daily Trend Line (simulated with bars) */}
              <div className="card">
                <h3 className="text-sm font-bold text-text-primary mb-6">Daily Engagement</h3>
                <div className="h-32 flex items-end gap-0.5">
                  {detail.daily.map((d, i) => {
                    const maxEng = Math.max(...detail.daily.map((x) => x.engagement), 1);
                    const height = `${Math.max(2, (d.engagement / maxEng) * 100)}%`;
                    
                    return (
                      <div key={d.date} className="flex-1 flex flex-col items-center group relative">
                        <div className="absolute bottom-full mb-2 hidden group-hover:block z-10 bg-[rgba(22,28,36,0.9)] text-white text-xs rounded py-1 px-2 whitespace-nowrap">
                          <p className="font-bold">{d.date}</p>
                          <p>Eng: {d.engagement.toLocaleString()}</p>
                          <p>{d.posts} posts</p>
                        </div>
                        <div
                          className="w-full bg-[#00A76F] hover:opacity-80 rounded-t-sm"
                          style={{ height }}
                        ></div>
                        {/* Only show a few labels to avoid crowding */}
                        {(i === 0 || i === detail.daily.length - 1 || i === Math.floor(detail.daily.length / 2)) && (
                          <div className="text-[10px] text-text-secondary mt-1 whitespace-nowrap hidden sm:block">
                            {new Date(d.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Tables Row */}
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                <div className="card">
                  <h3 className="text-sm font-bold text-text-primary mb-4">By Content Type</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-divider-soft text-text-secondary">
                          <th className="pb-2 font-medium">Type</th>
                          <th className="pb-2 font-medium">Posts</th>
                          <th className="pb-2 font-medium">Median Eng</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-hairline">
                        {detail.by_content_type.map((c) => (
                          <tr key={c.content_type}>
                            <td className="py-2 text-text-primary">{c.content_type}</td>
                            <td className="py-2 text-text-secondary">{c.posts}</td>
                            <td className="py-2 font-medium text-text-primary">
                              {c.eng_median === null ? (
                                <span className="text-text-disabled text-xs">n&lt;5</span>
                              ) : (
                                c.eng_median
                              )}
                            </td>
                          </tr>
                        ))}
                        {detail.by_content_type.length === 0 && (
                          <tr>
                            <td colSpan={3} className="py-4 text-center text-text-secondary text-xs">No data</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="card">
                  <h3 className="text-sm font-bold text-text-primary mb-4">Top Posts</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-divider-soft text-text-secondary">
                          <th className="pb-2 font-medium">Title</th>
                          <th className="pb-2 font-medium">Date</th>
                          <th className="pb-2 font-medium">Type</th>
                          <th className="pb-2 font-medium text-right">Eng</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-hairline">
                        {detail.top_posts.map((p) => (
                          <tr key={p.job_id}>
                            <td className="py-2 text-text-primary font-medium truncate max-w-[200px]" title={p.title}>
                              {p.title || "(No title)"}
                            </td>
                            <td className="py-2 text-text-secondary text-xs whitespace-nowrap">
                              {new Date(p.scheduled_for).toLocaleDateString()}
                            </td>
                            <td className="py-2 text-text-secondary text-xs">
                              {p.content_type}
                            </td>
                            <td className="py-2 text-primary-main font-bold text-right">
                              {p.engagement.toLocaleString()}
                            </td>
                          </tr>
                        ))}
                        {detail.top_posts.length === 0 && (
                          <tr>
                            <td colSpan={4} className="py-4 text-center text-text-secondary text-xs">No posts yet</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
