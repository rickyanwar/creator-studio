"use client";

import React from "react";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { getSchedulePreview } from "@/lib/api";
import { SchedulePreviewResponse } from "@/lib/types";

interface SchedulePreviewProps {
  destinationId: number;
}

export function SchedulePreview({ destinationId }: SchedulePreviewProps) {
  const { data, error, isLoading, mutate } = useSWR<SchedulePreviewResponse>(
    destinationId ? `schedule-preview-${destinationId}` : null,
    () => getSchedulePreview(destinationId, 6).then((res) => res.data),
    {
      revalidateOnFocus: false,
    }
  );

  return (
    <div className="p-3.5 rounded-lg bg-bg-paper-hover border border-divider-soft space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon icon="solar:calendar-date-bold-duotone" width={18} className="text-primary-main" />
          <h4 className="text-xs font-semibold text-text-primary">
            Next available slots (saved settings)
          </h4>
        </div>
        <button
          type="button"
          onClick={() => mutate()}
          disabled={isLoading}
          className="btn-secondary text-[11px] py-1 px-2.5 flex items-center gap-1.5"
          title="Refresh preview slots"
        >
          <Icon
            icon="solar:refresh-linear"
            width={13}
            className={isLoading ? "animate-spin text-text-secondary" : "text-text-secondary"}
          />
          <span>Refresh</span>
        </button>
      </div>

      <div className="text-[11px] text-text-secondary flex flex-wrap gap-x-3 gap-y-1">
        <span>
          Timezone: <strong className="text-text-primary">{data?.timezone || "—"}</strong>
        </span>
        {data?.target_country && (
          <span>
            Country: <strong className="text-text-primary">{data.target_country}</strong>
          </span>
        )}
        <span>
          Limit: <strong className="text-text-primary">{data?.daily_limit ?? "—"}/day</strong>
        </span>
        <span>
          Spacing: <strong className="text-text-primary">{data?.min_gap_minutes ?? "—"}m min</strong>
        </span>
      </div>

      {isLoading && (
        <div className="py-4 text-center text-xs text-text-secondary flex items-center justify-center gap-2">
          <Icon icon="solar:refresh-linear" width={14} className="animate-spin text-primary-main" />
          <span>Loading upcoming publishing slots…</span>
        </div>
      )}

      {error && !isLoading && (
        <div className="p-2.5 rounded bg-error-main/10 border border-error-main/20 text-xs text-error-main flex items-center justify-between">
          <span>Failed to load preview slots.</span>
          <button
            type="button"
            onClick={() => mutate()}
            className="underline hover:text-error-dark font-medium"
          >
            Retry
          </button>
        </div>
      )}

      {!isLoading && !error && data?.slots && data.slots.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
          {data.slots.map((s, idx) => {
            const dateObj = new Date(s.slot_utc);
            const dateFormatted = !isNaN(dateObj.getTime())
              ? dateObj.toLocaleString("en-GB", {
                  timeZone: data.timezone,
                  weekday: "short",
                  day: "numeric",
                  month: "short",
                  hour: "2-digit",
                  minute: "2-digit",
                  hour12: false,
                })
              : s.slot_local;

            return (
              <div
                key={idx}
                className="px-2.5 py-1.5 rounded bg-bg-paper border border-divider-soft flex items-center justify-between text-xs"
              >
                <span className="font-mono text-[11px] text-text-primary">
                  {dateFormatted}
                </span>
                <span className="text-[10px] text-text-secondary uppercase">
                  Slot #{idx + 1}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {!isLoading && !error && data?.slots && data.slots.length === 0 && (
        <div className="py-2 text-center text-xs text-text-secondary">
          No available candidate slots found.
        </div>
      )}

      <div className="pt-1 border-t border-divider-soft flex flex-col gap-1 text-[10px] text-text-secondary">
        <p className="italic">
          {data?.note || "Estimated local activity windows; no account audience Insights connected."}
        </p>
        <p className="text-text-muted">
          Based on saved settings. Save changes to update this preview.
        </p>
      </div>
    </div>
  );
}
