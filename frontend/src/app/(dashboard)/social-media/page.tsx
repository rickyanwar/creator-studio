"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { SocialTabs } from "@/components/social-media/SocialTabs";
import { listBrands } from "@/lib/socialBrandsApi";
import type { BrandSummaryOut, ChannelSummaryOut } from "@/lib/socialBrandsApi";

function getInitials(name: string): string {
  if (!name) return "??";
  const words = name.trim().split(/\s+/);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

const AVATAR_COLORS = [
  "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  "bg-sky-500/15 text-sky-400 border-sky-500/30",
  "bg-amber-500/15 text-amber-400 border-amber-500/30",
  "bg-purple-500/15 text-purple-400 border-purple-500/30",
  "bg-rose-500/15 text-rose-400 border-rose-500/30",
];

function getAvatarColor(id: number): string {
  return AVATAR_COLORS[Math.abs(id) % AVATAR_COLORS.length];
}

function formatNextPost(isoString: string | null, timezone: string): string {
  if (!isoString) return "—";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return "—";

    const tz = timezone || "Europe/London";
    const localTime = new Intl.DateTimeFormat("en-GB", {
      timeZone: tz,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(d);

    const city = tz.split("/")[1]?.replace(/_/g, " ") || tz;

    const wibTime = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Jakarta",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(d);

    if (tz === "Asia/Jakarta") {
      return `${localTime} WIB`;
    }
    return `${localTime} ${city} · ${wibTime} WIB`;
  } catch {
    return "—";
  }
}

function getChannelStatus(channel: ChannelSummaryOut): {
  dot: string;
  label: string;
  color: string;
} {
  if (!channel.is_active) {
    return {
      dot: "bg-grey-500",
      label: "Paused",
      color: "text-text-disabled",
    };
  }
  const hasAttention =
    channel.attention?.some((a) => a.level === "error" || a.level === "warn") ||
    (channel.today && channel.today.needs_attention > 0);
  if (hasAttention) {
    return {
      dot: "bg-amber-500",
      label: "Needs attention",
      color: "text-amber-400",
    };
  }
  return {
    dot: "bg-emerald-500",
    label: "Live",
    color: "text-emerald-400",
  };
}

function getModeBadge(mode: string) {
  switch (mode) {
    case "phone":
      return <span className="badge badge-green text-[10px]">Phone</span>;
    case "repliz":
      return <span className="badge badge-blue text-[10px]">Repliz</span>;
    case "mixed":
      return <span className="badge badge-purple text-[10px]">Split</span>;
    default:
      return <span className="badge badge-gray text-[10px]">{mode}</span>;
  }
}

function getKindLabel(kind: string): string {
  if (kind === "facebook_page") return "Facebook Page";
  if (kind === "facebook_profile") return "Facebook Profile";
  return kind.replace(/_/g, " ");
}

interface AttentionItemWithBrand {
  brandId: number;
  brandName: string;
  channelId: number | null;
  channelName: string | null;
  level: string;
  message: string;
}

export default function SocialMediaBrandsPage() {
  const {
    data: brands = [],
    isLoading,
    error,
    mutate,
  } = useSWR<BrandSummaryOut[]>("social-brands", listBrands, {
    refreshInterval: 60000,
  });

  const [search, setSearch] = useState("");
  const [platform, setPlatform] = useState("all");
  const [statusFilter, setStatusFilter] = useState("any");

  // Aggregate stats
  const totalBrands = brands.length;
  let totalChannels = 0;
  let activeChannels = 0;
  let sumPublished = 0;
  let sumPlanned = 0;

  const attentionItems: AttentionItemWithBrand[] = [];

  for (const b of brands) {
    sumPublished += b.today?.published || 0;

    const brandAttns = b.attention || [];
    for (const a of brandAttns) {
      if (a.level === "error" || a.level === "warn") {
        const ch = a.channel_id ? b.channels?.find((c) => c.id === a.channel_id) : null;
        attentionItems.push({
          brandId: b.id,
          brandName: b.name,
          channelId: a.channel_id,
          channelName: ch?.name || null,
          level: a.level,
          message: a.message,
        });
      }
    }

    for (const c of b.channels || []) {
      totalChannels += 1;
      if (c.is_active) {
        activeChannels += 1;
        sumPlanned += c.daily_target || 0;
      }

      for (const a of c.attention || []) {
        if (
          (a.level === "error" || a.level === "warn") &&
          !brandAttns.some((ba) => ba.channel_id === c.id && ba.message === a.message)
        ) {
          attentionItems.push({
            brandId: b.id,
            brandName: b.name,
            channelId: c.id,
            channelName: c.name,
            level: a.level,
            message: a.message,
          });
        }
      }
    }
  }

  // Filtered brands
  const filteredBrands = brands.filter((brand) => {
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      const brandMatches = brand.name.toLowerCase().includes(q);
      const channelMatches = brand.channels?.some((c) =>
        c.name.toLowerCase().includes(q)
      );
      if (!brandMatches && !channelMatches) return false;
    }

    if (platform === "facebook") {
      const hasFb = brand.channels?.some(
        (c) =>
          c.kind === "facebook_page" ||
          c.kind === "facebook_profile" ||
          c.kind.toLowerCase().includes("facebook")
      );
      if (!hasFb && (brand.channels?.length || 0) > 0) return false;
    }

    if (statusFilter === "live") {
      if (!brand.is_active) return false;
      const hasLiveChannel = brand.channels?.some((c) => c.is_active);
      if (!hasLiveChannel && (brand.channels?.length || 0) > 0) return false;
    } else if (statusFilter === "needs_attention") {
      const hasBrandAttn = brand.attention?.some(
        (a) => a.level === "error" || a.level === "warn"
      );
      const hasChanAttn = brand.channels?.some(
        (c) =>
          c.attention?.some((a) => a.level === "error" || a.level === "warn") ||
          (c.today && c.today.needs_attention > 0)
      );
      if (!hasBrandAttn && !hasChanAttn) return false;
    } else if (statusFilter === "paused") {
      const isPaused =
        !brand.is_active ||
        (brand.channels?.length > 0 && brand.channels.every((c) => !c.is_active));
      if (!isPaused) return false;
    }

    return true;
  });

  return (
    <div className="max-w-6xl space-y-6">
      {/* ── Header ── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-text-primary">Social Media — Brands</h1>
          <p className="text-sm text-text-secondary mt-1">
            Your brands and every channel they publish to.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Link
            href="/content-projects"
            className="btn-secondary text-xs py-2 px-3 flex items-center gap-2"
          >
            <Icon icon="solar:folder-with-files-bold-duotone" width={16} />
            Add brand
          </Link>
          <Link
            href="/social-media/channels/new"
            className="btn-primary text-xs py-2 px-3 flex items-center gap-2"
          >
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            Add channel
          </Link>
        </div>
      </div>

      {/* ── Social Tabs ── */}
      <SocialTabs brands={totalBrands} />

      {/* ── Stat Tiles ── */}
      <section
        aria-label="Today at a glance"
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4"
      >
        <div className="card p-4 space-y-1">
          <span className="text-xs text-text-secondary">Brands</span>
          <div className="text-2xl font-bold text-text-primary">{totalBrands}</div>
        </div>

        <div className="card p-4 space-y-1">
          <span className="text-xs text-text-secondary">Channels publishing</span>
          <div className="text-2xl font-bold text-text-primary">
            {activeChannels}{" "}
            <span className="text-sm font-medium text-text-secondary">of {totalChannels}</span>
          </div>
        </div>

        <div className="card p-4 space-y-1">
          <span className="text-xs text-text-secondary">Posts today</span>
          <div className="text-2xl font-bold text-text-primary">
            {sumPublished}{" "}
            <span className="text-sm font-medium text-text-secondary">
              of {sumPlanned} planned
            </span>
          </div>
        </div>

        <div
          className={`card p-4 space-y-1 ${
            attentionItems.length > 0
              ? "border-amber-500/40 bg-amber-500/10"
              : ""
          }`}
        >
          <span
            className={`text-xs ${
              attentionItems.length > 0 ? "text-amber-400 font-medium" : "text-text-secondary"
            }`}
          >
            Needs attention
          </span>
          <div
            className={`text-2xl font-bold ${
              attentionItems.length > 0 ? "text-amber-300" : "text-text-primary"
            }`}
          >
            {attentionItems.length}
          </div>
        </div>
      </section>

      {/* ── Needs Attention List ── */}
      {attentionItems.length > 0 && (
        <section
          aria-label="Needs attention"
          className="card border border-amber-500/30 bg-bg-paper divide-y divide-divider-soft overflow-hidden"
        >
          <div className="px-4 py-3 bg-amber-500/10 flex items-center gap-2">
            <Icon icon="solar:danger-triangle-bold-duotone" width={18} className="text-amber-400 flex-shrink-0" />
            <h2 className="text-xs font-semibold text-amber-300 uppercase tracking-wide">
              Action Items Required ({attentionItems.length})
            </h2>
          </div>
          {attentionItems.map((item, idx) => (
            <div
              key={`${item.brandId}-${item.channelId}-${idx}`}
              className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-4 hover:bg-bg-paper-hover transition-colors"
            >
              <div className="flex items-start gap-3">
                <span
                  aria-hidden="true"
                  className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${
                    item.level === "error" ? "bg-error-main" : "bg-amber-400"
                  }`}
                />
                <div className="text-xs space-y-0.5">
                  <p className="text-text-primary">
                    <strong className="font-semibold">
                      {item.brandName}
                      {item.channelName ? ` · ${item.channelName}` : ""}
                    </strong>
                    <span className="text-text-secondary"> — {item.message}</span>
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                {item.channelId ? (
                  <Link
                    href={`/social-media/channels/${item.channelId}`}
                    className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1.5"
                  >
                    <span>Open channel</span>
                    <Icon icon="solar:alt-arrow-right-bold-duotone" width={14} />
                  </Link>
                ) : (
                  <Link
                    href={`/social-media/brands/${item.brandId}`}
                    className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1.5"
                  >
                    <span>Open brand</span>
                    <Icon icon="solar:alt-arrow-right-bold-duotone" width={14} />
                  </Link>
                )}
              </div>
            </div>
          ))}
        </section>
      )}

      {/* ── Filters ── */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1 min-w-[240px]">
          <label htmlFor="brand-search" className="sr-only">
            Search brands
          </label>
          <div className="relative">
            <Icon
              icon="solar:magnifer-linear"
              width={16}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-text-disabled"
            />
            <input
              id="brand-search"
              type="search"
              placeholder="Search brands or channels…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="input-rect pl-9 text-xs py-2 w-full"
            />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <label htmlFor="platform-filter" className="text-xs text-text-secondary">
            Platform:
          </label>
          <select
            id="platform-filter"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
            className="input text-xs py-2"
          >
            <option value="all">All platforms</option>
            <option value="facebook">Facebook</option>
            <option value="instagram" disabled>
              Instagram (not built yet)
            </option>
            <option value="youtube" disabled>
              YouTube (not built yet)
            </option>
            <option value="x" disabled>
              X (not built yet)
            </option>
            <option value="threads" disabled>
              Threads (not built yet)
            </option>
          </select>
        </div>

        <div className="flex items-center gap-2">
          <label htmlFor="status-filter" className="text-xs text-text-secondary">
            Status:
          </label>
          <select
            id="status-filter"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="input text-xs py-2"
          >
            <option value="any">Any status</option>
            <option value="live">Live</option>
            <option value="needs_attention">Needs attention</option>
            <option value="paused">Paused</option>
          </select>
        </div>
      </div>

      {/* ── Loading Skeleton ── */}
      {isLoading && (
        <div className="space-y-4">
          {[1, 2].map((i) => (
            <div key={i} className="card p-6 space-y-4 animate-pulse">
              <div className="flex items-center gap-4">
                <div className="w-12 h-12 rounded-xl bg-bg-paper-hover" />
                <div className="space-y-2 flex-1">
                  <div className="h-5 w-40 bg-bg-paper-hover rounded" />
                  <div className="h-3 w-64 bg-bg-paper-hover rounded" />
                </div>
              </div>
              <div className="h-16 bg-bg-paper-hover rounded-lg" />
            </div>
          ))}
        </div>
      )}

      {/* ── Error Box ── */}
      {error && !isLoading && (
        <div className="card text-center py-12 space-y-3 border-error-main/40 bg-error-lighter/20">
          <Icon
            icon="solar:danger-triangle-bold-duotone"
            width={40}
            className="mx-auto text-error-main"
          />
          <p className="text-sm font-semibold text-text-primary">Could not load brands.</p>
          <p className="text-xs text-text-secondary max-w-sm mx-auto">
            Failed to communicate with the brands API. Check your connection or retry.
          </p>
          <button
            type="button"
            onClick={() => mutate()}
            className="btn-secondary text-xs py-1.5 px-4 inline-flex items-center gap-1.5"
          >
            <Icon icon="solar:refresh-bold-duotone" width={14} />
            Retry
          </button>
        </div>
      )}

      {/* ── Empty State ── */}
      {!isLoading && !error && filteredBrands.length === 0 && (
        <div className="card text-center py-12 space-y-3">
          <Icon
            icon="solar:folder-with-files-bold-duotone"
            width={44}
            className="mx-auto text-text-disabled"
          />
          <p className="text-base font-semibold text-text-primary">
            {brands.length === 0 ? "No brands yet." : "No brands match your filters."}
          </p>
          <p className="text-xs text-text-secondary max-w-md mx-auto">
            {brands.length === 0
              ? "Create your first brand (content project) to start publishing."
              : "Try clearing search keywords or choosing different platform and status filters."}
          </p>
          {brands.length === 0 && (
            <div className="pt-2">
              <Link
                href="/content-projects"
                className="btn-primary text-xs py-2 px-4 inline-flex items-center gap-2"
              >
                <Icon icon="solar:add-circle-bold-duotone" width={16} />
                Add brand
              </Link>
            </div>
          )}
        </div>
      )}

      {/* ── Brand Cards List ── */}
      {!isLoading && !error && filteredBrands.length > 0 && (
        <section aria-label="Brands" className="space-y-5">
          {filteredBrands.map((b) => {
            const activeChs = b.channels?.filter((c) => c.is_active) || [];
            const brandPlanned = activeChs.reduce(
              (sum, c) => sum + (c.daily_target || 0),
              0
            );
            const brandPublished = b.today?.published || 0;
            const targetCount = brandPlanned > 0 ? brandPlanned : b.publish_daily_limit || 1;
            const progressPct = Math.min(
              100,
              Math.round((brandPublished / targetCount) * 100)
            );

            return (
              <article
                key={b.id}
                className="card p-6 space-y-5 hover:border-text-disabled/60 transition-colors shadow-sm"
              >
                {/* Brand Top Row */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-divider-soft pb-4">
                  <div className="flex items-center gap-4 min-w-0">
                    <div
                      aria-hidden="true"
                      className={`w-12 h-12 rounded-xl flex items-center justify-center font-bold text-base border flex-shrink-0 ${getAvatarColor(
                        b.id
                      )}`}
                    >
                      {getInitials(b.name)}
                    </div>
                    <div className="min-w-0 space-y-1">
                      <div className="flex items-center gap-2.5 flex-wrap">
                        <h2 className="text-lg font-bold text-text-primary truncate">
                          {b.name}
                        </h2>
                        {b.niche && (
                          <span className="badge badge-gray text-xs">{b.niche}</span>
                        )}
                        <span className="text-xs text-text-secondary">
                          {b.channels?.length || 0}{" "}
                          {(b.channels?.length || 0) === 1 ? "channel" : "channels"}
                        </span>
                        {b.template_style && (
                          <span className="badge badge-blue text-[10px] uppercase font-semibold">
                            {b.template_style}
                          </span>
                        )}
                        <span
                          className={`badge ${
                            b.is_active ? "badge-green" : "badge-gray"
                          }`}
                        >
                          {b.is_active ? "Live" : "Paused"}
                        </span>
                      </div>
                      <p className="text-xs text-text-secondary truncate">
                        Timezone: {b.timezone}
                        {b.target_country ? ` · Target: ${b.target_country}` : ""}
                        {b.publish_sleep_start_hour !== null &&
                          b.publish_sleep_end_hour !== null && (
                            <span>
                              {" "}
                              · Quiet hours {String(b.publish_sleep_start_hour).padStart(2, "0")}:00–
                              {String(b.publish_sleep_end_hour).padStart(2, "0")}:00
                            </span>
                          )}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 self-start sm:self-auto shrink-0">
                    <Link
                      href={`/social-media/channels/new?brand=${b.id}`}
                      className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1.5"
                    >
                      <Icon icon="solar:add-circle-bold-duotone" width={14} />
                      Add channel
                    </Link>
                    <Link
                      href={`/social-media/brands/${b.id}`}
                      className="btn-primary text-xs py-1.5 px-3 flex items-center gap-1.5"
                    >
                      <span>Open brand</span>
                      <Icon icon="solar:alt-arrow-right-bold-duotone" width={14} />
                    </Link>
                  </div>
                </div>

                {/* Compact Channels List */}
                <div className="space-y-2">
                  <span className="text-[11px] font-semibold text-text-disabled uppercase tracking-wide">
                    Connected Channels
                  </span>
                  <div className="flex flex-wrap gap-2.5">
                    {b.channels && b.channels.length > 0 ? (
                      b.channels.map((c) => {
                        const status = getChannelStatus(c);
                        return (
                          <div
                            key={c.id}
                            className="flex items-center gap-2.5 px-3 py-2 rounded-xl bg-bg-default border border-divider-soft min-h-[44px] hover:border-text-disabled transition-colors"
                          >
                            <span
                              aria-hidden="true"
                              className="w-7 h-7 rounded-lg bg-primary-lighter/30 text-primary-main flex items-center justify-center flex-shrink-0"
                            >
                              <Icon icon="mingcute:facebook-fill" width={16} />
                            </span>
                            <div className="flex flex-col">
                              <span className="text-xs font-semibold text-text-primary leading-tight">
                                {c.name}
                              </span>
                              <span className="text-[11px] text-text-secondary leading-tight mt-0.5">
                                {getKindLabel(c.kind)}
                              </span>
                            </div>
                            {getModeBadge(c.mode)}
                            <div className="flex items-center gap-1.5 text-[11px] font-medium ml-1">
                              <span
                                aria-hidden="true"
                                className={`w-2 h-2 rounded-full ${status.dot}`}
                              />
                              <span className={status.color}>{status.label}</span>
                            </div>
                            <span className="text-[11px] text-text-secondary border-l border-divider-soft pl-2 ml-1">
                              {c.today?.published || 0} / {c.daily_target}
                            </span>
                          </div>
                        );
                      })
                    ) : (
                      <div className="text-xs text-text-disabled italic py-1">
                        No channels connected yet.
                      </div>
                    )}
                    <Link
                      href={`/social-media/channels/new?brand=${b.id}`}
                      className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-dashed border-divider-soft text-text-secondary hover:text-text-primary hover:border-text-disabled text-xs transition-colors"
                    >
                      <Icon icon="solar:add-circle-bold-duotone" width={14} />
                      <span>Add channel</span>
                    </Link>
                  </div>
                </div>

                {/* Bottom Details Row: Today, Next post, Content feeding */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-4 border-t border-divider-soft text-xs">
                  {/* Today */}
                  <div className="space-y-1.5">
                    <span className="text-text-secondary block font-medium">Today</span>
                    <span className="font-semibold text-text-primary block">
                      {brandPublished} of {brandPlanned} posts
                    </span>
                    <div
                      aria-hidden="true"
                      className="h-1.5 rounded-full bg-bg-default border border-divider-soft overflow-hidden"
                    >
                      <div
                        style={{ width: `${progressPct}%` }}
                        className="h-full rounded-full bg-primary-main transition-all duration-300"
                      />
                    </div>
                  </div>

                  {/* Next Post */}
                  <div className="space-y-1">
                    <span className="text-text-secondary block font-medium">Next post</span>
                    <span className="font-semibold text-text-primary block">
                      {formatNextPost(b.next_post_at, b.timezone)}
                    </span>
                  </div>

                  {/* Content Feeding */}
                  <div className="space-y-1">
                    <span className="text-text-secondary block font-medium">
                      Content feeding this brand
                    </span>
                    <div className="flex flex-wrap gap-1.5 pt-0.5">
                      {b.content && b.content.filter((m) => m.enabled).length > 0 ? (
                        b.content
                          .filter((m) => m.enabled)
                          .map((m) => (
                            <span
                              key={m.key}
                              className="px-2 py-0.5 rounded-full bg-bg-default border border-divider-soft text-[11px] text-text-secondary"
                            >
                              {m.label}
                              {m.daily_count ? ` (${m.daily_count}/day)` : ""}
                            </span>
                          ))
                      ) : (
                        <span className="text-text-disabled italic text-[11px]">
                          No active content feeds
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}
