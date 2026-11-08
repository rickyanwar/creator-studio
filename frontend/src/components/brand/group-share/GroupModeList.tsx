"use client";

import { useState } from "react";
import { Icon } from "@iconify/react";
import useSWR from "swr";
import { getFanpage } from "@/lib/api";
import type { FanpageDetail, GroupShareSettings, ModeKey } from "@/lib/types";
import { inputClass } from "./Fields";

const modes: { key: ModeKey; title: string; blurb: string; icon: string; enabledField: keyof FanpageDetail }[] = [
  { key: "ig", title: "Instagram", blurb: "Crawl IG sources · Repost or recreate with templates / AI edit", icon: "solar:camera-bold-duotone", enabledField: "mode1_ig_repost_enabled" },
  { key: "news", title: "News card", blurb: "Articles from RSS / news sites → branded news cards", icon: "solar:documents-bold-duotone", enabledField: "mode2_news_content_enabled" },
  { key: "discussion", title: "Discussion", blurb: "Evergreen questions and topics → engagement cards", icon: "solar:chat-round-dots-bold-duotone", enabledField: "discussion_enabled" },
  { key: "pinterest", title: "Pinterest", blurb: "Curated Pinterest boards / pins → photo cards", icon: "solar:pin-bold-duotone", enabledField: "pinterest_enabled" },
  { key: "fb_recreate", title: "FB photo recreate", blurb: "Recreate photos & quotes from Facebook pages", icon: "solar:gallery-bold-duotone", enabledField: "facebook_photo_enabled" },
  { key: "yt_clips", title: "YouTube clips", blurb: "Long videos → AI picks best moments → 9:16 vertical clips", icon: "solar:play-circle-bold-duotone", enabledField: "yt_clip_enabled" },
  { key: "ai_video", title: "AI video", blurb: "Original short videos generated with Google Flow", icon: "solar:videocamera-record-bold-duotone", enabledField: "ai_video_enabled" },
  { key: "viral_radar", title: "Viral Radar", blurb: "Watches niche-leader accounts and recreates fast-moving posts", icon: "solar:radar-2-bold-duotone", enabledField: "radar_enabled" },
];

const sourceOptions = [
  ["follow", "Follow brand content"],
  ["groups_only", "Made for groups only"],
  ["both", "Both"],
] as const;

