"use client";

import React from "react";
import Link from "next/link";
import { Icon } from "@iconify/react";
import type { SocialDestination } from "@/lib/types";
import type { BrandSummaryOut, ContentModeOut } from "@/lib/socialBrandsApi";

interface Props {
  destination: SocialDestination;
  brand?: BrandSummaryOut | null;
}

const DEFAULT_CONTENT_MODES: ContentModeOut[] = [
  { key: "mode2", label: "News card", enabled: true, daily_count: 3, media: "Image" },
  { key: "mode3", label: "IG recreate", enabled: true, daily_count: 2, media: "Image" },
  { key: "mode4", label: "Discussion card", enabled: true, daily_count: 1, media: "Image" },
  { key: "mode6", label: "FB photo recreate", enabled: false, daily_count: null, media: "Image" },
  { key: "mode5", label: "Pinterest card", enabled: false, daily_count: null, media: "Image" },
  { key: "mode7", label: "YouTube clips", enabled: true, daily_count: 2, media: "Video" },
  { key: "mode8", label: "AI video", enabled: false, daily_count: null, media: "Video" },
];

const MODE_DESCRIPTIONS: Record<string, string> = {
  mode1: "Instagram source reposting with source credits",
  mode2: "Articles from your news sources, made into image cards",
  mode3: "Fast-moving Instagram posts rebuilt in your brand style",
  mode4: "Debate and hot-take cards driving fan interaction",
  mode5: "Photo-led visual cards curated from Pinterest boards",
  mode6: "Competitor Facebook photos rebuilt in your design",
  mode7: "Short vertical clips cut from monitored YouTube channels",
  mode8: "AI generated video reels via Google Flow",
  "News card": "Articles from your news sources, made into image cards",
  "IG recreate": "Fast-moving Instagram posts rebuilt in your brand style",
  "Discussion card": "Debate and hot-take cards driving fan interaction",
  "FB photo recreate": "Competitor Facebook photos rebuilt in your design",
  "Pinterest card": "Photo-led visual cards curated from Pinterest boards",
  "YouTube clips": "Short vertical clips cut from monitored YouTube channels",
  "AI video": "AI generated video reels via Google Flow",
};

export function ContentTab({ destination, brand }: Props) {
  const brandName = brand?.name || "Brand";
  const modes = brand?.content && brand.content.length > 0 ? brand.content : DEFAULT_CONTENT_MODES;
  const supportedMedia = destination.supported_media || ["image", "album", "video"];

  return (
    <div className="flex flex-col gap-6">
      {/* ── Brand Relationship Card ── */}
      <section className="p-5 rounded-xl bg-surface2 border border-line flex flex-wrap items-center justify-between gap-4">
        <div className="max-w-xl space-y-1.5">
          <h2 className="text-sm font-bold text-text flex items-center gap-2">
            <Icon icon="solar:folder-with-files-bold-duotone" className="text-pri" width={18} />
            Content comes from the brand
          </h2>
          <p className="text-xs text-text2 leading-relaxed">
            <strong className="text-text">{brandName}</strong> is the Content Project. It decides what gets made — News, IG recreate, Discussion and so on — and every channel of the brand receives the same posts. This channel only filters by <strong className="text-text">media type</strong> (configured under Media tab).
          </p>
        </div>

        {brand?.id ? (
          <Link
            href={`/brands/${brand.id}`}
            className="btn-primary text-xs px-4 py-2 flex-shrink-0"
          >
            Edit content on brand →
          </Link>
        ) : (
          <Link
            href="/brands"
            className="btn-primary text-xs px-4 py-2 flex-shrink-0"
          >
            Manage Brands →
          </Link>
        )}
      </section>

      {/* ── What Reaches This Page Table ── */}
      <section className="card p-6 space-y-4">
        <div>
          <h3 className="text-base font-bold text-text">
            What reaches this channel
          </h3>
          <p className="text-xs text-text2 mt-0.5">
            Read-only here. Turn content on or off, and set quotas, on the brand.
          </p>
        </div>

        <div className="border border-line rounded-xl overflow-hidden divide-y divide-line">
          {modes.map((m) => {
            const desc = MODE_DESCRIPTIONS[m.key] || MODE_DESCRIPTIONS[m.label] || "Brand content mode";
            const mediaType = m.media?.toLowerCase() || "image";

            // Check if channel's supported media includes this mode's media
            const channelAccepts =
              mediaType === "video"
                ? supportedMedia.includes("video")
                : supportedMedia.includes("image") || supportedMedia.includes("album");

            let reachText = "Reaches this Page";
            let reachColor = "text-pri";
            let reachDot = "bg-pri";

            if (!m.enabled) {
              reachText = "Brand doesn’t make it";
              reachColor = "text-text2";
              reachDot = "bg-text2";
            } else if (!channelAccepts) {
              reachText = `Channel ignores ${m.media}`;
              reachColor = "text-warn";
              reachDot = "bg-warn";
            }

            const brandStateText = m.enabled
              ? `On${m.daily_count ? ` · ~${m.daily_count} / day` : ""}`
              : "Off on the brand";

            return (
              <div
                key={m.key}
                className="flex flex-wrap items-center justify-between gap-3 p-4 bg-surface hover:bg-surface2 transition"
              >
                <div className="flex-1 min-w-[240px]">
                  <span className="text-sm font-semibold text-text block">
                    {m.label}
                  </span>
                  <span className="text-xs text-text2 mt-0.5 block">
                    {desc}
                  </span>
                </div>

                <div className="flex items-center gap-4 flex-wrap">
                  <span className="px-2.5 py-1 rounded-full text-xs font-semibold bg-surface2 border border-line text-text2">
                    {m.media}
                  </span>

                  <span
                    className={`text-xs min-w-[130px] font-medium ${
                      m.enabled ? "text-text" : "text-text2"
                    }`}
                  >
                    {brandStateText}
                  </span>

                  <div className={`flex items-center gap-2 min-w-[170px] text-xs font-semibold ${reachColor}`}>
                    <span className={`w-2 h-2 rounded-full ${reachDot}`} />
                    <span>{reachText}</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
