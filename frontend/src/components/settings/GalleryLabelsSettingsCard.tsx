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
    <section className="card space-y-4">
      <div>
        <h2 className="text-base font-semibold text-text-primary">
          Gallery labels (third-party API)
        </h2>
        <p className="text-xs text-text-secondary mt-1">
          Rich labeling for new gallery downloads.
        </p>
      </div>

      {error && (
        <p className="text-xs text-error-main bg-red-50 rounded p-2">
          {error}
        </p>
      )}

      <label className="flex items-center gap-3 text-sm text-text-primary cursor-pointer">
        <input
          type="checkbox"
          className="w-4 h-4 accent-primary-main"
          checked={settings?.gallery_labeling_enabled ?? false}
          onChange={(e) => update("gallery_labeling_enabled", e.target.checked)}
        />
        Enable rich labeling
      </label>

      <div>
        <label className="label">Daily cap (0–5000)</label>
        <input
          type="number"
          min={0}
          max={5000}
          className="input-rect"
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
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-2 border-t border-border-default text-xs">
        <div className="bg-bg-subtle p-2 rounded">
          <span className="text-text-secondary block">Labeled today</span>
          <span className="font-semibold text-text-primary">{settings?.gallery_labels_today ?? 0}</span>
        </div>
        <div className="bg-bg-subtle p-2 rounded">
          <span className="text-text-secondary block">Labeled total</span>
          <span className="font-semibold text-text-primary">{settings?.gallery_labels_total ?? 0}</span>
        </div>
        <div className="bg-bg-subtle p-2 rounded">
          <span className="text-text-secondary block">Pending</span>
          <span className="font-semibold text-text-primary">{settings?.gallery_labels_pending ?? 0}</span>
        </div>
        <div className="bg-bg-subtle p-2 rounded">
          <span className="text-text-secondary block">Failed (&ge;3 attempts)</span>
          <span className="font-semibold text-text-primary">{settings?.gallery_labels_failed ?? 0}</span>
        </div>
      </div>

      <p className="text-[11px] text-text-secondary italic">
        New downloads are labeled with 9Router; older photos will be labeled later
      </p>
    </section>
  );
}
