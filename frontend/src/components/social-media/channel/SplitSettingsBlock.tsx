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

  return (
    <section className="card p-5 space-y-4">
      <div>
        <h3 className="text-sm font-bold text-text-primary flex items-center gap-2">
          <Icon icon="solar:shuffle-bold" className="text-amber-400" width={16} />
          Split of the day
        </h3>
        <p className="text-xs text-text-secondary mt-0.5">
          Phone posts + Repliz posts must equal the channel’s Posts / day ({dailyTarget}).
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="w-36">
          <label className="label">Phone posts / day</label>
          <input
            type="number"
            min={0}
            max={dailyTarget}
            value={phoneDailyTarget}
            onChange={(e) => setPhoneDailyTarget(parseInt(e.target.value, 10) || 0)}
            className="input-rect"
          />
        </div>

        <span className="text-text-secondary font-bold text-lg pt-4">+</span>

        <div className="w-36">
          <label className="label">Repliz posts / day</label>
          <input
            type="number"
            min={0}
            max={dailyTarget}
            value={replizDailyTarget}
            onChange={(e) => setReplizDailyTarget(parseInt(e.target.value, 10) || 0)}
            className="input-rect"
          />
        </div>

        <span className="text-text-secondary font-bold text-lg pt-4">=</span>

        <div className="pt-4">
          <span
            className={`text-xl font-bold tabular-nums ${
              isSplitValid ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {splitSum}
          </span>
          <span className="text-xs text-text-secondary ml-1">/ {dailyTarget} daily target</span>
        </div>
      </div>

      {!isSplitValid && (
        <p className="text-xs font-semibold text-rose-400">
          Split sum ({splitSum}) does not equal Posts / day ({dailyTarget}).
        </p>
      )}
    </section>
  );
}
