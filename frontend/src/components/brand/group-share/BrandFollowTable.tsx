"use client";

import useSWR from "swr";
import { getFanpage } from "@/lib/api";
import type { FanpageDetail, GroupShareSettings, ModeKey } from "@/lib/types";
import { TableWrap, inputClass, panelClass } from "./Fields";
import { sourceOf } from "./LaneContent";

const MODES: { key: ModeKey; title: string; enabledField: keyof FanpageDetail }[] = [
  { key: "ig", title: "Instagram", enabledField: "mode1_ig_repost_enabled" },
  { key: "news", title: "News card", enabledField: "mode2_news_content_enabled" },
  { key: "discussion", title: "Discussion", enabledField: "discussion_enabled" },
  { key: "pinterest", title: "Pinterest", enabledField: "pinterest_enabled" },
  { key: "fb_recreate", title: "FB photo recreate", enabledField: "facebook_photo_enabled" },
  { key: "yt_clips", title: "YouTube clips", enabledField: "yt_clip_enabled" },
  { key: "ai_video", title: "AI video", enabledField: "ai_video_enabled" },
  { key: "viral_radar", title: "Viral Radar", enabledField: "radar_enabled" },
];

const MAX_FOLLOW_PER_DAY = 50;

/** Per mode: how many of the brand page's newest posts per day also go to groups (follow_per_day). */
export function BrandFollowTable({ fanpageId, settings, onChange }: {
  fanpageId: number;
  settings: GroupShareSettings;
  onChange: (settings: GroupShareSettings) => void;
}) {
  // Same SWR key and fetcher as the brand page, so this reads the brand's saved mode switches.
  const { data: fp, error } = useSWR(`fanpage-${fanpageId}`, () => getFanpage(fanpageId).then((r) => r.data as FanpageDetail));

  function setFollow(key: ModeKey, value: number) {
    const current = sourceOf(settings, key);
    onChange({ ...settings, sources: { ...settings.sources, [key]: { ...current, follow_per_day: value } } });
  }

  return (
    <section className={panelClass} aria-labelledby="group-follow-heading">
      <div>
        <h3 id="group-follow-heading" className="font-semibold text-text">From brand page posts</h3>
        <p className="text-sm text-text2">
          After a post goes live on the brand page, it can also go to groups. Per mode, only the newest posts up to
          this number per day are shared. 0 = none.
        </p>
      </div>
      {error ? (
        <p role="alert" className="text-sm text-red-500">Brand content settings could not be loaded.</p>
      ) : !fp ? (
        <p role="status" className="text-sm text-text2">Loading brand content settings…</p>
      ) : (
        <TableWrap label="Brand page posts shared to groups">
          <thead>
            <tr>{["Mode", "On the page", "Posts per day to groups"].map((label) => <th scope="col" key={label}>{label}</th>)}</tr>
          </thead>
          <tbody>
            {MODES.map((mode) => {
              const brandOn = !!fp[mode.enabledField];
              const value = sourceOf(settings, mode.key).follow_per_day;
              const inputId = `group-follow-${mode.key}`;
              return (
                <tr key={mode.key}>
                  <td className="font-medium"><label htmlFor={inputId}>{mode.title}</label></td>
                  <td>
                    <span className={`whitespace-nowrap rounded-full px-2 py-1 text-xs font-medium ${brandOn ? "bg-pri-soft text-pri-text" : "bg-surface2 text-text2"}`}>
                      {brandOn ? "On" : "Off"}
                    </span>
                  </td>
                  <td>
                    <input
                      id={inputId}
                      type="number"
                      min={0}
                      max={MAX_FOLLOW_PER_DAY}
                      step={1}
                      className={`${inputClass} sm:w-32 disabled:opacity-50`}
                      disabled={!brandOn}
                      title={brandOn ? undefined : "This mode is off on the brand page"}
                      value={value}
                      onChange={(e) => {
                        const next = e.target.valueAsNumber;
                        setFollow(mode.key, Number.isFinite(next) ? Math.max(0, Math.min(MAX_FOLLOW_PER_DAY, Math.trunc(next))) : 0);
                      }}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </TableWrap>
      )}
    </section>
  );
}
