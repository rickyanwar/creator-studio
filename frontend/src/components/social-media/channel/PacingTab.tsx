"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { Icon } from "@iconify/react";
import type { SocialDestination, SocialDestinationUpdate } from "@/lib/types";
import type { BrandSummaryOut, EstimatedTimesOut } from "@/lib/socialBrandsApi";
import {
  getAllTimezones,
  getTimezoneDiffWib,
  getErrorMessage,
} from "./channelUtils";
import { EstimatedSlotsView } from "./EstimatedSlotsView";

interface Props {
  destination: SocialDestination;
  brand?: BrandSummaryOut | null;
  estimatedTimes?: EstimatedTimesOut | null;
  isLoadingEstimates?: boolean;
  onSave: (payload: SocialDestinationUpdate) => Promise<void>;
}

export function PacingTab({
  destination,
  brand,
  estimatedTimes,
  isLoadingEstimates,
  onSave,
}: Props) {
  const [timezone, setTimezone] = useState(destination.timezone || "Europe/London");
  const [dailyTarget, setDailyTarget] = useState<number>(destination.daily_target || 6);
  const [minGapMinutes, setMinGapMinutes] = useState<number>(destination.min_gap_minutes || 20);

  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    setTimezone(destination.timezone || "Europe/London");
    setDailyTarget(destination.daily_target || 6);
    setMinGapMinutes(destination.min_gap_minutes || 20);
  }, [destination]);

  const allTzs = getAllTimezones();
  const tzOptions = Array.from(new Set([timezone, ...allTzs])).sort();
  const tzDiffText = getTimezoneDiffWib(timezone);

  // Quiet hours from brand
  const sleepStart = brand?.publish_sleep_start_hour ?? 0;
  const sleepEnd = brand?.publish_sleep_end_hour ?? 7;
  const formatHour = (h: number) => (h < 10 ? `0${h}:00` : `${h}:00`);
  const quietHoursStr = `${formatHour(sleepStart)}–${formatHour(sleepEnd)}`;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (dailyTarget < 1 || dailyTarget > 1000) {
      setSaveError("Posts / day must be between 1 and 1000.");
      return;
    }
    if (minGapMinutes < 1) {
      setSaveError("Minimum gap must be at least 1 minute.");
      return;
    }

    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);

    try {
      const mode = destination.mode;
      let phoneTarget = destination.phone_daily_target;
      let replizTarget = destination.repliz_daily_target;

      if (mode === "phone") {
        phoneTarget = dailyTarget;
        replizTarget = 0;
      } else if (mode === "repliz") {
        phoneTarget = 0;
        replizTarget = dailyTarget;
      } else if (mode === "mixed") {
        // Adjust repliz target to keep sum == dailyTarget
        const oldSum = phoneTarget + replizTarget;
        if (oldSum !== dailyTarget) {
          const ratio = oldSum > 0 ? phoneTarget / oldSum : 0.5;
          phoneTarget = Math.round(dailyTarget * ratio);
          replizTarget = dailyTarget - phoneTarget;
        }
      }

      await onSave({
        timezone,
        daily_target: dailyTarget,
        phone_daily_target: phoneTarget,
        repliz_daily_target: replizTarget,
        min_gap_minutes: minGapMinutes,
      });

      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 4000);
    } catch (err) {
      setSaveError(getErrorMessage(err));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      {/* ── 1. Audience Location ── */}
      <section className="card p-6 space-y-4">
        <div>
          <h2 className="text-base font-bold text-text">
            Audience location
          </h2>
          <p className="text-xs text-text2 mt-0.5">
            Where most of this channel&apos;s followers live. Posting times are planned on their local clock, not yours.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">Audience Time Zone</label>
            <select
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className="input-rect"
            >
              {tzOptions.map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </select>
          </div>

          <div>
            <span className="label">Difference from your time</span>
            <div className="h-[42px] px-3 rounded-md bg-surface2 border border-line flex items-center gap-2 text-xs text-text">
              <Icon icon="solar:clock-circle-bold" className="text-text2" width={16} />
              <span>{tzDiffText}</span>
            </div>
          </div>
        </div>

        <p className="text-xs text-text2">
          One channel plans on one time zone. If followers are split across regions, choose where most of them live.
        </p>
      </section>

      {/* ── 2. When posts may go out ── */}
      <section className="card p-6 space-y-4">
        <div>
          <h2 className="text-base font-bold text-text">
            When posts may go out
          </h2>
          <p className="text-xs text-text2 mt-0.5">
            All pacing targets operate in the audience&apos;s local clock.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {/* Posts / day */}
          <div>
            <label className="label">Posts / day</label>
            <input
              type="number"
              min={1}
              max={1000}
              value={dailyTarget}
              onChange={(e) => setDailyTarget(parseInt(e.target.value, 10) || 1)}
              className="input-rect"
            />
          </div>

          {/* Minimum gap */}
          <div>
            <label className="label">Minimum gap (minutes)</label>
            <input
              type="number"
              min={1}
              value={minGapMinutes}
              onChange={(e) => setMinGapMinutes(parseInt(e.target.value, 10) || 1)}
              className="input-rect"
            />
          </div>

          {/* Quiet hours (read-only) */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="label !mb-0">Quiet hours (from brand)</span>
              {brand?.id && (
                <Link
                  href={`/brands/${brand.id}`}
                  className="text-[11px] font-semibold text-pri hover:opacity-90 transition"
                >
                  Change on brand →
                </Link>
              )}
            </div>
            <div className="h-[42px] px-3 rounded-md bg-surface2 border border-line flex items-center text-xs text-text2">
              <Icon icon="solar:moon-bold" className="text-warn mr-2" width={15} />
              <span>{quietHoursStr}</span>
            </div>
          </div>
        </div>

        {/* Built-in rules */}
        <div className="space-y-2 pt-1 border-t border-line text-xs text-text2">
          <div className="flex items-center gap-2">
            <Icon icon="solar:check-circle-bold" className="text-pri" width={15} />
            <span>Posts a little more around lunch (12–14) and evening (18–21) local time</span>
          </div>
          <div className="flex items-center gap-2">
            <Icon icon="solar:check-circle-bold" className="text-pri" width={15} />
            <span>Each time shifts by a few random minutes, so it doesn&apos;t look scheduled</span>
          </div>
        </div>
      </section>

      {/* Save Button & Feedback */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <span className="text-xs text-text2">
          Minimum gap is a floor. Shifts take effect on next planned slot generation.
        </span>
        <div className="flex items-center gap-3">
          {saveSuccess && (
            <span className="text-xs text-pri font-semibold flex items-center gap-1">
              <Icon icon="solar:check-circle-bold" width={14} />
              Saved pacing settings
            </span>
          )}
          <button
            type="submit"
            disabled={isSaving}
            className="btn-primary text-xs px-5 py-2.5"
          >
            {isSaving ? "Saving..." : "Save pacing settings"}
          </button>
        </div>
      </div>

      {saveError && (
        <div className="rounded-xl border border-bad/30 bg-bad-soft p-3 text-xs text-bad">
          {saveError}
        </div>
      )}

      {/* ── 3. Estimated Times Today ── */}
      <section className="card p-6">
        <EstimatedSlotsView
          estimatedTimes={estimatedTimes}
          isLoading={isLoadingEstimates}
          showPacingLink={false}
        />
      </section>
    </form>
  );
}
