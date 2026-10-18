"use client";

import React, { useState, useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import { Icon } from "@iconify/react";
import { listBrands, listReplizAccounts } from "@/lib/socialBrandsApi";
import type { BrandSummaryOut, ChannelSummaryOut, ReplizAccountOut, ReplizAccountsOut } from "@/lib/socialBrandsApi";
import { triggerFanpageSync } from "@/lib/api";
import {
  createBrand,
  AUDIENCE_COUNTRIES,
  CAPTION_LANGUAGES,
  COUNTRY_TIMEZONE_MAP,
  type BrandCreateInput,
} from "@/lib/brandCreateApi";
import { SocialTabs } from "@/components/social-media/SocialTabs";
import { ModeChip } from "@/components/ui/ModeChip";

function getInitials(name: string): string {
  if (!name) return "??";
  const words = name.trim().split(/\s+/);
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

function BrandCardAvatar({
  brand,
  replizAccountMap,
}: {
  brand: BrandSummaryOut;
  replizAccountMap: Map<string, ReplizAccountOut>;
}) {
  const [imgError, setImgError] = useState(false);
  const replizId =
    (brand as { repliz_account_id?: string | null }).repliz_account_id ||
    brand.channels?.find((c) => c.repliz_account_id)?.repliz_account_id;
  const pic = replizId ? replizAccountMap.get(replizId)?.picture : null;

  if (pic && !imgError) {
    return (
      <img
        src={pic}
        alt={brand.name}
        onError={() => setImgError(true)}
        className="w-10 h-10 rounded-xl object-cover shrink-0"
      />
    );
  }

  return (
    <span
      aria-hidden="true"
      className={`w-10 h-10 rounded-xl font-bold text-sm inline-flex items-center justify-center shrink-0 ${getAvatarColorClass(
        brand.id
      )}`}
    >
      {getInitials(brand.name)}
    </span>
  );
}

function ChannelRowAvatar({
  channel,
  replizAccountMap,
}: {
  channel: ChannelSummaryOut;
  replizAccountMap: Map<string, ReplizAccountOut>;
}) {
  const [imgError, setImgError] = useState(false);
  const pic = channel.repliz_account_id ? replizAccountMap.get(channel.repliz_account_id)?.picture : null;

  if (pic && !imgError) {
    return (
      <img
        src={pic}
        alt={channel.name}
        onError={() => setImgError(true)}
        className="w-8 h-8 rounded-lg object-cover shrink-0"
      />
    );
  }

  return (
    <span
      aria-hidden="true"
      className="w-8 h-8 rounded-lg bg-info-soft text-info flex items-center justify-center shrink-0"
    >
      <Icon icon="logos:facebook" width={16} />
    </span>
  );
}

const AVATAR_COLOR_CLASSES = [
  "bg-rose-500/15 text-rose-500 border border-rose-500/30",
  "bg-pri-soft text-pri-text border border-pri/30",
  "bg-purple-500/15 text-purple-400 border border-purple-500/30",
  "bg-teal-500/15 text-teal-400 border border-teal-500/30",
  "bg-warn-soft text-warn border border-warn/30",
];

function getAvatarColorClass(id: number): string {
  return AVATAR_COLOR_CLASSES[Math.abs(id) % AVATAR_COLOR_CLASSES.length];
}

function formatNextPost(isoString: string | null, timezone: string): { main: string; sub: string } {
  if (!isoString) return { main: "—", sub: "" };
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return { main: "—", sub: "" };

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
      return { main: `${localTime} WIB`, sub: "" };
    }
    return { main: `~${localTime} ${city}`, sub: `${wibTime} WIB` };
  } catch {
    return { main: "—", sub: "" };
  }
}

function getErrorMessage(err: unknown): string {
  if (err && typeof err === "object" && "response" in err) {
    const res = (err as { response?: { data?: { detail?: unknown } } }).response;
    if (res?.data?.detail) {
      if (typeof res.data.detail === "string") return res.data.detail;
      if (Array.isArray(res.data.detail)) {
        return res.data.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join(", ");
      }
    }
  }
  if (err instanceof Error) return err.message;
  return "An unexpected error occurred.";
}

const ALL_MODES = [
  { key: "ig", label: "Instagram" },
  { key: "news", label: "News card" },
  { key: "discussion", label: "Discussion" },
  { key: "pinterest", label: "Pinterest" },
  { key: "facebook", label: "FB photo recreate" },
  { key: "youtube", label: "YouTube clips" },
  { key: "aivideo", label: "AI video" },
  { key: "radar", label: "Viral Radar" },
];

interface AttentionItemWithBrand {
  brandId: number;
  brandName: string;
  channelId: number | null;
  channelName: string | null;
  level: string;
  message: string;
  isReview: boolean;
}

