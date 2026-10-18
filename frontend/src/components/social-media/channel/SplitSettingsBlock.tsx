"use client";

import React from "react";
import { Icon } from "@iconify/react";

interface Props {
  dailyTarget: number;
  phoneDailyTarget: number;
  setPhoneDailyTarget: (n: number) => void;
  replizDailyTarget: number;
  setReplizDailyTarget: (n: number) => void;
  isSplitValid: boolean;
}

export function SplitSettingsBlock({
  dailyTarget,
  phoneDailyTarget,
  setPhoneDailyTarget,
  replizDailyTarget,
  setReplizDailyTarget,
  isSplitValid,
}: Props) {
  const splitSum = phoneDailyTarget + replizDailyTarget;

  const handleSliderChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const repliz = parseInt(e.target.value, 10);
    if (isNaN(repliz)) return;
    const phone = Math.max(0, dailyTarget - repliz);
    setPhoneDailyTarget(phone);
    setReplizDailyTarget(repliz);
  };

  return (
    <section className="bg-surface2 border border-line rounded-2xl p-5 flex flex-col gap-4 shadow-sm">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-bold text-text flex items-center gap-2">
          <Icon icon="solar:shuffle-bold" className="text-warn" width={16} />
          <span>Split of the day</span>
        </h3>
        <p className="text-xs text-text2 leading-relaxed">
          Phone posts + Repliz posts make the channel’s posts per day. The day alternates in this ratio.
        </p>
      </div>

      {dailyTarget > 1 && (
        <div className="flex flex-col gap-1.5">
          <div className="flex justify-between items-baseline text-xs font-semibold">
            <span className="text-text2">Phone ← → Repliz</span>
            <span className="text-sm font-bold text-text tabular-nums">
              {phoneDailyTarget} phone / {replizDailyTarget} Repliz
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={dailyTarget}
            step={1}
            value={replizDailyTarget}
            onChange={handleSliderChange}
            className="w-full h-8 accent-pri cursor-pointer"
          />
          <div className="flex justify-between text-[11px] text-text2">
            <span>More by phone</span>
            <span className="font-semibold text-text">{dailyTarget} posts / day</span>
            <span>More by Repliz</span>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <div className="w-36">
          <label className="text-xs font-semibold text-text block mb-1">Phone / day</label>
          <input
            type="number"
            min={0}
            max={dailyTarget}
            value={phoneDailyTarget}
            onChange={(e) => setPhoneDailyTarget(parseInt(e.target.value, 10) || 0)}
            className="h-10 px-3 rounded-xl border border-line bg-surface text-text text-sm w-full focus:outline-none focus:ring-2 focus:ring-pri"
          />
        </div>

        <span className="text-text2 font-bold text-lg pt-4">+</span>

        <div className="w-36">
          <label className="text-xs font-semibold text-text block mb-1">Repliz / day</label>
          <input
            type="number"
            min={0}
            max={dailyTarget}
            value={replizDailyTarget}
            onChange={(e) => setReplizDailyTarget(parseInt(e.target.value, 10) || 0)}
            className="h-10 px-3 rounded-xl border border-line bg-surface text-text text-sm w-full focus:outline-none focus:ring-2 focus:ring-pri"
          />
        </div>

        <span className="text-text2 font-bold text-lg pt-4">=</span>

        <div className="pt-4">
          <span
            className={`text-xl font-bold tabular-nums ${
              isSplitValid ? "text-pri" : "text-bad"
            }`}
          >
            {splitSum}
          </span>
          <span className="text-xs text-text2 ml-1">/ {dailyTarget} daily target</span>
        </div>
      </div>

      {!isSplitValid && (
        <p className="text-xs font-semibold text-bad m-0">
          Split sum ({splitSum}) does not equal Posts / day ({dailyTarget}).
        </p>
      )}
    </section>
  );
}
