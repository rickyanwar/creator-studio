"use client";

import useSWR from "swr";
import { api } from "@/lib/api";
import type { DestinationDayPlan } from "@/lib/types";

const MODE_NAMES: Record<string, string> = {
  news: "News",
  ig: "IG",
  ai_video: "AI video",
  discussion: "Discussion",
  pinterest: "Pinterest",
  facebook_photo: "FB photo",
  yt_clip: "YouTube clips",
};

function formatCityTime(timezoneName: string, localNowIso?: string): string {
  try {
    const city = timezoneName.split("/").pop()?.replace(/_/g, " ") || timezoneName;
    const d = localNowIso ? new Date(localNowIso) : new Date();
    const timeStr = new Intl.DateTimeFormat("en-GB", {
      timeZone: timezoneName,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(d);
    return `${city} ${timeStr}`;
  } catch {
    return timezoneName;
  }
}

function formatSlotWib(isoStr: string): string {
  try {
    const d = new Date(isoStr);
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Jakarta",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(d);
  } catch {
    return isoStr;
  }
}

interface TodayPlanCardProps {
  destinationId?: number | null;
  brandId?: number | null;
}

export default function TodayPlanCard({ destinationId, brandId }: TodayPlanCardProps) {
  const params: Record<string, number> = {};
  if (destinationId) params.destination_id = destinationId;
  if (brandId) params.content_project_id = brandId;

  const { data: plans = [], isLoading } = useSWR<DestinationDayPlan[]>(
    ["/social-media/day-plan", destinationId, brandId],
    () => api.get<DestinationDayPlan[]>("/social-media/day-plan", { params }).then((r) => r.data),
    { refreshInterval: 60000 }
  );

  return (
    <div className="bg-surface border border-line rounded-xl p-4 mb-4 shadow-sm">
      <div className="flex items-center justify-between pb-2.5 mb-3 border-b border-line">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-text2">
          Today&apos;s plan
        </h2>
      </div>

      {isLoading && plans.length === 0 && (
        <div className="py-2 text-xs text-text2">Loading plan…</div>
      )}

      {!isLoading && plans.length === 0 && (
        <div className="py-2 text-xs text-text2 italic">No active channels found.</div>
      )}

      <div className="divide-y divide-line">
        {plans.map((plan) => {
          const remainingEntries = Object.entries(plan.remaining).filter(([, count]) => count > 0);
          const maxSlots = 8;
          const displaySlots = plan.upcoming_slots.slice(0, maxSlots);
          const moreCount = plan.upcoming_slots.length - maxSlots;

          return (
            <div
              key={plan.destination_id}
              className="py-2.5 first:pt-0 last:pb-0 flex flex-col md:flex-row md:items-center justify-between gap-2.5 text-xs"
            >
              {/* Channel and Modes */}
              <div className="flex flex-wrap items-center gap-2 min-w-0">
                <span className="font-semibold text-text">{plan.name}</span>
                <span className="text-text2 font-mono text-[11px] bg-surface2 px-1.5 py-0.5 rounded border border-line">
                  {formatCityTime(plan.timezone, plan.local_now)}
                </span>
                <div className="flex flex-wrap items-center gap-1.5">
                  {remainingEntries.length > 0 ? (
                    remainingEntries.map(([mode, count]) => (
                      <span
                        key={mode}
                        className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium bg-surface2 text-text2 border border-line"
                      >
                        {MODE_NAMES[mode] || mode} {count}
                      </span>
                    ))
                  ) : (
                    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium bg-surface2 text-text2 border border-line">
                      Remaining {plan.remaining_total}
                    </span>
                  )}
                </div>
              </div>

              {/* Upcoming slots in WIB */}
              <div className="flex items-center md:justify-end text-right">
                {plan.upcoming_slots.length === 0 ? (
                  <span className="text-text2 italic text-[11px]">No slots left today</span>
                ) : (
                  <div className="text-[11px] text-text font-mono">
                    {displaySlots.map(formatSlotWib).join(", ")} WIB
                    {moreCount > 0 && (
                      <span className="text-text2 ml-1">+{moreCount} more</span>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
