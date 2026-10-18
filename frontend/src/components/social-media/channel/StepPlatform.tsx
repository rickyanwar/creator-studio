"use client";

import React from "react";
import Link from "next/link";
import { Icon } from "@iconify/react";
import type { SocialDestination } from "@/lib/types";
import {
  findMatchingReplizAccount,
  getPlatformRuleInfo,
  type ReplizAccountLike,
} from "@/lib/platformChannelsApi";

export interface PlatformOption {
  id: string;
  label: string;
  avail: string;
  enabled: boolean;
  icon: string;
  iconBg: string;
}

export const BASE_PLATFORMS: PlatformOption[] = [
  {
    id: "facebook_page",
    label: "Facebook Page",
    avail: "Phone, Repliz or both",
    enabled: true,
    icon: "logos:facebook",
    iconBg: "bg-info-soft text-info",
  },
  {
    id: "facebook_profile",
    label: "Facebook Profile timeline",
    avail: "Phone",
    enabled: true,
    icon: "solar:user-bold",
    iconBg: "bg-surface2 text-text border border-line",
  },
  {
    id: "instagram",
    label: "Instagram",
    avail: "Connect this account in Repliz first",
    enabled: false,
    icon: "skill-icons:instagram",
    iconBg: "bg-rose-500/15 text-rose-500",
  },
  {
    id: "youtube",
    label: "YouTube",
    avail: "Connect this account in Repliz first",
    enabled: false,
    icon: "logos:youtube-icon",
    iconBg: "bg-bad-soft text-bad",
  },
  {
    id: "x",
    label: "X",
    avail: "Connect this account in Repliz first",
    enabled: false,
    icon: "ri:twitter-x-fill",
    iconBg: "bg-surface2 text-text border border-line",
  },
  {
    id: "threads",
    label: "Threads",
    avail: "Connect this account in Repliz first",
    enabled: false,
    icon: "ri:threads-fill",
    iconBg: "bg-surface2 text-text border border-line",
  },
];

interface Props {
  isStep1Valid: boolean;
  kind: string;
  setKind: (k: any) => void;
  channelName: string;
  setChannelName: (name: string) => void;
  facebookActorId: string;
  setFacebookActorId: (id: string) => void;
  parentProfileId: number | null;
  setParentProfileId: (id: number | null) => void;
  profiles: SocialDestination[];
  mode: "phone" | "repliz" | "mixed";
  replizAccounts?: ReplizAccountLike[] | null;
  brand?: { name?: string; username?: string | null } | null;
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
  replizAccounts,
  brand,
}: Props) {
  const isFacebook = kind === "facebook_page" || kind === "facebook_profile";
  const platformInfo = getPlatformRuleInfo(kind);

  const platforms = BASE_PLATFORMS.map((p) => {
    if (p.id === "facebook_page" || p.id === "facebook_profile") {
      return p;
    }
    const matching = findMatchingReplizAccount(p.id, replizAccounts, brand);
    if (matching) {
      return {
        ...p,
        enabled: true,
        avail: `Repliz · ${matching.name}`,
      };
    }
    return {
      ...p,
      enabled: false,
      avail: "Connect this account in Repliz first",
    };
  });

  return (
    <section
      aria-labelledby="step2-h"
      className={`card p-5 space-y-4 transition ${
        !isStep1Valid ? "opacity-50 pointer-events-none" : ""
      }`}
    >
      <h2 id="step2-h" className="text-sm font-bold text-text flex items-center gap-2">
        <span className="w-5 h-5 rounded-full bg-pri-soft text-pri text-xs flex items-center justify-center font-bold">
          2
        </span>
        Platform
      </h2>

      <div
        role="radiogroup"
        aria-label="Platform"
        className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3"
      >
        {platforms.map((p) => {
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
                  ? "opacity-50 cursor-not-allowed bg-surface2 border-line"
                  : isSelected
                  ? "bg-surface border-pri ring-1 ring-pri"
                  : "bg-surface border-line hover:border-text2"
              }`}
            >
              <span
                className={`w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${p.iconBg}`}
              >
                <Icon icon={p.icon} width={20} />
              </span>
              <div className="flex flex-col gap-0.5 min-w-0">
                <span className="text-xs font-bold text-text truncate">
                  {p.label}
                </span>
                <span
                  className={`text-[11px] leading-tight ${
                    p.enabled ? "text-text2" : "text-warn"
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
        <div className="p-3.5 rounded-xl bg-surface2 border border-line text-xs text-text2 leading-relaxed">
          This adds a Profile’s <strong className="text-text">own timeline</strong> as a channel. The Profile itself (login, phone, managed Pages) is set up once under{" "}
          <Link href="/brands/accounts" className="text-pri font-semibold hover:underline">
            Accounts &amp; phones
          </Link>.
        </div>
      )}

      {!isFacebook && (
        <div className="p-3.5 rounded-xl bg-surface2 border border-line text-xs text-text2 leading-relaxed space-y-1">
          <div className="flex items-center gap-1.5 font-semibold text-text">
            <Icon icon={platformInfo.icon} width={16} />
            <span>{platformInfo.label} Limits</span>
          </div>
          <p className="text-text2">{platformInfo.limitsSummary}</p>
          <p className="text-[11px] text-text2">
            Publishing method is fixed to <strong className="text-text">Repliz</strong> for {platformInfo.label}. Quotes are never shortened or paraphrased.
          </p>
        </div>
      )}

      <div className={`grid grid-cols-1 ${isFacebook ? "sm:grid-cols-2" : ""} gap-4 pt-1`}>
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
            placeholder={`e.g. ${brand?.name || "Formula Weekend"} ${platformInfo.label}`}
            className="input-rect"
          />
        </div>

        {isFacebook && (
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
        )}
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