export function GroupModeList({ fanpageId, settings, onChange }: {
  fanpageId: number;
  settings: GroupShareSettings;
  onChange: (settings: GroupShareSettings) => void;
}) {
  const { data: fp, error } = useSWR(`fanpage-${fanpageId}`, () => getFanpage(fanpageId).then((r) => r.data as FanpageDetail));
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ ig: true });
  const onCount = modes.filter(({ key }) => settings.sources[key]?.source && settings.sources[key].source !== "off").length;

  function updateSource(key: ModeKey, patch: Partial<GroupShareSettings["sources"][ModeKey]>) {
    const current = settings.sources[key] ?? { source: "off", groups_only_per_day: 0 };
    onChange({ ...settings, sources: { ...settings.sources, [key]: { ...current, ...patch } } });
  }

  return (
    <section aria-labelledby="group-sources-heading" className="min-w-0 space-y-4">
      <div className="flex flex-wrap justify-between items-baseline gap-2 pb-1">
        <div>
          <h2 id="group-sources-heading" className="text-lg font-semibold text-text">What goes to groups</h2>
          <p className="text-xs text-text2">Switch each mode on for groups. Open Settings to choose where the group posts come from.</p>
        </div>
        <span className="text-xs text-text2 tabular-nums">{onCount} of 8 on</span>
      </div>
      {error && <p role="alert" className="text-sm text-red-500">Brand content settings could not be loaded.</p>}
      {!fp ? <p role="status" className="text-sm text-text2">{error ? "Mode controls are unavailable." : "Loading brand content settings…"}</p> : (
        <div className="space-y-3">
          {modes.map((mode) => {
            const source = settings.sources[mode.key] ?? { source: "off", groups_only_per_day: 0 };
            const enabled = source.source !== "off";
            const brandEnabled = !!fp[mode.enabledField];
            const isExpanded = !!expanded[mode.key];
            const panelId = `group-mode-${fanpageId}-${mode.key}`;
            const maxGroupPosts = mode.key === "ai_video" ? 5 : 50;
            const groupHelp = mode.key === "ai_video"
              ? "Extra AI videos for groups use Google Flow credits (about 15 credits per 10 s). Keep this low."
              : ["news", "ig", "viral_radar"].includes(mode.key)
                ? "Extra group posts come from items the brand's page has no room for (overflow), up to the daily number."
                : "Extra group posts are generated from the same sources and never go to the page.";
            const sourceLabel = source.source === "follow" ? "Follows brand"
              : source.source === "groups_only" ? `Groups only · ${source.groups_only_per_day}/day`
              : source.source === "both" ? `Brand + ${source.groups_only_per_day}/day extra` : "Off";
            return (
              <article key={mode.key} aria-label={mode.title} className="bg-surface border border-line rounded-2xl overflow-hidden transition-all">
                <div className="flex flex-wrap items-center justify-between gap-4 p-4 sm:px-6">
                  <div className="flex-1 min-w-0 basis-[220px] flex items-center gap-3.5">
                    <span className="w-10 h-10 rounded-xl bg-surface2 flex items-center justify-center shrink-0 text-text border border-line">
                      <Icon icon={mode.icon} width={20} aria-hidden="true" />
                    </span>
                    <div className="min-w-0">
                      <h3 className="text-sm font-semibold text-text leading-tight">{mode.title}</h3>
                      <p className="text-xs text-text2 leading-snug mt-0.5">{mode.blurb}</p>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-semibold px-2.5 py-1 rounded-lg bg-surface2 text-text tabular-nums border border-line">{sourceLabel}</span>
                    <span className="text-xs font-medium px-2.5 py-1 rounded-lg bg-surface2 text-text2 border border-line">Brand: {brandEnabled ? "on" : "off"}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button type="button" role="switch" aria-label={`${mode.title} for groups`} aria-checked={enabled}
                      onClick={() => updateSource(mode.key, { source: enabled ? "off" : brandEnabled ? "follow" : "groups_only" })}
                      className="inline-flex items-center gap-2 min-h-[44px] px-3 rounded-xl border border-line bg-surface text-text text-xs font-semibold cursor-pointer hover:bg-surface2 transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-pri">
                      <span aria-hidden="true" className={`relative w-9 h-5 rounded-full transition-colors ${enabled ? "bg-pri" : "bg-line"}`}>
                        <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full transition-transform shadow-sm ${enabled ? "bg-pri-fg translate-x-4" : "bg-surface"}`} />
                      </span>
                      <span>{enabled ? "On" : "Off"}</span>
                    </button>
                    <button type="button" aria-label={`${mode.title} group settings`} aria-expanded={isExpanded} aria-controls={panelId}
                      onClick={() => setExpanded((prev) => ({ ...prev, [mode.key]: !prev[mode.key] }))}
                      className="inline-flex items-center gap-1.5 min-h-[44px] px-3.5 rounded-xl border border-line bg-surface text-text text-xs font-semibold cursor-pointer hover:bg-surface2 transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-pri">
                      <span>Settings</span>
                      <Icon icon="solar:alt-arrow-down-bold" width={14} aria-hidden="true" className={`transition-transform duration-200 motion-reduce:transition-none ${isExpanded ? "rotate-180" : ""}`} />
                    </button>
                  </div>
                </div>
                <div id={panelId} hidden={!isExpanded} className="border-t border-line p-5 sm:p-6 bg-surface/50">
                  <div className="space-y-3">
                    <div role="group" aria-label={`${mode.title} group content source`} aria-describedby={`${panelId}-help${brandEnabled ? "" : ` ${panelId}-brand-off`}`} className="flex flex-wrap gap-1 rounded-xl border border-line bg-surface2 p-1">
                      {sourceOptions.map(([value, label]) => (
                        <button key={value} type="button" aria-pressed={source.source === value} disabled={!brandEnabled && value !== "groups_only"}
                          onClick={() => updateSource(mode.key, { source: value })}
                          className={`min-h-[44px] min-w-0 flex-1 basis-[160px] rounded-lg px-3 py-2 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-pri ${source.source === value ? "bg-pri-soft text-pri-text" : "text-text hover:bg-surface"}`}>
                          {label}
                        </button>
                      ))}
                    </div>
                    {!brandEnabled && <p id={`${panelId}-brand-off`} className="text-xs text-text2">This mode is off on the brand page, so there are no brand posts to follow.</p>}
                    {(source.source === "groups_only" || source.source === "both") && (
                      <div className="min-w-0 space-y-1">
                        <label htmlFor={`${panelId}-count`} className="block text-sm font-medium text-text">Extra group posts per day</label>
                        <input id={`${panelId}-count`} type="number" min={0} max={maxGroupPosts} step={1} value={source.groups_only_per_day}
                          aria-describedby={`${panelId}-help`} className={`${inputClass} min-h-[44px]`}
                          onChange={(event) => {
                            const value = event.target.valueAsNumber;
                            updateSource(mode.key, { groups_only_per_day: Number.isFinite(value) ? Math.max(0, Math.min(maxGroupPosts, Math.trunc(value))) : 0 });
                          }} />
                      </div>
                    )}
                    <p id={`${panelId}-help`} className="text-xs text-text2">Follow brand content reuses posts after they go live on the brand&apos;s page. {groupHelp}</p>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
