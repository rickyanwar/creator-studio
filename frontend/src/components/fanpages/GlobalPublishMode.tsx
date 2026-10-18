"use client";

import { Icon } from "@iconify/react";

export const PUBLISH_MODE_FIELDS = [
  "publish_mode",
  "mode2_publish_mode",
  "discussion_publish_mode",
  "pinterest_publish_mode",
  "facebook_photo_publish_mode",
  "yt_clip_publish_mode",
  "ai_video_publish_mode",
] as const;

export type PublishModeField = typeof PUBLISH_MODE_FIELDS[number];

const MODE_CONFIG: Record<
  PublishModeField,
  { label: string; outcomeAuto: string; outcomeManual: string }
> = {
  publish_mode: {
    label: "Instagram",
    outcomeAuto: "Posts go directly to publishing queue.",
    outcomeManual: "You approve each post in Queue or Designer before publishing.",
  },
  mode2_publish_mode: {
    label: "News card",
    outcomeAuto: "Renders card and schedules post automatically.",
    outcomeManual: "Waits for approval in Queue before publishing.",
  },
  discussion_publish_mode: {
    label: "Discussion",
    outcomeAuto: "Generates question card and schedules automatically.",
    outcomeManual: "Review topic and card in Queue before publishing.",
  },
  pinterest_publish_mode: {
    label: "Pinterest",
    outcomeAuto: "Picks photo, formats card and schedules automatically.",
    outcomeManual: "Review photo card in Queue before publishing.",
  },
  facebook_photo_publish_mode: {
    label: "FB photo recreate",
    outcomeAuto: "Recreates design and schedules automatically.",
    outcomeManual: "Review recreated design in Queue before publishing.",
  },
  yt_clip_publish_mode: {
    label: "YouTube clips",
    outcomeAuto: "Clips render and publish on channel pacing.",
    outcomeManual: "Review clip and caption in Queue before publishing.",
  },
  ai_video_publish_mode: {
    label: "AI video",
    outcomeAuto: "GFlow video renders and enters schedule automatically.",
    outcomeManual: "Review video and caption in Queue before publishing.",
  },
};

export function GlobalPublishMode({
  form,
  set,
}: {
  form: Record<string, unknown>;
  set: (key: string, value: unknown) => void;
}) {
  const values = PUBLISH_MODE_FIELDS.map((f) => (form[f] as string) || "auto");
  const isAllAuto = values.every((v) => v === "auto");
  const isAllManual = values.every((v) => v === "manual_review");

  return (
    <section aria-labelledby="h-review" className="bg-surface border border-line rounded-2xl p-6 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4 pb-2">
        <div className="space-y-1">
          <h2 id="h-review" className="text-lg font-semibold text-text">
            Review before posting
          </h2>
          <p className="text-sm text-text2">
            Choose whether content is auto-published or held for review in Queue.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => PUBLISH_MODE_FIELDS.forEach((f) => set(f, "auto"))}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all ${
              isAllAuto
                ? "bg-pri-soft text-pri-text border-pri"
                : "bg-surface2 text-text2 border-line hover:text-text"
            }`}
          >
            All auto-publish
          </button>
          <button
            type="button"
            onClick={() => PUBLISH_MODE_FIELDS.forEach((f) => set(f, "manual_review"))}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-all ${
              isAllManual
                ? "bg-warn-soft text-warn border-warn"
                : "bg-surface2 text-text2 border-line hover:text-text"
            }`}
          >
            All review first
          </button>
        </div>
      </div>

      <ul className="divide-y divide-line">
        {PUBLISH_MODE_FIELDS.map((f) => {
          const cfg = MODE_CONFIG[f];
          const modeVal = (form[f] as string) || "auto";
          const isAuto = modeVal === "auto";

          return (
            <li
              key={f}
              className="py-3.5 flex flex-wrap items-center justify-between gap-4"
            >
              <div className="min-w-0 flex-1">
                <span id={`rv-${f}`} className="text-sm font-semibold text-text block">
                  {cfg.label}
                </span>
                <span className="text-xs text-text2 leading-relaxed">
                  {isAuto ? cfg.outcomeAuto : cfg.outcomeManual}
                </span>
              </div>

              <div
                role="radiogroup"
                aria-labelledby={`rv-${f}`}
                className="flex gap-1 p-1 rounded-xl bg-surface2 shrink-0 border border-line"
              >
                <button
                  type="button"
                  role="radio"
                  aria-checked={isAuto}
                  onClick={() => set(f, "auto")}
                  className={`inline-flex items-center gap-1.5 min-h-[36px] px-3 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                    isAuto
                      ? "bg-surface text-pri-text shadow-sm border border-pri"
                      : "bg-transparent text-text2 border border-transparent hover:text-text"
                  }`}
                >
                  {isAuto && <Icon icon="solar:check-circle-bold" width={14} className="text-pri" />}
                  Auto-publish
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={!isAuto}
                  onClick={() => set(f, "manual_review")}
                  className={`inline-flex items-center gap-1.5 min-h-[36px] px-3 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                    !isAuto
                      ? "bg-surface text-warn shadow-sm border border-warn"
                      : "bg-transparent text-text2 border border-transparent hover:text-text"
                  }`}
                >
                  {!isAuto && <Icon icon="solar:check-circle-bold" width={14} className="text-warn" />}
                  Review first
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
