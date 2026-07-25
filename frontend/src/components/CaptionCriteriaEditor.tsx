"use client";

import { Icon } from "@iconify/react";

export type CaptionCriteria = {
  caption_tone: string;
  caption_language: string;
  caption_max_length: string; // kept as string for the input; "" = inherit
  caption_hashtag_count: string;
  caption_cta_text: string;
  caption_custom_prompt: string;
};

export const emptyCaptionCriteria: CaptionCriteria = {
  caption_tone: "",
  caption_language: "",
  caption_max_length: "",
  caption_hashtag_count: "",
  caption_cta_text: "",
  caption_custom_prompt: "",
};

/** Build the CaptionCriteria form value from an API source row (nullable fields). */
export function captionFromSource(s: {
  caption_tone?: string | null;
  caption_language?: string | null;
  caption_max_length?: number | null;
  caption_hashtag_count?: number | null;
  caption_cta_text?: string | null;
  caption_custom_prompt?: string | null;
}): CaptionCriteria {
  return {
    caption_tone: s.caption_tone ?? "",
    caption_language: s.caption_language ?? "",
    caption_max_length: s.caption_max_length != null ? String(s.caption_max_length) : "",
    caption_hashtag_count: s.caption_hashtag_count != null ? String(s.caption_hashtag_count) : "",
    caption_cta_text: s.caption_cta_text ?? "",
    caption_custom_prompt: s.caption_custom_prompt ?? "",
  };
}

/** Convert the form value into an API payload ("" → clear to fanpage default). */
export function captionToPayload(c: CaptionCriteria) {
  return {
    caption_tone: c.caption_tone,
    caption_language: c.caption_language,
    caption_max_length: c.caption_max_length === "" ? null : parseInt(c.caption_max_length),
    caption_hashtag_count: c.caption_hashtag_count === "" ? null : parseInt(c.caption_hashtag_count),
    caption_cta_text: c.caption_cta_text,
    caption_custom_prompt: c.caption_custom_prompt,
  };
}

export function CaptionCriteriaEditor({
  value,
  onChange,
  onSave,
  saving,
  hint,
}: {
  value: CaptionCriteria;
  onChange: (next: CaptionCriteria) => void;
  onSave?: () => void;
  saving?: boolean;
  hint?: string;
}) {
  const set = (k: keyof CaptionCriteria, v: string) => onChange({ ...value, [k]: v });

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-ink-48">
        {hint ?? "Per-source caption criteria (global). Empty fields inherit the fanpage's criteria."}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="label">Tone</label>
          <input className="input w-full" placeholder="inherit fanpage"
            value={value.caption_tone} onChange={(e) => set("caption_tone", e.target.value)} />
        </div>
        <div>
          <label className="label">Language</label>
          <input className="input w-full" placeholder="inherit (e.g. id / en)"
            value={value.caption_language} onChange={(e) => set("caption_language", e.target.value)} />
        </div>
        <div>
          <label className="label">Max length</label>
          <input className="input w-full" type="number" placeholder="inherit"
            value={value.caption_max_length} onChange={(e) => set("caption_max_length", e.target.value)} />
        </div>
        <div>
          <label className="label">Hashtag count</label>
          <input className="input w-full" type="number" placeholder="inherit"
            value={value.caption_hashtag_count} onChange={(e) => set("caption_hashtag_count", e.target.value)} />
        </div>
        <div className="sm:col-span-2">
          <label className="label">Call-to-action</label>
          <input className="input w-full" placeholder="inherit fanpage"
            value={value.caption_cta_text} onChange={(e) => set("caption_cta_text", e.target.value)} />
        </div>
        <div className="sm:col-span-2">
          <label className="label">Custom prompt / notes</label>
          <textarea className="input w-full" rows={2} placeholder="inherit fanpage"
            value={value.caption_custom_prompt} onChange={(e) => set("caption_custom_prompt", e.target.value)} />
        </div>
      </div>
      {onSave && (
        <button onClick={onSave} disabled={saving} className="btn btn-primary flex items-center gap-2">
          {saving ? <Icon icon="svg-spinners:ring-resize" width={14} /> : <Icon icon="solar:diskette-bold-duotone" width={14} />}
          Save caption criteria
        </button>
      )}
    </div>
  );
}
