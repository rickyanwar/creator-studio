"use client";

import type { FanpageDetail, AiVideoTemplate } from "@/lib/types";

type Props = {
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  templates?: AiVideoTemplate[];
};

export function AiVideoSection({ form, set, templates = [] }: Props) {
  const enabled = !!form.ai_video_enabled;
  const template = templates.find((item) => item.key === form.ai_video_template);
  const clips = form.ai_video_clips ?? template?.default_clips ?? 2;

  return (
    <section className="card space-y-5">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-text-primary">Mode 8: AI Video (GFlow)</h2>
          <p className="text-xs text-text-secondary mt-0.5">
            Generate original short videos with Google Flow and publish them as Reels.
          </p>
        </div>
        <button
          type="button"
          onClick={() => set("ai_video_enabled", !enabled)}
          className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${enabled ? "bg-primary-main" : "bg-hairline"}`}
          aria-label="Toggle Mode 8"
        >
          <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${enabled ? "translate-x-5" : ""}`} />
        </button>
      </div>
      {enabled && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="label">Publish Mode</label>
              <div className="flex gap-4 pt-2">
                {(["manual_review", "auto"] as const).map((mode) => (
                  <label key={mode} className="flex items-center gap-2 text-sm text-text-primary cursor-pointer">
                    <input type="radio" name="ai-video-publish-mode" checked={form.ai_video_publish_mode === mode} onChange={() => set("ai_video_publish_mode", mode)} className="accent-primary-main" />
                    {mode === "auto" ? "Auto-publish" : "Manual Review"}
                  </label>
                ))}
              </div>
            </div>
            <div>
              <label className="label">Videos per day</label>
              <input type="number" min={0} max={10} className="input w-full" value={form.ai_video_daily_count ?? 1} onChange={(e) => set("ai_video_daily_count", Number(e.target.value))} />
            </div>
            <div>
              <label className="label">Minimum idea score</label>
              <input type="number" min={0} max={100} className="input w-full" placeholder="Empty = never auto-approve" value={form.ai_video_min_score ?? ""} onChange={(e) => set("ai_video_min_score", e.target.value === "" ? null : Number(e.target.value))} />
              <p className="text-[11px] text-text-secondary mt-1">Used only for automatic approval of Hermes ideas.</p>
            </div>
            <div>
              <label className="label">Video template</label>
              <select className="input w-full" value={form.ai_video_template ?? ""} onChange={(e) => set("ai_video_template", e.target.value || null)}>
                <option value="">Use idea template</option>
                {templates.map((item) => <option key={item.key} value={item.key}>{item.name}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Default clips</label>
              <select className="input w-full" value={clips} onChange={(e) => set("ai_video_clips", Number(e.target.value))}>
                {(template?.clip_options ?? [2, 3]).map((count) => <option key={count} value={count}>{count}</option>)}
              </select>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
