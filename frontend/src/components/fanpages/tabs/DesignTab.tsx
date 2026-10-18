"use client";

import { useState } from "react";
import { Icon } from "@iconify/react";
import type { FanpageDetail } from "@/lib/types";
import type { BrandDetailOut } from "@/lib/socialBrandsApi";
import { previewAiVideoOverlay } from "@/lib/api";
import DesignTemplatesSection from "@/components/brand/DesignTemplatesSection";
import { BrandAiImageCard } from "@/components/social-media/brand/BrandAiImageCard";

export interface DesignTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  fp: FanpageDetail;
  mutate: () => Promise<any>;
  wmUploading: boolean;
  handleWatermarkUpload: (file: File) => Promise<void>;
  handleWatermarkDelete: () => Promise<void>;
  brand?: BrandDetailOut;
  aiEngineStatus?: { codex_usage?: { window_pct?: number } };
  onToggleGptcli: (enabled: boolean) => Promise<void>;
}

export function DesignTab({
  fanpageId,
  form,
  set,
  fp,
  mutate,
  wmUploading,
  handleWatermarkUpload,
  handleWatermarkDelete,
  brand,
  aiEngineStatus,
  onToggleGptcli,
}: DesignTabProps) {
  const [aiPreviewUrl, setAiPreviewUrl] = useState<string | null>(null);
  const [aiPreviewing, setAiPreviewing] = useState(false);

  return (
    <div role="tabpanel" id="tab-panel-design" aria-labelledby="tab-design" className="space-y-6">
      {/* ── Section: Brand Logo & Watermark ──────────────── */}
      <section className="bg-surface border border-line rounded-2xl p-6 space-y-5">
        <div>
          <h2 className="text-lg font-semibold text-text">Brand Logo</h2>
          <p className="text-xs text-text2 mt-0.5">
            Brand logo and watermark configuration, and where they are placed.
          </p>
        </div>

        {/* Logo file (image) */}
        <div>
          <label className="label">Logo file (image)</label>
          <div className="flex items-center gap-4">
            {fp.watermark_image_url ? (
              <div className="flex items-center gap-3">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={fp.watermark_image_url}
                  alt="watermark"
                  className="h-12 max-w-[160px] object-contain rounded-md border border-line bg-surface2 p-1.5"
                />
                <button
                  type="button"
                  onClick={handleWatermarkDelete}
                  disabled={wmUploading}
                  className="btn-ghost text-bad flex items-center gap-1 text-xs"
                >
                  <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} /> Remove
                </button>
              </div>
            ) : (
              <label
                className={`btn-secondary inline-flex items-center gap-2 cursor-pointer text-xs ${
                  wmUploading ? "opacity-50" : ""
                }`}
              >
                {wmUploading ? (
                  <Icon icon="svg-spinners:ring-resize" width={14} />
                ) : (
                  <Icon icon="solar:upload-bold-duotone" width={14} />
                )}
                Upload logo (PNG or JPG)
                <input
                  type="file"
                  accept="image/*"
                  className="hidden"
                  disabled={wmUploading}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleWatermarkUpload(f);
                    e.currentTarget.value = "";
                  }}
                />
              </label>
            )}
          </div>
          <p className="text-[11px] text-text2 mt-1">
            PNG with transparency recommended. Stamped onto post cards and video overlays. Overrides watermark text.
          </p>
        </div>

        {/* Watermark text fallback */}
        <div>
          <label className="label">Watermark Text (fallback)</label>
          <input
            className="input-rect w-full max-w-md"
            value={(form.watermark_text as string) ?? ""}
            onChange={(e) => set("watermark_text", e.target.value)}
            placeholder="e.g. @yourbrand — used when no logo is uploaded"
          />
          <p className="text-[11px] text-text2 mt-1">
            Used only when no logo image is uploaded.
          </p>
        </div>

        {/* Where it is used */}
        <div className="border-t border-line pt-4 space-y-4">
          <h3 className="text-sm font-semibold text-text">Where it is used</h3>
          <ul className="space-y-4 divide-y divide-line">
            {/* 1. Image cards */}
            <li className="pt-3 flex flex-wrap items-start justify-between gap-2">
              <div className="space-y-0.5">
                <span className="text-sm font-medium text-text">1. Image cards</span>
                <p className="text-xs text-text2">
                  {fp.watermark_image_url
                    ? "Every card template places the logo. It replaces the watermark text."
                    : "No logo uploaded: every card template shows the watermark text instead."}
                </p>
              </div>
              <span className="text-xs font-semibold px-2.5 py-1 rounded-full bg-surface2 text-text2 border border-line">
                Always on
              </span>
            </li>

            {/* 2. YouTube clips */}
            <li className="pt-3 flex flex-wrap items-center justify-between gap-4">
              <div className="space-y-0.5">
                <span className="text-sm font-medium text-text">2. YouTube clips</span>
                <p className="text-xs text-text2">
                  Show watermark on clips (uses the logo, or watermark text when there is no logo).
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={form.yt_clip_watermark ?? true}
                onClick={() => set("yt_clip_watermark", !(form.yt_clip_watermark ?? true))}
                className="inline-flex items-center gap-2 cursor-pointer"
              >
                <span
                  className={`relative w-10 h-6 rounded-full transition-colors ${
                    (form.yt_clip_watermark ?? true) ? "bg-pri" : "bg-line"
                  }`}
                >
                  <span
                    className={`absolute top-1 left-1 w-4 h-4 rounded-full transition-transform shadow-sm ${
                      (form.yt_clip_watermark ?? true) ? "bg-pri-fg translate-x-4" : "bg-surface"
                    }`}
                  />
                </span>
              </button>
            </li>

            {/* 3. AI video */}
            <li className="pt-3 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div className="space-y-0.5">
                  <span className="text-sm font-medium text-text">3. AI video logo overlay</span>
                  <p className="text-xs text-text2">
                    Overlay brand logo on generated GFlow video clips.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={form.ai_video_logo_enabled ?? true}
                  onClick={() => set("ai_video_logo_enabled", !(form.ai_video_logo_enabled ?? true))}
                  className="inline-flex items-center gap-2 cursor-pointer"
                >
                  <span
                    className={`relative w-10 h-6 rounded-full transition-colors ${
                      (form.ai_video_logo_enabled ?? true) ? "bg-pri" : "bg-line"
                    }`}
                  >
                    <span
                      className={`absolute top-1 left-1 w-4 h-4 rounded-full transition-transform shadow-sm ${
                        (form.ai_video_logo_enabled ?? true) ? "bg-pri-fg translate-x-4" : "bg-surface"
                      }`}
                    />
                  </span>
                </button>
              </div>

              {(form.ai_video_logo_enabled ?? true) && (
                <div className="p-4 rounded-xl bg-surface2 border border-line space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <label className="text-xs text-text font-medium flex flex-col gap-1.5">
                      <span>Size: {form.ai_video_logo_size_pct ?? 14}%</span>
                      <input
                        type="range"
                        min={5}
                        max={30}
                        value={form.ai_video_logo_size_pct ?? 14}
                        onChange={(e) => set("ai_video_logo_size_pct", Number(e.target.value))}
                        className="accent-pri"
                      />
                      <span className="text-[10px] text-text2">Logo width as a share of video width (5–30%)</span>
                    </label>

                    <label className="text-xs text-text font-medium flex flex-col gap-1.5">
                      <span>Position</span>
                      <select
                        className="input-rect w-full"
                        value={form.ai_video_logo_position ?? "top_right"}
                        onChange={(e) => set("ai_video_logo_position", e.target.value)}
                      >
                        <option value="top_right">Top right</option>
                        <option value="top_left">Top left</option>
                        <option value="bottom_right">Bottom right</option>
                        <option value="bottom_left">Bottom left</option>
                      </select>
                    </label>

                    <label className="text-xs text-text font-medium flex flex-col gap-1.5">
                      <span>Margin: {form.ai_video_logo_margin_pct ?? 3}%</span>
                      <input
                        type="range"
                        min={0}
                        max={10}
                        value={form.ai_video_logo_margin_pct ?? 3}
                        onChange={(e) => set("ai_video_logo_margin_pct", Number(e.target.value))}
                        className="accent-pri"
                      />
                      <span className="text-[10px] text-text2">Distance from edge (0–10%)</span>
                    </label>
                  </div>

                  <div className="pt-2 flex flex-col gap-2">
                    <button
                      type="button"
                      className="btn-secondary text-xs self-start"
                      onClick={async () => {
                        if (!fanpageId) return;
                        setAiPreviewing(true);
                        try {
                          const res = await previewAiVideoOverlay(fanpageId, {
                            ai_video_logo_enabled: form.ai_video_logo_enabled ?? true,
                            ai_video_logo_size_pct: form.ai_video_logo_size_pct ?? 14,
                            ai_video_logo_position: form.ai_video_logo_position ?? "top_right",
                            ai_video_logo_margin_pct: form.ai_video_logo_margin_pct ?? 3,
                            ai_video_captions_enabled: form.ai_video_captions_enabled ?? true,
                            ai_video_caption_size: form.ai_video_caption_size ?? 76,
                            ai_video_caption_bottom_pct: form.ai_video_caption_bottom_pct ?? 29,
                            ai_video_caption_highlight: form.ai_video_caption_highlight ?? "#FFE500",
                          });
                          if (aiPreviewUrl) URL.revokeObjectURL(aiPreviewUrl);
                          setAiPreviewUrl(URL.createObjectURL(res.data));
                        } catch {
                          alert("Preview overlay failed.");
                        } finally {
                          setAiPreviewing(false);
                        }
                      }}
                      disabled={aiPreviewing}
                    >
                      {aiPreviewing ? "Rendering…" : "Preview Video Overlay"}
                    </button>
                    {aiPreviewUrl && (
                      <img
                        src={aiPreviewUrl}
                        alt="AI video overlay preview"
                        className="w-[240px] h-[426px] object-cover rounded-lg border border-line"
                      />
                    )}
                  </div>
                </div>
              )}
            </li>
          </ul>
        </div>
      </section>

      {/* ── Section: Design Templates ────────────────────── */}
      <DesignTemplatesSection fanpage={fp} onSaved={() => mutate()} />

      {/* ── Section: Image Framing ───────────────────────── */}
      <section className="bg-surface border border-line rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-text">Image Framing</h2>
            <p className="text-xs text-text2 mt-0.5">
              Expand image to fill (auto). When a photo would be heavily cropped, fill the frame instead — mirror-extend action shots, blurred fit for close-up faces.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={form.design_expand ?? false}
            onClick={() => set("design_expand", !form.design_expand)}
            className="inline-flex items-center gap-2 cursor-pointer"
          >
            <span
              className={`relative w-10 h-6 rounded-full transition-colors ${
                form.design_expand ? "bg-pri" : "bg-line"
              }`}
            >
                <span
                  className={`absolute top-1 left-1 w-4 h-4 rounded-full transition-transform shadow-sm ${
                    form.design_expand ? "bg-pri-fg translate-x-4" : "bg-surface"
                  }`}
                />
            </span>
          </button>
        </div>
      </section>

      {/* ── Section: AI Image Editing (BrandAiImageCard) ─── */}
      <BrandAiImageCard
        brandId={fanpageId}
        gptcliEnabledForBrand={brand?.gptcli_enabled_for_brand ?? false}
        gptcliGlobalEnabled={brand?.gptcli_global_enabled ?? false}
        aiImagesToday={brand?.ai_images_today ?? { chatgpt: 0, codex: 0, codex_fallback: 0, template: 0 }}
        quotaPct={aiEngineStatus?.codex_usage?.window_pct}
        onToggle={onToggleGptcli}
      />
    </div>
  );
}
