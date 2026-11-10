"use client";

import type { ReactNode } from "react";

// Same list as LOOK_FIELDS in backend/app/services/fb_share/lanes.py. These columns belong to the
// brand: the backend copies them brand -> group lane on every brand save, so the lane UI shows them
// read-only. Keep both lists in sync.
export const LANE_LOCKED_FIELDS: readonly string[] = [
  "username", "picture_url", "platform_type", "timezone", "target_country",
  // gallery, templates and brand colours
  "mode2_gallery_keywords", "mode2_gallery_niches", "mode2_default_template_id",
  "default_quote_template_id", "default_news_template_id", "default_discussion_template_id",
  "accent_color", "template_style", "brand_override", "accent_secondary_color", "badge_color",
  "badge_text_color", "quote_mark_color", "quote_streak",
  "ig_recreate_quote_template_id", "ig_recreate_news_template_id", "ig_recreate_split_template_id",
  "design_expand", "fb_visual_engine",
  // news captions and badges
  "mode2_caption_tone", "mode2_caption_language", "mode2_caption_max_length",
  "mode2_caption_hashtag_count", "mode2_caption_cta_text", "mode2_caption_custom_prompt",
  "mode2_badge_text", "badge_breaking_enabled", "badge_discussion_enabled", "news_badge_percent",
  "mode2_source_attribution", "pinterest_hashtag_count",
  // video look
  "yt_clip_watermark", "ai_video_clips", "ai_video_clips_min",
  "ai_video_logo_enabled", "ai_video_logo_size_pct", "ai_video_logo_position",
  "ai_video_logo_margin_pct", "ai_video_captions_enabled", "ai_video_caption_size",
  "ai_video_caption_bottom_pct", "ai_video_caption_highlight", "ai_video_cover_enabled",
  // caption style
  "caption_tone", "caption_language", "caption_max_length", "caption_hashtag_count",
  "caption_must_include", "caption_must_avoid", "caption_cta_text", "use_attribution",
  "caption_attribution_template", "attribution_position", "caption_custom_prompt",
  // watermark / logo
  "watermark_text", "watermark_image_url",
];

export const LOCKED_HINT = "Set on the brand page";

/** True when any of `fields` is in `lockedFields` (undefined = brand page, nothing locked). */
export function isLocked(lockedFields: readonly string[] | undefined, ...fields: string[]): boolean {
  return !!lockedFields && fields.some((field) => lockedFields.includes(field));
}

/**
 * Renders `children` unchanged when not locked. When locked, the controls are disabled and a
 * "Set on the brand page" hint is shown under them.
 */
export function LockedField({ locked, children }: { locked: boolean; children: ReactNode }) {
  if (!locked) return <>{children}</>;
  return (
    <fieldset disabled className="min-w-0 space-y-1">
      <div className="opacity-60">{children}</div>
      <p className="text-[11px] text-text2 inline-flex items-center gap-1">{LOCKED_HINT}</p>
    </fieldset>
  );
}
