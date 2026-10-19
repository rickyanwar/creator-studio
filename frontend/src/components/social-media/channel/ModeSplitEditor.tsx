"use client";

import React, { useMemo } from "react";
import {
  GROUP_ORDER,
  GROUP_LABELS,
  evenShares,
  groupQuotas,
  quotaSummary,
} from "@/lib/modeSplit";

interface Props {
  dailyTarget: number;
  enabledGroups: string[];
  shares: Record<string, number> | null | undefined;
  onChange: (shares: Record<string, number> | null) => void;
}

function rebalanceShares(
  changedGroup: string,
  newVal: number,
  currentShares: Record<string, number>,
  enabledGroups: string[]
): Record<string, number> {
  const targetVal = Math.max(0, Math.min(100, Math.round(newVal)));
  const otherGroups = enabledGroups.filter((g) => g !== changedGroup);

  if (otherGroups.length === 0) {
    return { [changedGroup]: 100 };
  }

  const remaining = 100 - targetVal;
  const currentOtherSum = otherGroups.reduce(
    (sum, g) => sum + (currentShares[g] ?? 0),
    0
  );

  const newShares: Record<string, number> = {
    [changedGroup]: targetVal,
  };

  if (currentOtherSum <= 0) {
    const evenOther = evenShares(otherGroups);
    let allocated = 0;
    const fractions: { group: string; fraction: number; orderIndex: number }[] = [];
    otherGroups.forEach((g) => {
      const raw = (remaining * (evenOther[g] ?? 0)) / 100;
      const base = Math.floor(raw);
      newShares[g] = base;
      allocated += base;
      const orderIndex = GROUP_ORDER.indexOf(g as any);
      fractions.push({
        group: g,
        fraction: raw - base,
        orderIndex: orderIndex === -1 ? 999 : orderIndex,
      });
    });
    const rem = remaining - allocated;
    fractions.sort((a, b) => {
      if (Math.abs(b.fraction - a.fraction) > 1e-9) return b.fraction - a.fraction;
      return a.orderIndex - b.orderIndex;
    });
    for (let i = 0; i < rem && i < fractions.length; i++) {
      newShares[fractions[i].group] += 1;
    }
  } else {
    let allocated = 0;
    const fractions: { group: string; fraction: number; orderIndex: number }[] = [];
    otherGroups.forEach((g) => {
      const cur = currentShares[g] ?? 0;
      const raw = (remaining * cur) / currentOtherSum;
      const base = Math.floor(raw);
      newShares[g] = base;
      allocated += base;
      const orderIndex = GROUP_ORDER.indexOf(g as any);
      fractions.push({
        group: g,
        fraction: raw - base,
        orderIndex: orderIndex === -1 ? 999 : orderIndex,
      });
    });
    const rem = remaining - allocated;
    fractions.sort((a, b) => {
      if (Math.abs(b.fraction - a.fraction) > 1e-9) return b.fraction - a.fraction;
      return a.orderIndex - b.orderIndex;
    });
    for (let i = 0; i < rem && i < fractions.length; i++) {
      newShares[fractions[i].group] += 1;
    }
  }

  return newShares;
}

export function ModeSplitEditor({
  dailyTarget,
  enabledGroups,
  shares,
  onChange,
}: Props) {
  if (!enabledGroups || enabledGroups.length === 0) {
    return (
      <p className="text-xs text-text2">
        Turn on content modes for this brand to split posts.
      </p>
    );
  }

  const sortedGroups = useMemo(() => {
    return [...enabledGroups].sort((a, b) => {
      const ia = GROUP_ORDER.indexOf(a as any);
      const ib = GROUP_ORDER.indexOf(b as any);
      return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });
  }, [enabledGroups]);

  const isSplitOn = Boolean(shares && Object.keys(shares).length > 0);

  const effectiveShares = useMemo(() => {
    if (!shares || Object.keys(shares).length === 0) {
      return evenShares(sortedGroups);
    }
    const result: Record<string, number> = {};
    for (const g of sortedGroups) {
      result[g] = shares[g] ?? 0;
    }
    return result;
  }, [shares, sortedGroups]);

  const quotas = useMemo(() => {
    return isSplitOn ? groupQuotas(dailyTarget, effectiveShares, sortedGroups) : {};
  }, [dailyTarget, effectiveShares, sortedGroups, isSplitOn]);

  const handleSliderChange = (group: string, value: number) => {
    const nextShares = rebalanceShares(group, value, effectiveShares, sortedGroups);
    onChange(nextShares);
  };

  const handleSplitEvenly = () => {
    onChange(evenShares(sortedGroups));
  };

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-2 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={isSplitOn}
          onChange={(e) => {
            if (e.target.checked) {
              onChange(evenShares(sortedGroups));
            } else {
              onChange(null);
            }
          }}
          className="rounded border-line text-pri focus:ring-pri"
        />
        <span className="text-xs font-semibold text-text">
          Split posts by mode
        </span>
      </label>

      {!isSplitOn && (
        <p className="text-xs text-text2">
          Off: posts take free slots in arrival order.
        </p>
      )}

      {isSplitOn && (
        <>
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-text2 uppercase tracking-wider">
              Split by percentage
            </span>
            <button
              type="button"
              onClick={handleSplitEvenly}
              className="text-xs font-medium text-pri hover:underline"
            >
              Split evenly
            </button>
          </div>

          <div className="space-y-3">
            {sortedGroups.map((group) => {
              const val = effectiveShares[group] ?? 0;
              const label = GROUP_LABELS[group] ?? group;

              return (
                <div
                  key={group}
                  className="flex items-center gap-4 bg-surface p-3 rounded-lg border border-line"
                >
                  <div className="w-28 shrink-0">
                    <span className="text-xs font-medium text-text">{label}</span>
                  </div>

                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={val}
                    onChange={(e) =>
                      handleSliderChange(group, parseInt(e.target.value, 10) || 0)
                    }
                    className="flex-1 accent-pri h-2 cursor-pointer"
                  />

                  <div className="flex items-center gap-1 shrink-0">
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={val}
                      onChange={(e) => {
                        const parsed = parseInt(e.target.value, 10);
                        handleSliderChange(
                          group,
                          isNaN(parsed) ? 0 : Math.max(0, Math.min(100, parsed))
                        );
                      }}
                      className="w-16 h-8 text-center text-xs tabular-nums rounded-md bg-surface2 border border-line text-text focus:outline-none focus:ring-1 focus:ring-pri"
                    />
                    <span className="text-xs text-text2">%</span>
                  </div>
                </div>
              );
            })}
          </div>

          <p className="text-xs font-medium text-text2">
            {quotaSummary(quotas)}
          </p>
        </>
      )}
    </div>
  );
}
