"use client";

import { Icon } from "@iconify/react";
import type { FanpageDetail } from "@/lib/types";

export interface CaptionsTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  mustIncludeInput: string;
  setMustIncludeInput: (v: string) => void;
  mustAvoidInput: string;
  setMustAvoidInput: (v: string) => void;
  addTag: (key: "caption_must_include" | "caption_must_avoid", value: string) => void;
  removeTag: (key: "caption_must_include" | "caption_must_avoid", value: string) => void;
  previewSrc: string;
  setPreviewSrc: (v: string) => void;
  previewOrig: string;
  setPreviewOrig: (v: string) => void;
  previewResult: string;
  previewLoading: boolean;
  handlePreview: () => Promise<void>;
}

export function CaptionsTab({
  form,
  set,
  mustIncludeInput,
  setMustIncludeInput,
  mustAvoidInput,
  setMustAvoidInput,
  addTag,
  removeTag,
  previewSrc,
  setPreviewSrc,
  previewOrig,
  setPreviewOrig,
  previewResult,
  previewLoading,
  handlePreview,
}: CaptionsTabProps) {
  return (
    <div role="tabpanel" id="tab-panel-captions" aria-labelledby="tab-captions" className="space-y-6">
      {/* ── Section: Global Caption Rules ────────────────── */}
      <section className="bg-surface border border-line rounded-2xl p-6 space-y-5">
        <div>
          <h2 className="text-lg font-semibold text-text">Global Caption Rules</h2>
          <p className="text-xs text-text2 mt-0.5">
            One set of caption settings per brand, used by every content mode (IG Repost/Recreate, News, Discussion, Pinterest, FB Photo, YT Clip, AI Video).
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">Fanpage language</label>
            <select
              className="input-rect w-full"
              value={(form.caption_language as string) ?? "en"}
              onChange={(e) => {
                set("caption_language", e.target.value);
                set("mode2_caption_language", e.target.value);
              }}
            >
              <option value="en">English (en)</option>
              <option value="fr">French (fr)</option>
              <option value="es">Spanish (es)</option>
              <option value="de">German (de)</option>
              <option value="it">Italian (it)</option>
              <option value="pt">Portuguese (pt)</option>
              <option value="id">Indonesian (id)</option>
              <option value="nl">Dutch (nl)</option>
            </select>
          </div>
          <div>
            <label className="label">Tone</label>
            <input
              className="input-rect w-full"
              value={(form.caption_tone as string) ?? ""}
              onChange={(e) => set("caption_tone", e.target.value)}
              placeholder="engaging, inspirational, humorous…"
            />
          </div>
          <div>
            <label className="label">Max Length (chars)</label>
            <input
              className="input-rect w-full"
              type="number"
              value={(form.caption_max_length as number) ?? 500}
              onChange={(e) => set("caption_max_length", parseInt(e.target.value))}
            />
          </div>
          <div>
            <label className="label">Hashtag Count</label>
            <input
              className="input-rect w-full"
              type="number"
              value={(form.caption_hashtag_count as number) ?? 5}
              onChange={(e) => set("caption_hashtag_count", parseInt(e.target.value))}
            />
          </div>
        </div>

        {/* Must Include tags */}
        <div>
          <label className="label">Must Include Keywords</label>
          <div className="flex flex-wrap gap-2 mb-2">
            {((form.caption_must_include as string[]) ?? []).map((tag) => (
              <span key={tag} className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-info-soft text-info">
                {tag}
                <button type="button" onClick={() => removeTag("caption_must_include", tag)}>
                  <Icon icon="solar:close-bold" width={10} />
                </button>
              </span>
            ))}
          </div>
          <input
            className="input-rect w-full"
            placeholder="Add keyword, press Enter"
            value={mustIncludeInput}
            onChange={(e) => setMustIncludeInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                addTag("caption_must_include", mustIncludeInput.trim());
                setMustIncludeInput("");
              }
            }}
          />
        </div>

        {/* Must Avoid tags */}
        <div>
          <label className="label">Must Avoid Words</label>
          <div className="flex flex-wrap gap-2 mb-2">
            {((form.caption_must_avoid as string[]) ?? []).map((tag) => (
              <span key={tag} className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold bg-bad-soft text-bad">
                {tag}
                <button type="button" onClick={() => removeTag("caption_must_avoid", tag)}>
                  <Icon icon="solar:close-bold" width={10} />
                </button>
              </span>
            ))}
          </div>
          <input
            className="input-rect w-full"
            placeholder="Add word to avoid, press Enter"
            value={mustAvoidInput}
            onChange={(e) => setMustAvoidInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                addTag("caption_must_avoid", mustAvoidInput.trim());
                setMustAvoidInput("");
              }
            }}
          />
        </div>

        {/* CTA text */}
        <div>
          <label className="label">Call-to-Action Text</label>
          <input
            className="input-rect w-full"
            value={(form.caption_cta_text as string) ?? ""}
            onChange={(e) => set("caption_cta_text", e.target.value)}
            placeholder="Follow for more! / Link in bio"
          />
        </div>

        {/* Attribution */}
        <div className="space-y-3 pt-2">
          <div className="flex items-center gap-2">
            <input
              id="use-attribution"
              type="checkbox"
              className="w-4 h-4 rounded accent-pri"
              checked={(form.use_attribution as boolean) ?? true}
              onChange={(e) => set("use_attribution", e.target.checked)}
            />
            <label htmlFor="use-attribution" className="text-xs text-text font-medium cursor-pointer">
              Add attribution
            </label>
          </div>
          {form.use_attribution && (
            <div className="flex flex-wrap items-center gap-3">
              <input
                className="input-rect flex-1 min-w-[200px]"
                value={(form.caption_attribution_template as string) ?? "via @{source_username}"}
                onChange={(e) => set("caption_attribution_template", e.target.value)}
                placeholder="via @{source_username}"
              />
              <select
                className="input-rect"
                value={(form.attribution_position as string) ?? "caption_end"}
                onChange={(e) => set("attribution_position", e.target.value)}
              >
                <option value="caption_end">At end</option>
                <option value="caption_start">At start</option>
              </select>
            </div>
          )}
        </div>

        {/* Custom prompt */}
        <div>
          <label className="label">Additional AI Instructions</label>
          <textarea
            className="input-rect w-full h-24 resize-none"
            value={(form.caption_custom_prompt as string) ?? ""}
            onChange={(e) => set("caption_custom_prompt", e.target.value)}
            placeholder="e.g. Always mention our brand name. Keep it family-friendly."
          />
        </div>
      </section>

      {/* ── Section: Live Caption Preview ────────────────── */}
      <section className="bg-surface border border-line rounded-2xl p-6 space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-text">Caption Preview</h2>
          <p className="text-xs text-text2 mt-0.5">
            Test the AI caption writer with an example original caption. Nothing is posted.
          </p>
        </div>
        <div>
          <label className="label">Source IG Username</label>
          <input
            className="input-rect w-full"
            value={previewSrc}
            onChange={(e) => setPreviewSrc(e.target.value)}
            placeholder="e.g. motogp"
          />
        </div>
        <div>
          <label className="label">Original Caption (from IG)</label>
          <textarea
            className="input-rect w-full h-24 resize-none"
            value={previewOrig}
            onChange={(e) => setPreviewOrig(e.target.value)}
            placeholder="Paste an Instagram caption here…"
          />
        </div>
        <button
          type="button"
          onClick={handlePreview}
          disabled={previewLoading || !previewSrc || !previewOrig}
          className="btn-primary inline-flex items-center gap-2"
        >
          <Icon icon="solar:magic-stick-3-bold-duotone" width={16} />
          {previewLoading ? "Generating…" : "Generate Preview"}
        </button>
        {previewResult && (
          <div className="p-4 bg-surface2 border border-line rounded-xl">
            <p className="text-[11px] text-text2 mb-1 font-semibold uppercase tracking-wide">
              Generated Caption
            </p>
            <p className="text-sm text-text whitespace-pre-wrap">{previewResult}</p>
          </div>
        )}
      </section>
    </div>
  );
}
