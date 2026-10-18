"use client";

import { useState } from "react";
import type { FanpageDetail } from "@/lib/types";
import { GlobalPublishMode } from "@/components/fanpages/GlobalPublishMode";
import { TARGET_COUNTRIES, IANA_ZONES, isValidTz, TzClock } from "@/components/fanpages/tabs/GlobalTab";
import { clearPublishHourWeights, type PublishHourWeights } from "@/lib/hourWeightsApi";

export interface PacingTabProps {
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  handleCountryChange: (newCode: string) => void;
  tzInput: string;
  setTzInput: (v: string) => void;
  handleTimezoneChange: (newTz: string) => void;
  tzMsg: string;
}

export function PacingTab({
  form,
  set,
  handleCountryChange,
  tzInput,
  setTzInput,
  handleTimezoneChange,
  tzMsg,
}: PacingTabProps) {
  const [clearing, setClearing] = useState(false);

  const hourWeights = (form as Record<string, unknown>).publish_hour_weights as
    | PublishHourWeights
    | null
    | undefined;
  const hasWeights = Boolean(
    hourWeights && typeof hourWeights === "object" && Object.keys(hourWeights).length > 0
  );

  const handleClearWeights = async () => {
    setClearing(true);
    try {
      if (form.id) {
        await clearPublishHourWeights(form.id);
      }
      set("publish_hour_weights", null);
    } catch (err) {
      console.error("Failed to clear hour weights", err);
    } finally {
      setClearing(false);
    }
  };

  return (
    <div role="tabpanel" id="tab-panel-pacing" aria-labelledby="tab-pacing" className="space-y-6">
      {/* ── Section: Review before posting ──────────────── */}
      <GlobalPublishMode form={form as Record<string, unknown>} set={set} />

      {/* ── Section: Publish Pacing & Timezone ────────────── */}
      <section aria-labelledby="h-aud" className="bg-surface border border-line rounded-2xl p-6 space-y-5">
        <div>
          <h2 id="h-aud" className="text-lg font-semibold text-text">
            Publish Pacing & Timezone
          </h2>
          <p className="text-xs text-text2 mt-0.5">
            Target country and quiet hours keep auto-publish paced like a human admin.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">Target Country</label>
            <select
              className="input-rect w-full"
              value={form.target_country || ""}
              onChange={(e) => handleCountryChange(e.target.value)}
            >
              <option value="">— None (Global) —</option>
              {TARGET_COUNTRIES.map((c) => (
                <option key={c.code} value={c.code}>
                  {c.name}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-text2 mt-1">Country for scheduling & target audience.</p>
          </div>

          <div>
            <label className="label">Timezone</label>
            <input
              list="iana-zones"
              className="input-rect w-full"
              value={tzInput}
              onChange={(e) => {
                const val = e.target.value;
                setTzInput(val);
                if (isValidTz(val) && val !== form.timezone) {
                  handleTimezoneChange(val);
                }
              }}
              onBlur={() => {
                if (!isValidTz(tzInput)) {
                  setTzInput(form.timezone || "Europe/London");
                }
              }}
              placeholder="e.g. Europe/London"
            />
            <datalist id="iana-zones">
              {IANA_ZONES.map((z) => (
                <option key={z} value={z} />
              ))}
            </datalist>
            {!isValidTz(tzInput) && (
              <p className="text-[11px] text-bad mt-1">Unknown timezone</p>
            )}
            <p className="text-[11px] text-text2 mt-1">
              Local time: <TzClock tz={form.timezone || "Europe/London"} />
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-6 pt-2">
          <div>
            <label className="label">Sleep window (local time)</label>
            <div className="flex items-center gap-2">
              <select
                className="input-rect py-1.5 text-sm w-24"
                value={form.publish_sleep_start_hour ?? ""}
                onChange={(e) =>
                  set(
                    "publish_sleep_start_hour",
                    e.target.value === "" ? null : Number(e.target.value)
                  )
                }
              >
                <option value="">Off</option>
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {String(h).padStart(2, "0")}:00
                  </option>
                ))}
              </select>
              <span className="text-xs text-text2">to</span>
              <select
                className="input-rect py-1.5 text-sm w-24"
                value={form.publish_sleep_end_hour ?? ""}
                onChange={(e) =>
                  set(
                    "publish_sleep_end_hour",
                    e.target.value === "" ? null : Number(e.target.value)
                  )
                }
                disabled={
                  form.publish_sleep_start_hour === null ||
                  form.publish_sleep_start_hour === undefined
                }
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {String(h).padStart(2, "0")}:00
                  </option>
                ))}
              </select>
            </div>
            {tzMsg && <p className="text-[11px] text-warn mt-1 font-semibold">{tzMsg}</p>}
          </div>

          <div>
            <label className="label">Max posts / day</label>
            <input
              type="number"
              min={1}
              className="input-rect py-1.5 text-sm w-28"
              value={form.publish_daily_limit ?? 45}
              onChange={(e) =>
                set("publish_daily_limit", Math.max(1, Number(e.target.value) || 1))
              }
            />
            <p className="text-[11px] text-text2 mt-1">Fallback limit when no channel active.</p>
          </div>
        </div>
      </section>

      {/* ── Section: Best hours (from Strategy) ──────────── */}
      <section aria-labelledby="h-best-hours" className="bg-surface border border-line rounded-2xl p-6 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="h-best-hours" className="text-lg font-semibold text-text">
              Best hours (from Strategy)
            </h2>
            <p className="text-xs text-text2 mt-0.5">
              Posting hour weights applied from Strategy. Heavier hours are preferred during slot planning.
            </p>
          </div>
          {hasWeights && (
            <button
              type="button"
              onClick={handleClearWeights}
              disabled={clearing}
              className="h-8 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-xs font-semibold text-text transition-colors cursor-pointer"
            >
              {clearing ? "Clearing..." : "Clear"}
            </button>
          )}
        </div>

        {hasWeights ? (
          <div className="pt-2">
            <div className="flex items-end justify-between gap-1 overflow-x-auto min-w-0 h-28 p-3 bg-surface2 rounded-xl">
              {Array.from({ length: 24 }, (_, h) => {
                const w = Number(hourWeights![String(h)] ?? hourWeights![h] ?? 1.0);
                const heightPct = Math.round(Math.min(100, Math.max(15, (w / 3.0) * 100)));
                const isHeavy = w > 1.0;
                return (
                  <div
                    key={h}
                    className="flex-1 min-w-[12px] max-w-[28px] flex flex-col items-center gap-1 h-full justify-end"
                    title={`${String(h).padStart(2, "0")}:00 - ${w.toFixed(1)}x`}
                  >
                    <span className="text-[9px] font-mono text-text2 leading-none">{w.toFixed(1)}</span>
                    <div
                      className={`w-full rounded-t transition-all ${
                        isHeavy ? "bg-pri" : "bg-line"
                      }`}
                      style={{ height: `${heightPct}%` }}
                    />
                    <span className="text-[10px] text-text2 font-mono leading-none mt-1">
                      {h}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="flex items-center justify-between text-[11px] text-text2 mt-2 px-1">
              <span>00:00 (Midnight)</span>
              <span>12:00 (Noon)</span>
              <span>23:00</span>
            </div>
          </div>
        ) : (
          <div className="p-4 rounded-xl bg-surface2 text-xs text-text2">
            No hour weights applied. Posting uses default pacing spread. Hermes strategy proposals can set best hours.
          </div>
        )}
      </section>
    </div>
  );
}
