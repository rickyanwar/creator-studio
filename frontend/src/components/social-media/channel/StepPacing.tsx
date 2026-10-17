"use client";

import React from "react";
import { getAllTimezones } from "./channelUtils";

interface Props {
  isStep3Valid: boolean;
  useBrandDefaults: boolean;
  setUseBrandDefaults: (val: boolean) => void;
  brandTz: string;
  quietHoursStr: string;
  mode: "phone" | "repliz" | "mixed";
  effectiveDailyTarget: number;
  dailyPosts: number;
  setDailyPosts: (n: number) => void;
  minGapMinutes: number;
  setMinGapMinutes: (n: number) => void;
  activeTimezone: string;
  setCustomTimezone: (tz: string) => void;
  supportedMedia: string[];
  handleToggleMedia: (media: string) => void;
}

export function StepPacing({
  isStep3Valid,
  useBrandDefaults,
  setUseBrandDefaults,
  brandTz,
  quietHoursStr,
  mode,
  effectiveDailyTarget,
  dailyPosts,
  setDailyPosts,
  minGapMinutes,
  setMinGapMinutes,
  activeTimezone,
  setCustomTimezone,
  supportedMedia,
  handleToggleMedia,
}: Props) {
  const allTzs = getAllTimezones();

  return (
    <section
      aria-labelledby="step4-h"
      className={`card p-5 space-y-4 transition ${
        !isStep3Valid ? "opacity-50 pointer-events-none" : ""
      }`}
    >
      <h2 id="step4-h" className="text-sm font-bold text-text-primary flex items-center gap-2">
        <span className="w-5 h-5 rounded-full bg-primary-main/20 text-primary-main text-xs flex items-center justify-center font-bold">
          4
        </span>
        Pacing
      </h2>

      <label className="flex items-center gap-2.5 cursor-pointer text-xs font-medium text-text-primary">
        <input
          type="checkbox"
          checked={useBrandDefaults}
          onChange={(e) => setUseBrandDefaults(e.target.checked)}
          className="w-4 h-4 rounded text-primary-main focus:ring-primary-main"
        />
        <span>
          Use brand defaults ({brandTz}, quiet {quietHoursStr})
        </span>
      </label>

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <label htmlFor="daily-posts-input" className="label">
            Posts / day
          </label>
          {mode === "mixed" ? (
            <div className="h-[42px] px-3 rounded-md bg-bg-paper-hover border border-grey-700 flex items-center font-bold text-sm text-text-primary">
              {effectiveDailyTarget} (Split sum)
            </div>
          ) : (
            <input
              id="daily-posts-input"
              type="number"
              min={1}
              max={1000}
              value={dailyPosts}
              onChange={(e) => setDailyPosts(parseInt(e.target.value, 10) || 1)}
              className="input-rect"
            />
          )}
        </div>

        <div>
          <label htmlFor="min-gap-input" className="label">
            Minimum gap (min)
          </label>
          <input
            id="min-gap-input"
            type="number"
            min={1}
            value={minGapMinutes}
            onChange={(e) => setMinGapMinutes(parseInt(e.target.value, 10) || 1)}
            className="input-rect"
          />
        </div>

        <div>
          <label htmlFor="tz-select" className="label">
            Time zone
          </label>
          <select
            id="tz-select"
            disabled={useBrandDefaults}
            value={activeTimezone}
            onChange={(e) => setCustomTimezone(e.target.value)}
            className="input-rect"
          >
            {Array.from(new Set([brandTz, ...allTzs])).map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </div>

        <div>
          <span className="label">Quiet hours</span>
          <div className="h-[42px] px-3 rounded-md bg-bg-paper-hover border border-grey-700 flex items-center text-xs text-text-secondary">
            {quietHoursStr}
          </div>
        </div>
      </div>

      <div className="space-y-2 pt-2">
        <span className="label">Media accepted</span>
        <div className="flex flex-wrap items-center gap-4 text-xs">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={supportedMedia.includes("image")}
              onChange={() => handleToggleMedia("image")}
              className="w-4 h-4 rounded text-primary-main focus:ring-primary-main"
            />
            <span>Single image</span>
          </label>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={supportedMedia.includes("album")}
              onChange={() => handleToggleMedia("album")}
              className="w-4 h-4 rounded text-primary-main focus:ring-primary-main"
            />
            <span>Album</span>
          </label>

          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={supportedMedia.includes("video")}
              onChange={() => handleToggleMedia("video")}
              className="w-4 h-4 rounded text-primary-main focus:ring-primary-main"
            />
            <span>Video · Reel</span>
          </label>
        </div>
      </div>
    </section>
  );
}
