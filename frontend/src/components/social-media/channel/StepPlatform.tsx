"use client";

import React from "react";
import Link from "next/link";
import { Icon } from "@iconify/react";
import type { SocialDestination } from "@/lib/types";

export interface PlatformOption {
  id: string;
  label: string;
  avail: string;
  enabled: boolean;
  icon: string;
  iconBg: string;
}

export const PLATFORMS: PlatformOption[] = [
  {
    id: "facebook_page",
    label: "Facebook Page",
    avail: "Phone, Repliz or both",
    enabled: true,
    icon: "solar:facebook-bold",
    iconBg: "bg-[#0866FF]",
  },
  {
    id: "facebook_profile",
    label: "Facebook Profile timeline",
    avail: "Phone",
    enabled: true,
    icon: "solar:user-bold",
    iconBg: "bg-slate-700",
  },
  {
    id: "instagram",
    label: "Instagram",
    avail: "Publishing to this platform is not built yet.",
    enabled: false,
    icon: "solar:camera-bold",
    iconBg: "bg-[#E1306C]",
  },
  {
    id: "youtube",
    label: "YouTube",
    avail: "Publishing to this platform is not built yet.",
    enabled: false,
    icon: "solar:video-library-bold",
    iconBg: "bg-[#FF0000]",
  },
  {
    id: "x",
    label: "X",
    avail: "Publishing to this platform is not built yet.",
    enabled: false,
    icon: "solar:hashtag-bold",
    iconBg: "bg-black",
  },
  {
    id: "threads",
    label: "Threads",
    avail: "Publishing to this platform is not built yet.",
    enabled: false,
    icon: "solar:chat-round-line-bold",
    iconBg: "bg-black",
  },
];

interface Props {
  isStep1Valid: boolean;
  kind: "facebook_page" | "facebook_profile";
  setKind: (k: "facebook_page" | "facebook_profile") => void;
  channelName: string;
  setChannelName: (name: string) => void;
  facebookActorId: string;
  setFacebookActorId: (id: string) => void;
  parentProfileId: number | null;
  setParentProfileId: (id: number | null) => void;
  profiles: SocialDestination[];
  mode: "phone" | "repliz" | "mixed";
}

export function StepPlatform({
  isStep1Valid,
  kind,
  setKind,
  channelName,
  setChannelName,
  facebookActorId,
  setFacebookActorId,
  parentProfileId,
  setParentProfileId,
  profiles,
  mode,
}: Props) {
  return (
    <section
      aria-labelledby="step2-h"
      className={`card p-5 space-y-4 transition ${
        !isStep1Valid ? "opacity-50 pointer-events-none" : ""
      }`}
    >
      <h2 id="step2-h" className="text-sm font-bold text-text-primary flex items-center gap-2">
        <span className="w-5 h-5 rounded-full bg-primary-main/20 text-primary-main text-xs flex items-center justify-center font-bold">
          2
        </span>
        Platform
      </h2>

      <div
        role="radiogroup"
        aria-label="Platform"
        className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3"
      >
        {PLATFORMS.map((p) => {
          const isSelected = kind === p.id;
          return (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={isSelected}
              disabled={!p.enabled}
              onClick={() => p.enabled && setKind(p.id as any)}
              className={`text-left p-3.5 rounded-xl border transition flex items-center gap-3 min-h-[76px] ${
                !p.enabled
                  ? "opacity-50 cursor-not-allowed bg-bg-default border-divider-soft"
                  : isSelected
                  ? "bg-[#111a2e] border-primary-main ring-1 ring-primary-main"
                  : "bg-bg-paper border-divider-soft hover:border-text-disabled"
              }`}
            >
              <span
                className={`w-9 h-9 rounded-lg flex items-center justify-center text-white flex-shrink-0 ${p.iconBg}`}
              >
                <Icon icon={p.icon} width={20} />
              </span>
              <div className="flex flex-col gap-0.5 min-w-0">
                <span className="text-xs font-bold text-text-primary truncate">
                  {p.label}
                </span>
                <span
                  className={`text-[11px] leading-tight ${
                    p.enabled ? "text-text-secondary" : "text-amber-400"
                  }`}
                >
                  {p.avail}
                </span>
              </div>
            </button>
          );
        })}
      </div>

      {kind === "facebook_profile" && (
        <div className="p-3.5 rounded-xl bg-[#0f1d33] border border-[#1e3a5f] text-xs text-text-secondary leading-relaxed">
          This adds a Profile’s <strong className="text-text-primary">own timeline</strong> as a channel. The Profile itself (login, phone, managed Pages) is set up once under{" "}
          <Link href="/social-media/accounts" className="text-primary-main font-semibold hover:underline">
            Publishing accounts
          </Link>.
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-1">
        <div>
          <label htmlFor="channel-name" className="label">
            Channel Name *
          </label>
          <input
            id="channel-name"
            type="text"
            required
            value={channelName}
            onChange={(e) => setChannelName(e.target.value)}
            placeholder="e.g. Formula Weekend Official"
            className="input-rect"
          />
        </div>

        <div>
          <label htmlFor="facebook-actor-id" className="label">
            {kind === "facebook_profile" ? "Profile ID as seen on phone" : "Page ID as seen on the phone"}
          </label>
          <input
            id="facebook-actor-id"
            type="text"
            value={facebookActorId}
            onChange={(e) => setFacebookActorId(e.target.value)}
            placeholder="e.g. 100094901131361 (optional for Repliz-only)"
            className="input-rect font-mono text-xs"
          />
        </div>
      </div>

      {kind === "facebook_page" && (
        <div className="pt-1">
          <label htmlFor="parent-profile-select" className="label">
            Posted by Facebook Profile {mode !== "repliz" ? "*" : "(optional for Repliz-only)"}
          </label>
          <select
            id="parent-profile-select"
            value={parentProfileId ?? ""}
            onChange={(e) => setParentProfileId(e.target.value ? Number(e.target.value) : null)}
            className="input-rect max-w-sm"
          >
            <option value="">Select managing profile...</option>
            {profiles.map((pr) => (
              <option key={pr.id} value={pr.id}>
                {pr.name} {pr.facebook_actor_id ? `(${pr.facebook_actor_id})` : ""}
              </option>
            ))}
          </select>
        </div>
      )}
    </section>
  );
}
