"use client";

import { Icon } from "@iconify/react";
import type { FanpageDetail } from "@/lib/types";

export interface RadarTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  radarNichesData?: string[];
}

export function RadarTab({ form, set, radarNichesData }: RadarTabProps) {
  return (
    <div role="tabpanel" id="tab-panel-radar" aria-labelledby="tab-radar" className="space-y-8">
      {/* ── Section: Viral Radar (Feature 3) ───────── */}
      <section className="card space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-text">Viral Radar</h2>
            <p className="text-xs text-text2 mt-0.5">
              Automatically scrape niche leaders and jump on viral posts early. Recreate/repost them before they peak.
            </p>
          </div>
          <button
            role="switch"
            aria-checked={!!form.radar_enabled}
            aria-label="Enable Viral Radar"
            onClick={() => set("radar_enabled", !form.radar_enabled)}
            className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
              form.radar_enabled ? "bg-pri" : "bg-line"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                form.radar_enabled ? "translate-x-5" : ""
              }`}
            />
          </button>
        </div>

        {form.radar_enabled && (
          <div className="space-y-6 pt-2 border-t border-line mt-4">
            {/* Shadow Mode */}
            <div className="flex items-center justify-between bg-surface p-3 rounded-md border border-line">
              <div>
                <p className="text-sm font-medium text-text flex items-center gap-2">
                  <Icon icon="solar:ghost-bold-duotone" width={16} className={form.radar_shadow ? "text-pri" : "text-text2"} />
                  Shadow Mode
                </p>
                <p className="text-[11px] text-text2 mt-0.5">
                  Only records what would be posted in the Shadow Report, without actually creating jobs. 
                  Recommended for 3-5 days when tuning rules.
                </p>
              </div>
              <button
                role="switch"
                aria-checked={!!form.radar_shadow}
                aria-label="Enable Shadow Mode"
                onClick={() => set("radar_shadow", !form.radar_shadow)}
                className={`relative w-9 h-5 rounded-full transition-colors shrink-0 ${
                  form.radar_shadow ? "bg-pri" : "bg-line"
                }`}
              >
                <span
                  className={`absolute top-[2px] left-[2px] w-4 h-4 rounded-full bg-white transition-transform ${
                    form.radar_shadow ? "translate-x-4" : ""
                  }`}
                />
              </button>
            </div>

            {/* Radar Niches */}
            <div className="space-y-2">
              <label className="label">Radar Niches</label>
              <p className="text-[11px] text-text2 mb-2">
                This fanpage will receive stories from these niches.
              </p>
              {!(radarNichesData && radarNichesData.length > 0) ? (
                <p className="text-[11px] text-text2 italic">No radar niches found yet. Add accounts to Radar first.</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {radarNichesData.map((n) => {
                    const active = ((form.radar_niches as string[]) ?? []).includes(n);
                    return (
                      <button
                        key={n}
                        onClick={() => {
                          const cur = (form.radar_niches as string[]) ?? [];
                          set("radar_niches", active ? cur.filter((v) => v !== n) : [...cur, n]);
                        }}
                        className={`text-xs px-3 py-1.5 rounded-full border font-medium transition-colors ${
                          active
                            ? "bg-pri text-pri-fg border-pri"
                            : "border-line text-text2 hover:border-pri hover:text-pri"
                        }`}
                      >
                        {n}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Rule Thresholds */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="label">Min Likes (Abs)</label>
                <input
                  type="number"
                  className="input-rect"
                  value={form.radar_min_likes ?? 1000}
                  onChange={(e) => set("radar_min_likes", Math.max(1, parseInt(e.target.value) || 1000))}
                />
              </div>
              <div>
                <label className="label">Confirmed Ratio</label>
                <input
                  type="number"
                  step="0.1"
                  className="input-rect"
                  value={form.radar_confirm_ratio ?? 1.5}
                  onChange={(e) => set("radar_confirm_ratio", Math.max(0.1, parseFloat(e.target.value) || 1.5))}
                />
                <p className="text-[10px] text-text2 mt-1">x median likes at this age</p>
              </div>
              
              <div>
                <label className="label">Fast Ratio</label>
                <input
                  type="number"
                  step="0.1"
                  className="input-rect"
                  value={form.radar_fast_ratio ?? 2.0}
                  onChange={(e) => set("radar_fast_ratio", Math.max(0.1, parseFloat(e.target.value) || 2.0))}
                />
              </div>
              <div>
                <label className="label">Fast Window (min)</label>
                <input
                  type="number"
                  className="input-rect"
                  value={form.radar_fast_window_min ?? 60}
                  onChange={(e) => set("radar_fast_window_min", Math.max(1, parseInt(e.target.value) || 60))}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="label">Shelf Life: News (h)</label>
                <input
                  type="number"
                  className="input-rect"
                  value={form.radar_shelf_news_h ?? 72}
                  onChange={(e) => set("radar_shelf_news_h", Math.max(1, parseInt(e.target.value) || 72))}
                />
              </div>
              <div>
                <label className="label">Shelf Life: Evergreen (h)</label>
                <input
                  type="number"
                  className="input-rect"
                  value={form.radar_shelf_evergreen_h ?? 168}
                  onChange={(e) => set("radar_shelf_evergreen_h", Math.max(1, parseInt(e.target.value) || 168))}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="flex items-center gap-3 bg-surface p-3 rounded-md border border-line">
                <input
                  type="checkbox"
                  id="radar_burst_enabled"
                  checked={form.radar_burst_enabled ?? true}
                  onChange={(e) => set("radar_burst_enabled", e.target.checked)}
                  className="rounded border-line text-pri focus:ring-pri/20 accent-pri"
                />
                <label htmlFor="radar_burst_enabled" className="text-sm font-medium text-text cursor-pointer select-none">
                  Enable Topic Burst
                  <p className="text-[10px] text-text2 mt-0.5">Allow early triggers if many accounts post simultaneously.</p>
                </label>
              </div>

              <div>
                <label className="label">Posts per day limit</label>
                <input
                  type="number"
                  className="input-rect"
                  value={form.radar_daily_max ?? 3}
                  onChange={(e) => set("radar_daily_max", Math.max(0, parseInt(e.target.value) || 3))}
                />
                <p className="text-[10px] text-text2 mt-1">Reactive max, not a target. Shared within publish_daily_limit.</p>
              </div>
            </div>

            <div>
              <label className="label">Visual Engine (Run 2 image edit)</label>
              <select
                className="input w-full mt-1"
                value={(form.visual_engine as string) ?? "off"}
                onChange={(e) => set("visual_engine", e.target.value)}
              >
                <option value="off">Off (don&apos;t edit other images)</option>
                <option value="gflow">Google Flow (gflow)</option>
                <option value="chatgpt">ChatGPT (cx/gpt-image)</option>
                <option value="auto">Auto (let AI choose)</option>
              </select>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
