"use client";

import { useEffect, useState } from "react";
import type { AppSettings } from "@/lib/types";
import { updateSettings } from "@/lib/api";
import { ResearchDeskSettings } from "@/components/settings/ResearchDeskSettings";

export function AiVideoSettingsCard({
  settings,
  onChanged,
}: {
  settings: AppSettings | undefined;
  onChanged: () => void;
}) {
  const [reserve, setReserve] = useState<number | string>(
    settings?.ai_video_credit_reserve ?? 0
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReserve(settings?.ai_video_credit_reserve ?? 0);
  }, [settings?.ai_video_credit_reserve]);

  async function update(key: string, value: boolean | number) {
    setError(null);
    try {
      await updateSettings({ [key]: value });
      onChanged();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Failed to update AI video settings");
    }
  }

  function commitReserve() {
    const parsed = typeof reserve === "string" ? parseInt(reserve, 10) : reserve;
    const clamped = Math.max(0, Math.min(10000, Number.isNaN(parsed) ? 0 : parsed));
    setReserve(clamped);
    if (clamped !== (settings?.ai_video_credit_reserve ?? 0)) {
      update("ai_video_credit_reserve", clamped);
    }
  }

  return (
    <section className="card space-y-4">
      <div>
        <h2 className="text-base font-semibold text-text-primary">AI video (GFlow)</h2>
        <p className="text-xs text-text-secondary mt-1">
          Control the worker and global video quality checks.
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
          checked={settings?.ai_video_worker_enabled ?? true}
          onChange={(e) => update("ai_video_worker_enabled", e.target.checked)}
        />
        Worker enabled
      </label>

      <label className="flex items-start gap-3 text-sm text-text-primary cursor-pointer">
        <input
          type="checkbox"
          className="w-4 h-4 mt-0.5 accent-primary-main"
          checked={settings?.ai_video_vision_qc_enabled ?? false}
          onChange={(e) => update("ai_video_vision_qc_enabled", e.target.checked)}
        />
        <span>
          Vision QC toggle
          <span className="block text-[11px] text-text-secondary">
            1 vision call per video; off = only free technical checks
          </span>
        </span>
      </label>

      <div>
        <label className="label">Credit reserve</label>
        <input
          type="number"
          min={0}
          max={10000}
          className="input-rect"
          value={reserve}
          onChange={(e) => setReserve(e.target.value)}
          onBlur={commitReserve}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              commitReserve();
            }
          }}
        />
        <p className="text-[11px] text-text-secondary mt-1">
          Credits held back globally from job dispatch.
        </p>
      </div>

      <ResearchDeskSettings settings={settings} onChanged={onChanged} />
    </section>
  );
}