export default function BrandsPage() {
  const {
    data: brands = [],
    isLoading,
    error,
    mutate,
  } = useSWR<BrandSummaryOut[]>("social-brands", listBrands, {
    refreshInterval: 60000,
  });

  const { data: replizData } = useSWR<ReplizAccountsOut>(
    "repliz-accounts",
    listReplizAccounts
  );

  const replizAccountMap = useMemo(() => {
    const map = new Map<string, ReplizAccountOut>();
    for (const acc of replizData?.accounts || []) {
      map.set(acc.id, acc);
    }
    return map;
  }, [replizData]);

  const [search, setSearch] = useState("");
  const [platform, setPlatform] = useState("all");
  const [brandFilter, setBrandFilter] = useState<"all" | "active" | "paused">("all");
  const [showAddBrand, setShowAddBrand] = useState(false);
  const [offOpenMap, setOffOpenMap] = useState<Record<number, boolean>>({});

  // Add brand form state
  const [newBrandName, setNewBrandName] = useState("");
  const [newBrandCountry, setNewBrandCountry] = useState<string>("GB");
  const [newBrandTimezone, setNewBrandTimezone] = useState("Europe/London");
  const [newBrandLanguage, setNewBrandLanguage] = useState<string>("en");
  const [newBrandTone, setNewBrandTone] = useState<string>("engaging");
  const [creatingBrand, setCreatingBrand] = useState(false);
  const [createBrandError, setCreateBrandError] = useState<string | null>(null);

  // Sync state
  const [syncingRepliz, setSyncingRepliz] = useState(false);

  async function handleSyncRepliz() {
    setSyncingRepliz(true);
    try {
      await triggerFanpageSync();
      setTimeout(() => mutate(), 3000);
    } catch {
      // ignore
    } finally {
      setSyncingRepliz(false);
    }
  }

  async function handleCreateBrand(e: React.FormEvent) {
    e.preventDefault();
    setCreateBrandError(null);
    if (!newBrandName.trim()) {
      setCreateBrandError("Brand name cannot be blank.");
      return;
    }
    setCreatingBrand(true);
    try {
      const payload: BrandCreateInput = {
        name: newBrandName.trim(),
        timezone: newBrandTimezone.trim() || "Europe/London",
        target_country: newBrandCountry ? newBrandCountry : null,
        caption_language: newBrandLanguage || "en",
        caption_tone: newBrandTone.trim() || undefined,
      };
      await createBrand(payload);
      setNewBrandName("");
      setNewBrandCountry("GB");
      setNewBrandTimezone("Europe/London");
      setNewBrandLanguage("en");
      setNewBrandTone("engaging");
      setShowAddBrand(false);
      mutate();
    } catch (err) {
      setCreateBrandError(getErrorMessage(err));
    } finally {
      setCreatingBrand(false);
    }
  }

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
        const msg = a.message.toLowerCase();
        const isReview = msg.includes("uncertain") || msg.includes("human") || msg.includes("failed");
        attentionItems.push({
          brandId: b.id,
          brandName: b.name,
          channelId: a.channel_id,
          channelName: ch?.name || null,
          level: a.level,
          message: a.message,
          isReview,
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
          const msg = a.message.toLowerCase();
          const isReview = msg.includes("uncertain") || msg.includes("human") || msg.includes("failed");
          attentionItems.push({
            brandId: b.id,
            brandName: b.name,
            channelId: c.id,
            channelName: c.name,
            level: a.level,
            message: a.message,
            isReview,
          });
        }
      }
    }
  }

  const activeBrandsCount = useMemo(() => brands.filter((b) => b.is_active).length, [brands]);
  const pausedBrandsCount = totalBrands - activeBrandsCount;

  // Filtered brands
  const filteredBrands = useMemo(() => {
    return brands.filter((b) => {
      if (brandFilter === "active" && !b.is_active) return false;
      if (brandFilter === "paused" && b.is_active) return false;

      if (platform === "facebook") {
        const hasFb = b.channels?.some(
          (c) =>
            c.kind === "facebook_page" ||
            c.kind === "facebook_profile" ||
            c.kind.toLowerCase().includes("facebook")
        );
        if (!hasFb && (b.channels?.length || 0) > 0) return false;
      }

      if (search.trim()) {
        const q = search.trim().toLowerCase();
        const brandMatch = b.name.toLowerCase().includes(q);
        const channelMatch = b.channels?.some((c) => c.name.toLowerCase().includes(q));
        if (!brandMatch && !channelMatch) return false;
      }
      return true;
    });
  }, [brands, brandFilter, platform, search]);

  return (
    <div className="flex flex-col gap-6 max-w-[1240px] w-full">
      {/* ── Header ── */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1 min-w-0">
          <span className="text-xs font-semibold tracking-wider uppercase text-text2">
            Content &amp; channels
          </span>
          <h1 className="text-2xl sm:text-[26px] font-bold tracking-tight text-text">
            Brands
          </h1>
          <p className="text-sm text-text2 max-w-2xl leading-relaxed">
            Each brand’s content, channels and settings live here. Every post a brand makes goes to all of its active channels, each at its own time.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handleSyncRepliz}
            disabled={syncingRepliz}
            title="Creates or updates a brand for every Repliz Facebook account (new ones start paused) with a Repliz Page channel, marks missing ones Disconnected. Also runs every 6 h."
            className="h-11 px-4 rounded-xl border border-line bg-surface hover:bg-surface2 text-text text-sm font-semibold inline-flex items-center gap-2 transition cursor-pointer disabled:opacity-50"
          >
            <Icon
              icon="solar:refresh-bold-duotone"
              width={16}
              className={syncingRepliz ? "animate-spin text-pri" : ""}
            />
            <span>{syncingRepliz ? "Syncing…" : "Sync from Repliz"}</span>
          </button>

          <button
            type="button"
            onClick={() => {
              setShowAddBrand(!showAddBrand);
              setCreateBrandError(null);
            }}
            className="h-11 px-4 rounded-xl border border-line bg-surface hover:bg-surface2 text-text text-sm font-semibold inline-flex items-center gap-2 transition cursor-pointer"
          >
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            <span>Add brand</span>
          </button>

          <Link
            href="/brands/channels/new"
            className="h-11 px-4 rounded-xl border-0 bg-pri hover:opacity-90 text-pri-fg text-sm font-semibold inline-flex items-center gap-2 transition cursor-pointer"
          >
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            <span>Add channel</span>
          </Link>
        </div>
      </header>

      {/* ── View Tabs ── */}
      <SocialTabs brands={totalBrands} accounts={totalChannels} />

      {/* ── Summary Stat Tiles ── */}
      <section
        aria-label="Today at a glance"
        className="grid grid-cols-2 sm:grid-cols-4 gap-3"
      >
        <div className="bg-surface border border-line rounded-2xl p-4 flex flex-col gap-1 shadow-sm">
          <span className="text-xs text-text2 font-semibold">Brands</span>
          <div className="text-2xl font-bold text-text tabular-nums">{totalBrands}</div>
        </div>

        <div className="bg-surface border border-line rounded-2xl p-4 flex flex-col gap-1 shadow-sm">
          <span className="text-xs text-text2 font-semibold">Channels publishing</span>
          <div className="text-2xl font-bold text-text tabular-nums">
            {activeChannels}{" "}
            <span className="text-xs font-normal text-text2">of {totalChannels}</span>
          </div>
        </div>

        <div className="bg-surface border border-line rounded-2xl p-4 flex flex-col gap-1 shadow-sm">
          <span className="text-xs text-text2 font-semibold">Posts today</span>
          <div className="text-2xl font-bold text-text tabular-nums">
            {sumPublished}{" "}
            <span className="text-xs font-normal text-text2">of {sumPlanned} planned</span>
          </div>
        </div>

        <div
          className={`rounded-2xl p-4 flex flex-col gap-1 shadow-sm border ${
            attentionItems.length > 0
              ? "bg-warn-soft/50 border-warn/40 text-warn"
              : "bg-surface border border-line"
          }`}
        >
          <span className={`text-xs font-semibold ${attentionItems.length > 0 ? "text-warn" : "text-text2"}`}>
            Needs attention
          </span>
          <div className={`text-2xl font-bold tabular-nums ${attentionItems.length > 0 ? "text-warn" : "text-text"}`}>
            {attentionItems.length}
          </div>
        </div>
      </section>

      {/* ── Add Brand Section (Collapsible) ── */}
      {showAddBrand && (
        <section
          aria-labelledby="add-brand-h"
          className="bg-surface border border-line rounded-2xl p-6 flex flex-col gap-4 shadow-sm"
        >
          <div className="flex flex-wrap justify-between items-start gap-2">
            <div className="flex flex-col gap-1 min-w-0">
              <h2 id="add-brand-h" className="text-lg font-bold text-text">
                Add a brand
              </h2>
              <p className="text-xs text-text2 max-w-xl leading-relaxed">
                A brand is what it makes, how it looks and where it posts. After creating it, turn on its content modes in Brand settings and add its channels here.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowAddBrand(false)}
              className="h-9 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold cursor-pointer"
            >
              Cancel
            </button>
          </div>

          {createBrandError && (
            <div className="p-3 rounded-lg border border-bad/30 bg-bad-soft text-bad text-xs font-medium">
              {createBrandError}
            </div>
          )}

          <form onSubmit={handleCreateBrand} className="flex flex-col gap-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
              <label className="flex flex-col gap-1.5 text-xs font-semibold text-text">
                Brand name *
                <input
                  type="text"
                  required
                  placeholder="e.g. Paddock Insider"
                  value={newBrandName}
                  onChange={(e) => setNewBrandName(e.target.value)}
                  className="h-11 px-3 rounded-xl border border-line bg-surface text-text text-sm focus:outline-none focus:ring-2 focus:ring-pri"
                />
              </label>

              <label className="flex flex-col gap-1.5 text-xs font-semibold text-text">
                Audience country
                <select
                  value={newBrandCountry}
                  onChange={(e) => {
                    const val = e.target.value;
                    setNewBrandCountry(val);
                    if (val && COUNTRY_TIMEZONE_MAP[val]) {
                      setNewBrandTimezone(COUNTRY_TIMEZONE_MAP[val]);
                    }
                  }}
                  className="h-11 px-3 rounded-xl border border-line bg-surface text-text text-sm focus:outline-none focus:ring-2 focus:ring-pri"
                >
                  {AUDIENCE_COUNTRIES.map((c) => (
                    <option key={c.code ?? "none"} value={c.code ?? ""}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1.5 text-xs font-semibold text-text">
                Time zone *
                <input
                  type="text"
                  required
                  value={newBrandTimezone}
                  onChange={(e) => setNewBrandTimezone(e.target.value)}
                  placeholder="e.g. Europe/London"
                  className="h-11 px-3 rounded-xl border border-line bg-surface text-text text-sm focus:outline-none focus:ring-2 focus:ring-pri"
                />
              </label>

              <label className="flex flex-col gap-1.5 text-xs font-semibold text-text">
                Caption language
                <select
                  value={newBrandLanguage}
                  onChange={(e) => setNewBrandLanguage(e.target.value)}
                  className="h-11 px-3 rounded-xl border border-line bg-surface text-text text-sm focus:outline-none focus:ring-2 focus:ring-pri"
                >
                  {CAPTION_LANGUAGES.map((lang) => (
                    <option key={lang.code} value={lang.code}>
                      {lang.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1.5 text-xs font-semibold text-text">
                Caption tone
                <input
                  type="text"
                  value={newBrandTone}
                  onChange={(e) => setNewBrandTone(e.target.value)}
                  placeholder="e.g. engaging, informative, humorous"
                  className="h-11 px-3 rounded-xl border border-line bg-surface text-text text-sm focus:outline-none focus:ring-2 focus:ring-pri"
                />
              </label>
            </div>

            <div className="flex flex-wrap justify-between items-center gap-4 pt-2 border-t border-line">
              <span className="text-xs text-text2 max-w-lg leading-relaxed">
                The time zone is used when the brand has no active channel, and in Analytics; each channel keeps its own time zone. A brand makes content only when it is active and has its own connected Repliz account or an active phone or split channel.
              </span>
              <button
                type="submit"
                disabled={creatingBrand}
                className="h-11 px-5 rounded-xl border-0 bg-pri hover:opacity-90 text-pri-fg text-sm font-semibold cursor-pointer disabled:opacity-50"
              >
                {creatingBrand ? "Creating…" : "Create and set up"}
              </button>
            </div>
          </form>
        </section>
      )}

      {/* ── Needs Attention Section ── */}
      <section
        aria-labelledby="attn-h"
        className="bg-surface border border-line rounded-2xl p-4 flex flex-col gap-3 shadow-sm"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="attn-h" className="text-base font-bold text-text flex items-center gap-2">
            <span
              aria-hidden="true"
              className="w-7 h-7 rounded-lg bg-warn-soft text-warn inline-flex items-center justify-center shrink-0"
            >
              <Icon icon="solar:danger-triangle-bold-duotone" width={16} />
            </span>
            <span>Needs attention</span>
            <span className="min-w-6 h-6 px-2 rounded-full bg-surface2 text-text text-xs font-bold inline-flex items-center justify-center tabular-nums">
              {attentionItems.length}
            </span>
          </h2>
          <span className="text-xs text-text2">
            Active channels of every brand · paused ones are not listed
          </span>
        </div>

        {attentionItems.length > 0 ? (
          <ul className="m-0 p-0 list-none flex flex-col gap-2">
            {attentionItems.map((item, idx) => (
              <li
                key={`${item.brandId}-${item.channelId}-${idx}`}
                className="p-3 rounded-xl bg-surface2 flex flex-wrap items-center gap-3"
              >
                <span
                  aria-hidden="true"
                  className="w-9 h-9 rounded-xl bg-info-soft text-info flex items-center justify-center shrink-0"
                >
                  <Icon icon="logos:facebook" width={18} />
                </span>

                <div className="flex-1 min-w-[240px] flex flex-col gap-0.5">
                  <div className="text-sm font-semibold text-text leading-tight">
                    <strong>{item.channelName || item.brandName}</strong>
                    <span className="text-text2 font-normal"> · {item.brandName}</span>
                  </div>
                  <span className="text-xs font-semibold text-warn flex items-center gap-1.5 leading-snug">
                    <Icon icon="solar:danger-triangle-bold" width={13} className="shrink-0" />
                    <span>{item.message}</span>
                  </span>
                </div>

                <div className="shrink-0">
                  {item.isReview ? (
                    <Link
                      href="/history"
                      className="h-9 px-3.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold inline-flex items-center gap-1.5 transition"
                    >
                      <span>Review in Activity</span>
                      <Icon icon="solar:alt-arrow-right-bold" width={13} />
                    </Link>
                  ) : item.channelId ? (
                    <Link
                      href={`/brands/channels/${item.channelId}`}
                      className="h-9 px-3.5 rounded-lg border-0 bg-pri hover:opacity-90 text-pri-fg text-xs font-semibold inline-flex items-center gap-1.5 transition"
                    >
                      <span>Fix</span>
                      <Icon icon="solar:alt-arrow-right-bold" width={13} />
                    </Link>
                  ) : (
                    <Link
                      href={`/brands/${item.brandId}`}
                      className="h-9 px-3.5 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold inline-flex items-center gap-1.5 transition"
                    >
                      <span>Configure brand</span>
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-text2 m-0 py-1">
            Nothing needs you right now: every active channel is posting on plan.
          </p>
        )}
      </section>

      {/* ── Filter & Search Bar ── */}
      <div className="flex flex-wrap gap-2 justify-between items-center">
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="tablist"
            aria-label="Filter brands"
            className="flex gap-1 p-1 rounded-xl bg-surface2 border border-line"
          >
            <button
              type="button"
              role="tab"
              aria-selected={brandFilter === "all"}
              onClick={() => setBrandFilter("all")}
              className={`h-9 px-3 rounded-[9px] text-xs font-semibold cursor-pointer inline-flex items-center gap-1.5 transition ${
                brandFilter === "all"
                  ? "bg-surface text-text shadow-sm"
                  : "text-text2 hover:text-text"
              }`}
            >
              <span>All</span>
              <span className="text-[11px] tabular-nums font-semibold text-text2">
                {totalBrands}
              </span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={brandFilter === "active"}
              onClick={() => setBrandFilter("active")}
              className={`h-9 px-3 rounded-[9px] text-xs font-semibold cursor-pointer inline-flex items-center gap-1.5 transition ${
                brandFilter === "active"
                  ? "bg-surface text-text shadow-sm"
                  : "text-text2 hover:text-text"
              }`}
            >
              <span>Active</span>
              <span className="text-[11px] tabular-nums font-semibold text-text2">
                {activeBrandsCount}
              </span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={brandFilter === "paused"}
              onClick={() => setBrandFilter("paused")}
              className={`h-9 px-3 rounded-[9px] text-xs font-semibold cursor-pointer inline-flex items-center gap-1.5 transition ${
                brandFilter === "paused"
                  ? "bg-surface text-text shadow-sm"
                  : "text-text2 hover:text-text"
              }`}
            >
              <span>Paused</span>
              <span className="text-[11px] tabular-nums font-semibold text-text2">
                {pausedBrandsCount}
              </span>
            </button>
          </div>

          <div className="flex items-center gap-1.5">
            <label htmlFor="platform-filter" className="sr-only">
              Platform:
            </label>
            <select
              id="platform-filter"
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              className="h-11 px-3 rounded-xl border border-line bg-surface text-text text-xs focus:outline-none focus:ring-2 focus:ring-pri"
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
        </div>

        <div className="relative flex-1 max-w-xs min-w-[200px]">
          <Icon
            icon="solar:magnifer-linear"
            width={16}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-text2 pointer-events-none"
          />
          <input
            type="search"
            aria-label="Search brands"
            placeholder="Search brands"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-11 pl-9 pr-3 rounded-xl border border-line bg-surface text-text text-sm focus:outline-none focus:ring-2 focus:ring-pri"
          />
        </div>
      </div>

      {/* ── Loading Skeleton ── */}
      {isLoading && (
        <div className="flex flex-col gap-4 animate-pulse">
          {[1, 2].map((i) => (
            <div key={i} className="bg-surface border border-line rounded-2xl p-5 h-48" />
          ))}
        </div>
      )}

      {/* ── Error State ── */}
      {error && !isLoading && (
        <div className="bg-surface border border-bad/30 rounded-2xl p-8 text-center flex flex-col items-center gap-3">
          <Icon icon="solar:danger-triangle-bold-duotone" width={36} className="text-bad" />
          <p className="text-sm font-semibold text-text">Could not load brands.</p>
          <p className="text-xs text-text2 max-w-md">
            Failed to communicate with the brands API. Check your connection or retry.
          </p>
          <button
            type="button"
            onClick={() => mutate()}
            className="h-9 px-4 rounded-xl border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold inline-flex items-center gap-1.5"
          >
            <Icon icon="solar:refresh-bold" width={14} />
            <span>Retry</span>
          </button>
        </div>
      )}

      {/* ── Empty State ── */}
      {!isLoading && !error && filteredBrands.length === 0 && (
        <div className="bg-surface border border-line rounded-2xl p-12 text-center flex flex-col items-center gap-3">
          <Icon icon="solar:folder-with-files-bold-duotone" width={40} className="text-text2" />
          <p className="text-base font-semibold text-text">
            {brands.length === 0 ? "No brands yet." : "No brands match your filters."}
          </p>
          <p className="text-xs text-text2 max-w-sm">
            {brands.length === 0
              ? "Create your first brand to start publishing content to Facebook and other channels."
              : "Try adjusting your search query or status filter."}
          </p>
          {brands.length === 0 && (
            <button
              type="button"
              onClick={() => setShowAddBrand(true)}
              className="mt-2 h-10 px-4 rounded-xl border-0 bg-pri text-pri-fg text-xs font-semibold inline-flex items-center gap-2"
            >
              <Icon icon="solar:add-circle-bold-duotone" width={16} />
              <span>Add brand</span>
            </button>
          )}
        </div>
      )}

      {/* ── Brand Cards List ── */}
      {!isLoading && !error && filteredBrands.length > 0 && (
        <section aria-label="Brands list" className="flex flex-col gap-4">
          {filteredBrands.map((b) => {
            const activeChs = b.channels?.filter((c) => c.is_active) || [];
            const pausedChs = (b.channels?.length || 0) - activeChs.length;
            const hasPhoneChannel = activeChs.some((c) => c.mode === "phone" || c.mode === "mixed");
            const hasReplizChannel = activeChs.some((c) => Boolean(c.repliz_account_id) || c.mode === "repliz" || c.mode === "mixed");
            const hasReplizLinked = b.channels?.some((c) => Boolean(c.repliz_account_id) || c.mode === "repliz" || c.mode === "mixed");
            const isMakingContent = b.is_active && (hasReplizChannel || hasPhoneChannel);

            const makingWhy = !b.is_active
              ? "The brand is paused."
              : hasReplizChannel
              ? "Active, and a Repliz channel is connected."
              : hasPhoneChannel
              ? "Active, and a phone channel is on."
              : "It has no connected Repliz account and no phone channel that is on.";

            const publishedToday = b.today?.published || 0;
            const plannedToday = activeChs.reduce((sum, c) => sum + (c.daily_target || 0), 0);
            const progressPct = plannedToday > 0 ? Math.min(100, Math.round((publishedToday / plannedToday) * 100)) : 0;

            const nextPostInfo = formatNextPost(b.next_post_at, b.timezone);

            // Enabled vs disabled modes
            const enabledModeKeys = new Set(b.content?.filter((m) => m.enabled).map((m) => m.key) || []);
            const modesOn = ALL_MODES.filter((m) => enabledModeKeys.has(m.key));
            const modesOff = ALL_MODES.filter((m) => !enabledModeKeys.has(m.key));
            const isOffExpanded = Boolean(offOpenMap[b.id]);

            return (
              <article
                key={b.id}
                aria-label={b.name}
                className="bg-surface border border-line rounded-2xl p-4 sm:p-5 flex flex-col gap-4 shadow-sm"
              >
                {/* Brand Header Row */}
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex items-start gap-3 min-w-0 flex-1">
                    <BrandCardAvatar brand={b} replizAccountMap={replizAccountMap} />
                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-base sm:text-lg font-bold text-text truncate">
                          {b.name}
                        </h2>
                        <span
                          className={`h-6 px-2.5 rounded-full inline-flex items-center gap-1.5 text-xs font-semibold ${
                            b.is_active
                              ? "bg-pri-soft text-pri-text"
                              : "bg-surface2 text-text2 border border-line"
                          }`}
                        >
                          <span
                            aria-hidden="true"
                            className="w-1.5 h-1.5 rounded-full bg-current shrink-0"
                          />
                          <span>{b.is_active ? "Active" : "Paused"}</span>
                        </span>
                      </div>

                      <div className="text-xs text-text2 flex flex-wrap items-center gap-2">
                        <span>{b.timezone}</span>
                        <span>·</span>
                        <span>
                          {hasReplizLinked ? "Repliz linked" : "No Repliz account · phone only"}
                        </span>
                      </div>

                      <div className="flex flex-wrap items-center gap-2 text-xs pt-0.5">
                        <span
                          className={`h-6 px-2.5 rounded-full inline-flex items-center gap-1.5 font-semibold text-xs ${
                            isMakingContent
                              ? "bg-pri-soft text-pri-text"
                              : "bg-bad-soft text-bad"
                          }`}
                        >
                          <Icon
                            icon={isMakingContent ? "solar:check-circle-bold" : "solar:danger-triangle-bold"}
                            width={13}
                          />
                          <span>{isMakingContent ? "Making content" : "Not making content"}</span>
                        </span>
                        <span className="text-text2">{makingWhy}</span>
                      </div>
                    </div>
                  </div>

                  <Link
                    href={`/brands/${b.id}`}
                    className="h-9 px-3 rounded-xl border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold inline-flex items-center gap-1.5 transition shrink-0"
                  >
                    <span>Brand settings</span>
                    <Icon icon="solar:alt-arrow-right-bold" width={13} />
                  </Link>
                </div>

                {/* Content Modes ("Makes") */}
                <div className="flex flex-col gap-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs font-semibold text-text2 mr-1">Makes</span>
                    {modesOn.length > 0 ? (
                      modesOn.map((md) => (
                        <ModeChip key={md.key} mode={md.key} size="sm" />
                      ))
                    ) : (
                      <span className="text-xs text-text2 italic">None turned on</span>
                    )}

                    {modesOff.length > 0 && (
                      <button
                        type="button"
                        onClick={() =>
                          setOffOpenMap((prev) => ({
                            ...prev,
                            [b.id]: !prev[b.id],
                          }))
                        }
                        className="h-6 px-2 rounded-lg bg-surface2 hover:bg-line text-text2 text-xs font-semibold inline-flex items-center gap-1 transition cursor-pointer"
                        title={`${isOffExpanded ? "Hide" : "Show"} the ${modesOff.length} modes that are off`}
                      >
                        <span>+{modesOff.length} off</span>
                        <Icon
                          icon={isOffExpanded ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"}
                          width={12}
                        />
                      </button>
                    )}
                  </div>

                  {/* Off modes expansion */}
                  {isOffExpanded && (
                    <div className="p-2.5 rounded-xl bg-surface2 flex flex-wrap items-center gap-2 text-xs">
                      <span className="font-semibold text-text2">Off:</span>
                      <div className="flex flex-wrap gap-1.5">
                        {modesOff.map((mo) => (
                          <span
                            key={mo.key}
                            className="h-6 px-2 rounded-lg bg-surface border border-line text-text2 inline-flex items-center gap-1 text-xs"
                          >
                            <span>{mo.label}</span>
                          </span>
                        ))}
                      </div>
                      <Link
                        href={`/brands/${b.id}`}
                        className="text-xs font-semibold text-pri hover:underline ml-auto"
                      >
                        Turn on in Brand settings →
                      </Link>
                    </div>
                  )}
                </div>

                {/* 3-Column Summary Box */}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 p-3.5 rounded-xl bg-surface2 text-xs">
                  {/* Today */}
                  <div className="flex flex-col gap-1.5 min-w-0">
                    <div className="flex justify-between items-baseline gap-2">
                      <span className="text-text2 font-semibold">Today</span>
                      <span className="font-bold text-text tabular-nums">
                        {publishedToday} of {plannedToday} published
                      </span>
                    </div>
                    <div
                      aria-hidden="true"
                      className="h-2 rounded-full bg-line overflow-hidden w-full"
                    >
                      <div
                        style={{ width: `${progressPct}%` }}
                        className="h-full rounded-full bg-pri transition-all"
                      />
                    </div>
                    <span className="text-[11px] text-text2">
                      Summed over {activeChs.length} active channel{activeChs.length === 1 ? "" : "s"}
                    </span>
                  </div>

                  {/* Next Post */}
                  <div className="flex flex-col gap-1 min-w-0">
                    <span className="text-text2 font-semibold">Next post</span>
                    <span className="text-sm font-semibold text-text">
                      {nextPostInfo.main}
                    </span>
                    {nextPostInfo.sub && (
                      <span className="text-[11px] text-text2">{nextPostInfo.sub}</span>
                    )}
                  </div>

                  {/* Details */}
                  <div className="flex flex-col gap-1 min-w-0 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-text2">Review mode</span>
                      <span className="font-semibold text-text capitalize">
                        {b.publish_mode === "auto" ? "Auto-publish" : "Manual review"}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-text2">Quiet hours</span>
                      <span className="font-semibold text-text">
                        {b.publish_sleep_start_hour !== null && b.publish_sleep_end_hour !== null
                          ? `${String(b.publish_sleep_start_hour).padStart(2, "0")}:00–${String(
                              b.publish_sleep_end_hour
                            ).padStart(2, "0")}:00`
                          : "None · 07:00–22:00"}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-text2">Channels</span>
                      <span className="font-semibold text-text">
                        {b.channels?.length || 0}
                        {pausedChs > 0 ? ` · ${pausedChs} paused` : ""}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Connected Channels List */}
                <div className="flex flex-col gap-2">
                  <span className="text-xs font-semibold text-text2">Channels</span>
                  <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2.5">
                    {b.channels && b.channels.length > 0 ? (
                      b.channels.map((c) => {
                        const isChannelActive = c.is_active;
                        const hasAttn =
                          c.attention &&
                          c.attention.some((a) => a.level === "error" || a.level === "warn");
                        const methodLabel =
                          c.mode === "mixed"
                            ? `Split ${c.phone_daily_target || ""}+${c.repliz_daily_target || ""}`
                            : c.mode === "phone"
                            ? "Phone"
                            : "Repliz";

                        const channelProgress =
                          c.daily_target > 0
                            ? Math.min(100, Math.round(((c.today?.published || 0) / c.daily_target) * 100))
                            : 0;

                        return (
                          <Link
                            key={c.id}
                            href={`/brands/channels/${c.id}`}
                            className="p-3 rounded-xl border border-line bg-surface hover:border-pri hover:shadow-sm transition flex flex-col gap-2.5 text-left text-text"
                          >
                            <div className="flex items-center gap-2.5 w-full min-w-0">
                              <ChannelRowAvatar
                                channel={c}
                                replizAccountMap={replizAccountMap}
                              />
                              <div className="flex flex-col min-w-0 flex-1">
                                <span className="text-sm font-semibold truncate leading-tight">
                                  {c.name}
                                </span>
                                <span className="text-[11px] text-text2 truncate leading-tight">
                                  {c.kind === "facebook_page" ? "Facebook Page" : "Facebook Profile"}
                                </span>
                              </div>
                              <Icon
                                icon="solar:alt-arrow-right-linear"
                                width={14}
                                className="text-text2 shrink-0"
                              />
                            </div>

                            <div className="flex flex-wrap items-center gap-2 text-xs">
                              <span
                                className={`h-6 px-2 rounded-md font-semibold text-[11px] inline-flex items-center gap-1 ${
                                  c.mode === "mixed"
                                    ? "bg-surface2 text-text border border-line"
                                    : c.mode === "phone"
                                    ? "bg-info-soft text-info"
                                    : "bg-pri-soft text-pri-text"
                                }`}
                              >
                                {methodLabel}
                              </span>

                              <span
                                className={`h-6 px-2 rounded-md font-semibold text-[11px] inline-flex items-center gap-1 ${
                                  !isChannelActive
                                    ? "bg-surface2 text-text2 border border-line"
                                    : hasAttn
                                    ? "bg-warn-soft text-warn"
                                    : "bg-pri-soft text-pri-text"
                                }`}
                              >
                                {!isChannelActive ? "Paused" : hasAttn ? "Needs attention" : "Live"}
                              </span>

                              <div className="ml-auto inline-flex items-center gap-1.5 text-[11px] font-semibold tabular-nums text-text">
                                <span>
                                  {c.today?.published || 0} / {c.daily_target}
                                </span>
                                <span
                                  aria-hidden="true"
                                  className="w-10 h-1.5 rounded-full bg-line overflow-hidden"
                                >
                                  <span
                                    style={{ width: `${channelProgress}%` }}
                                    className="block h-full bg-pri"
                                  />
                                </span>
                              </div>
                            </div>
                          </Link>
                        );
                      })
                    ) : (
                      <span className="text-xs text-text2 italic py-1">
                        No channels connected yet.
                      </span>
                    )}

                    <Link
                      href={`/brands/channels/new?brand=${b.id}`}
                      className="p-3 rounded-xl border border-dashed border-line hover:border-pri text-text2 hover:text-text transition flex items-center justify-center gap-1.5 text-xs font-semibold"
                    >
                      <Icon icon="solar:add-circle-bold" width={14} />
                      <span>Add channel</span>
                    </Link>
                  </div>
                </div>
              </article>
            );
          })}
        </section>
      )}

      {/* ── Footer Information ── */}
      <footer className="flex flex-col gap-1 text-xs text-text2 leading-relaxed pt-2">
        <p className="m-0">
          Posts via: Phone = a phone signed in as the Facebook Profile posts it · Repliz = Repliz schedules and posts it · Split p+r = p posts a day by phone and r by Repliz.
        </p>
        <p className="m-0">
          Making content = the brand is active and has its own connected Repliz account or an active phone or split channel. Today = published / planned, summed over the brand&apos;s active channels.
        </p>
      </footer>
    </div>
  );
}
