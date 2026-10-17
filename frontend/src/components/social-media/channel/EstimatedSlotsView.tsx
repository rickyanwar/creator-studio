"use client";

import React, { useState } from "react";
import type { EstimatedTimesOut } from "@/lib/socialBrandsApi";

interface Props {
  estimatedTimes?: EstimatedTimesOut | null;
  isLoading?: boolean;
  error?: string | null;
  onGoPacing?: () => void;
  showPacingLink?: boolean;
}

export function EstimatedSlotsView({
  estimatedTimes,
  isLoading,
  error,
  onGoPacing,
  showPacingLink = true,
}: Props) {
  const [clockMode, setClockMode] = useState<"audience" | "mine">("audience");

  if (isLoading) {
    return (
      <div className="rounded-xl border border-divider-soft bg-bg-paper p-4 text-center text-sm text-text-secondary">
        Loading estimated schedule slots...
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-divider-soft bg-bg-paper p-4 text-xs text-text-secondary">
        Could not load estimated schedule: {error}
      </div>
    );
  }

  const slots = estimatedTimes?.slots || [];
  const tz = estimatedTimes?.timezone || "UTC";
  const city = tz.split("/")[1]?.replace(/_/g, " ") || tz;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="text-sm font-semibold text-text-primary">
            Today&apos;s slots — estimated time and method
          </span>
          <p className="text-xs text-text-secondary">
            Audience time ({city}) · your time (WIB) · exact time is set when the post is ready
          </p>
        </div>
        <div className="flex items-center gap-1.5 rounded-full border border-divider-soft p-1 bg-bg-default text-xs">
          <button
            type="button"
            onClick={() => setClockMode("audience")}
            className={`px-3 py-1 rounded-full font-medium transition ${
              clockMode === "audience"
                ? "bg-primary-main text-white"
                : "text-text-secondary hover:text-text-primary"
            }`}
          >
            Audience ({city})
          </button>
          <button
            type="button"
            onClick={() => setClockMode("mine")}
            className={`px-3 py-1 rounded-full font-medium transition ${
              clockMode === "mine"
                ? "bg-primary-main text-white"
                : "text-text-secondary hover:text-text-primary"
            }`}
          >
            My time (WIB)
          </button>
        </div>
      </div>

      {slots.length === 0 ? (
        <div className="rounded-xl border border-divider-soft bg-bg-paper p-6 text-center text-xs text-text-secondary">
          No slots scheduled for today yet. Check daily posts target and active hours.
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2.5">
          {slots.map((s, idx) => {
            const isPhone = s.method === "phone";
            const mainTime = clockMode === "audience" ? s.local_time : s.wib_time;
            const subTime = clockMode === "audience" ? `${s.wib_time} WIB` : `${s.local_time} ${city}`;

            return (
              <div
                key={s.delivery_id ?? s.index ?? idx}
                className={`p-3 rounded-xl border flex flex-col gap-1.5 transition ${
                  isPhone
                    ? "bg-blue-950/20 border-blue-900/40"
                    : "bg-purple-950/20 border-purple-900/40"
                }`}
              >
                <div className="flex items-center justify-between text-[11px] text-text-secondary">
                  <span>Slot {(s.index ?? idx) + 1}</span>
                  <span className="capitalize text-[10px] opacity-80">{s.status || "Planned"}</span>
                </div>
                <div className="text-lg font-bold tabular-nums text-text-primary">
                  ~{mainTime}
                </div>
                <div className="text-xs text-text-secondary tabular-nums">
                  {subTime}
                </div>
                <div
                  className={`text-xs font-semibold ${
                    isPhone ? "text-blue-400" : "text-purple-400"
                  }`}
                >
                  {isPhone ? "Phone" : "Repliz"}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showPacingLink && onGoPacing && (
        <button
          type="button"
          onClick={onGoPacing}
          className="self-start text-xs font-semibold text-primary-main hover:text-primary-dark transition pt-1"
        >
          Change times and audience location in Pacing →
        </button>
      )}
    </div>
  );
}
