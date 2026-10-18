"use client";

import React from "react";
import { Icon } from "@iconify/react";
import { clsx } from "clsx";

export interface ModeConfig {
  label: string;
  icon: string;
}

export const MODE_MAP: Record<string, ModeConfig> = {
  // ── Instagram ─────────────────────────────────────
  ig: { label: "Instagram", icon: "solar:camera-bold-duotone" },
  instagram: { label: "Instagram", icon: "solar:camera-bold-duotone" },
  ig_repost: { label: "Instagram · repost", icon: "solar:camera-bold-duotone" },
  repost: { label: "Instagram · repost", icon: "solar:camera-bold-duotone" },
  ig_recreate: { label: "Instagram · recreate", icon: "solar:camera-bold-duotone" },
  recreate: { label: "Instagram · recreate", icon: "solar:camera-bold-duotone" },
  mode1: { label: "Instagram · repost", icon: "solar:camera-bold-duotone" },
  "1": { label: "Instagram · repost", icon: "solar:camera-bold-duotone" },
  mode3: { label: "Instagram · recreate", icon: "solar:camera-bold-duotone" },
  "3": { label: "Instagram · recreate", icon: "solar:camera-bold-duotone" },

  // ── News ──────────────────────────────────────────
  news: { label: "News card", icon: "solar:documents-bold-duotone" },
  news_content: { label: "News card", icon: "solar:documents-bold-duotone" },
  news_card: { label: "News card", icon: "solar:documents-bold-duotone" },
  mode2: { label: "News card", icon: "solar:documents-bold-duotone" },
  "2": { label: "News card", icon: "solar:documents-bold-duotone" },

  // ── Discussion ────────────────────────────────────
  discussion: { label: "Discussion", icon: "solar:chat-round-dots-bold-duotone" },
  discussion_card: { label: "Discussion", icon: "solar:chat-round-dots-bold-duotone" },
  mode4: { label: "Discussion", icon: "solar:chat-round-dots-bold-duotone" },
  "4": { label: "Discussion", icon: "solar:chat-round-dots-bold-duotone" },

  // ── Pinterest ─────────────────────────────────────
  pinterest: { label: "Pinterest", icon: "solar:pin-bold-duotone" },
  pinterest_content: { label: "Pinterest", icon: "solar:pin-bold-duotone" },
  mode5: { label: "Pinterest", icon: "solar:pin-bold-duotone" },
  "5": { label: "Pinterest", icon: "solar:pin-bold-duotone" },

  // ── Facebook photo recreate ───────────────────────
  facebook: { label: "FB photo recreate", icon: "solar:gallery-bold-duotone" },
  facebook_photo: { label: "FB photo recreate", icon: "solar:gallery-bold-duotone" },
  facebook_recreate: { label: "FB photo recreate", icon: "solar:gallery-bold-duotone" },
  fb: { label: "FB photo recreate", icon: "solar:gallery-bold-duotone" },
  fb_photo: { label: "FB photo recreate", icon: "solar:gallery-bold-duotone" },
  fb_recreate: { label: "FB photo recreate", icon: "solar:gallery-bold-duotone" },
  mode6: { label: "FB photo recreate", icon: "solar:gallery-bold-duotone" },
  "6": { label: "FB photo recreate", icon: "solar:gallery-bold-duotone" },

  // ── YouTube clips ─────────────────────────────────
  youtube: { label: "YouTube clips", icon: "solar:play-circle-bold-duotone" },
  youtube_clip: { label: "YouTube clips", icon: "solar:play-circle-bold-duotone" },
  yt_clip: { label: "YouTube clips", icon: "solar:play-circle-bold-duotone" },
  clip: { label: "YouTube clips", icon: "solar:play-circle-bold-duotone" },
  clips: { label: "YouTube clips", icon: "solar:play-circle-bold-duotone" },
  mode7: { label: "YouTube clips", icon: "solar:play-circle-bold-duotone" },
  "7": { label: "YouTube clips", icon: "solar:play-circle-bold-duotone" },

  // ── AI video ──────────────────────────────────────
  aivideo: { label: "AI video", icon: "solar:videocamera-record-bold-duotone" },
  ai_video: { label: "AI video", icon: "solar:videocamera-record-bold-duotone" },
  narrative_video: { label: "AI video", icon: "solar:videocamera-record-bold-duotone" },
  mode8: { label: "AI video", icon: "solar:videocamera-record-bold-duotone" },
  "8": { label: "AI video", icon: "solar:videocamera-record-bold-duotone" },

  // ── Viral Radar ───────────────────────────────────
  radar: { label: "Viral Radar", icon: "solar:radar-2-bold-duotone" },
  viral_radar: { label: "Viral Radar", icon: "solar:radar-2-bold-duotone" },
};

export interface ModeChipProps {
  mode: string;
  size?: "sm" | "md";
  className?: string;
}

export function ModeChip({ mode, size = "md", className }: ModeChipProps) {
  const norm = (mode || "").toLowerCase().trim().replace(/[-\s]/g, "_");
  const config = MODE_MAP[norm] || {
    label: mode || "Unknown",
    icon: "solar:widget-bold-duotone",
  };

  const isSmall = size === "sm";

  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-lg border border-line bg-surface text-text font-medium shrink-0",
        isSmall ? "h-6 px-2 text-xs gap-1" : "h-7 px-2.5 text-[13px] gap-1.5",
        className
      )}
    >
      <Icon
        icon={config.icon}
        width={isSmall ? 13 : 15}
        className="shrink-0 text-text2"
      />
      <span>{config.label}</span>
    </span>
  );
}
