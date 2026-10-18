"use client";

import { useEffect, useState } from "react";
import type { AppSettings } from "@/lib/types";
import { updateSettings } from "@/lib/api";

export function GalleryLabelsSettingsCard({
  settings,
  onChanged,
}: {
  settings: AppSettings | undefined;
  onChanged: () => void;
}) {
  const [dailyCap, setDailyCap] = useState<number | string>(
    settings?.gallery_labeling_daily_cap ?? 1000
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDailyCap(settings?.gallery_labeling_daily_cap ?? 1000);
  }, [settings?.gallery_labeling_daily_cap]);

  async function update(key: string, value: boolean | number) {
    setError(null);
    try {
      await updateSettings({ [key]: value });
      onChanged();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Failed to update gallery labeling settings");
    }
  }

  function commitCap() {
    const parsed = typeof dailyCap === "string" ? parseInt(dailyCap, 10) : dailyCap;
    const clamped = Math.max(0, Math.min(5000, Number.isNaN(parsed) ? 1000 : parsed));
    setDailyCap(clamped);
    if (clamped !== (settings?.gallery_labeling_daily_cap ?? 1000)) {
      update("gallery_labeling_daily_cap", clamped);
    }
  }

  return (
    <section className="bg-surface border border-line rounded-[14px] p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-text">
          Gallery labels (third-party API)
        </h2>
        <p className="text-xs text-text2 mt-1">
          Rich labeling for new gallery downloads.
        </p>
      </div>

      {error && (
        <p className="text-xs text-bad bg-bad-soft rounded-lg p-2.5">
          {error}
        </p>
      )}

      <label className="flex items-center gap-3 text-sm text-text cursor-pointer">
        <input
          type="checkbox"
          className="w-4 h-4 accent-pri rounded"
          checked={settings?.gallery_labeling_enabled ?? false}
          onChange={(e) => update("gallery_labeling_enabled", e.target.checked)}
        />
        <span>Enable rich labeling</span>
      </label>

      <div>
        <label className="block text-xs font-semibold text-text mb-1">Daily cap (0–5000)</label>
        <input
          type="number"
          min={0}
          max={5000}
          className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-44"
          value={dailyCap}
          onChange={(e) => setDailyCap(e.target.value)}
          onBlur={commitCap}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              commitCap();
            }
          }}
        />
      </div>

      {/* Stats display */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 pt-2 border-t border-line text-xs">
        <div className="bg-surface2 p-2.5 rounded-lg">
          <span className="text-text2 block text-[11px]">Labeled today</span>
          <span className="font-semibold text-text text-base">{settings?.gallery_labels_today ?? 0}</span>
        </div>
        <div className="bg-surface2 p-2.5 rounded-lg">
          <span className="text-text2 block text-[11px]">Labeled total</span>
          <span className="font-semibold text-text text-base">{settings?.gallery_labels_total ?? 0}</span>
        </div>
        <div className="bg-surface2 p-2.5 rounded-lg">
          <span className="text-text2 block text-[11px]">Pending</span>
          <span className="font-semibold text-text text-base">{settings?.gallery_labels_pending ?? 0}</span>
        </div>
        <div className="bg-surface2 p-2.5 rounded-lg">
          <span className="text-text2 block text-[11px]">Failed (&ge;3 attempts)</span>
          <span className="font-semibold text-text text-base">{settings?.gallery_labels_failed ?? 0}</span>
        </div>
      </div>

      <p className="text-[11px] text-text2 italic">
        New downloads are labeled with 9Router; older photos will be labeled later
      </p>
    </section>
  );
}
