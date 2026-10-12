"use client";

import type { FanpageDetail, AiVideoTemplate } from "@/lib/types";
import { previewAiVideoOverlay } from "@/lib/api";
import { useState } from "react";

type Props = {
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  templates?: AiVideoTemplate[];
};

export function AiVideoSection({ form, set, templates = [] }: Props) {
  const enabled = !!form.ai_video_enabled;
  const template = templates.find((item) => item.key === form.ai_video_template);
  const clips = form.ai_video_clips ?? template?.default_clips ?? 2;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);

  const preview = async () => {
    if (!form.id) return;
    setPreviewing(true);
    try {
      const response = await previewAiVideoOverlay(form.id, {
        ai_video_logo_enabled: form.ai_video_logo_enabled ?? true,
        ai_video_logo_size_pct: form.ai_video_logo_size_pct ?? 14,
        ai_video_logo_position: form.ai_video_logo_position ?? "top_right",
        ai_video_logo_margin_pct: form.ai_video_logo_margin_pct ?? 3,
        ai_video_captions_enabled: form.ai_video_captions_enabled ?? true,
        ai_video_caption_size: form.ai_video_caption_size ?? 76,
        ai_video_caption_bottom_pct: form.ai_video_caption_bottom_pct ?? 29,
        ai_video_caption_highlight: form.ai_video_caption_highlight ?? "#FFE500",
      });
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl(URL.createObjectURL(response.data));
    } finally {
      setPreviewing(false);
    }
  };

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
            <div>
              <label className="label">Narrator voice</label>
              <select className="input w-full" value={form.ai_video_narrator ?? ""} onChange={(e) => set("ai_video_narrator", e.target.value || null)}>
                <option value="">Auto (by timezone)</option>
                <option value="EN-DOC">British documentary (deep, warm)</option>
                <option value="EN-PADDOCK">British paddock commentator</option>
                <option value="EN-AUSSIE-ENG">Australian race engineer</option>
                <option value="EN-SCOT">Scottish old mechanic</option>
                <option value="EN-JOURNO-F">Northern English female journalist</option>
                <option value="EN-US-ENG">American race engineer</option>
              </select>
            </div>
            <div>
              <label className="label">Panjang judul (karakter)</label>
              <input
                type="number"
                min={20}
                max={5000}
                className="input w-full"
                value={form.ai_video_title_max_chars ?? 100}
                onChange={(e) => set("ai_video_title_max_chars", Number(e.target.value))}
              />
            </div>
            <div>
              <label className="label">Panjang deskripsi (karakter)</label>
              <input
                type="number"
                min={20}
                max={5000}
                className="input w-full"
                value={form.ai_video_description_max_chars ?? 600}
                onChange={(e) => set("ai_video_description_max_chars", Number(e.target.value))}
              />
            </div>
          </div>
          <p className="text-xs text-text-secondary italic">
            Caption mengikuti Pengaturan Global
          </p>
          <div className="border-t border-hairline pt-4 space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-text-primary">Logo & captions</h3>
                <p className="text-xs text-text-secondary">Brand every generated video before it enters the publish queue.</p>
              </div>
              <button type="button" className="btn-secondary" onClick={preview} disabled={previewing}>
                {previewing ? "Rendering…" : "Preview"}
              </button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <label className="flex items-center gap-2 text-sm text-text-primary">
                <input type="checkbox" checked={form.ai_video_logo_enabled ?? true} onChange={(e) => set("ai_video_logo_enabled", e.target.checked)} /> Logo
              </label>
              <label className="flex items-center gap-3 text-sm text-text-primary">Size
                <input type="range" min={5} max={30} value={form.ai_video_logo_size_pct ?? 14} onChange={(e) => set("ai_video_logo_size_pct", Number(e.target.value))} />
                <span>{form.ai_video_logo_size_pct ?? 14}%</span>
              </label>
              <label className="text-sm text-text-primary">Position
                <select className="input w-full mt-1" value={form.ai_video_logo_position ?? "top_right"} onChange={(e) => set("ai_video_logo_position", e.target.value)}>
                  <option value="top_right">Top right</option><option value="top_left">Top left</option><option value="bottom_right">Bottom right</option><option value="bottom_left">Bottom left</option>
                </select>
              </label>
              <label className="flex items-center gap-3 text-sm text-text-primary">Margin
                <input type="range" min={0} max={10} value={form.ai_video_logo_margin_pct ?? 3} onChange={(e) => set("ai_video_logo_margin_pct", Number(e.target.value))} />
                <span>{form.ai_video_logo_margin_pct ?? 3}%</span>
              </label>
              <label className="flex items-center gap-2 text-sm text-text-primary">
                <input type="checkbox" checked={form.ai_video_captions_enabled ?? true} onChange={(e) => set("ai_video_captions_enabled", e.target.checked)} /> Captions
              </label>
              <label className="flex items-center gap-3 text-sm text-text-primary">Caption size
                <input type="range" min={40} max={140} value={form.ai_video_caption_size ?? 76} onChange={(e) => set("ai_video_caption_size", Number(e.target.value))} />
                <span>{form.ai_video_caption_size ?? 76}px</span>
              </label>
              <label className="flex items-center gap-3 text-sm text-text-primary">Vertical position
                <input type="range" min={5} max={70} value={form.ai_video_caption_bottom_pct ?? 29} onChange={(e) => set("ai_video_caption_bottom_pct", Number(e.target.value))} />
                <span>{form.ai_video_caption_bottom_pct ?? 29}%</span>
              </label>
              <label className="text-sm text-text-primary">Highlight colour
                <input type="color" className="block mt-1 h-9 w-16" value={form.ai_video_caption_highlight ?? "#FFE500"} onChange={(e) => set("ai_video_caption_highlight", e.target.value)} />
              </label>
            </div>
            {previewUrl && <img src={previewUrl} alt="AI video overlay preview" className="w-[270px] h-[480px] object-cover rounded-lg border border-hairline" />}
          </div>
        </div>
      )}
    </section>
  );
}
