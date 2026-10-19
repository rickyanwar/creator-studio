"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Icon } from "@iconify/react";
import {
  getSettings,
  updateSettings,
  testReplizCredentials,
  testProxies,
  testRelays,
} from "@/lib/api";
import { YouTubeSettingsCard } from "@/components/settings/YouTubeSettingsCard";
import { HermesTokensCard } from "@/components/settings/HermesTokensCard";
import { AiVideoSettingsCard } from "@/components/settings/AiVideoSettingsCard";
import { GalleryLabelsSettingsCard } from "@/components/settings/GalleryLabelsSettingsCard";
import { AiImageEngineSettingsCard } from "@/components/settings/AiImageEngineSettingsCard";
import { ProPosterSettingsCard } from "@/components/settings/ProPosterSettingsCard";
import { F1DriversSettings } from "@/components/settings/F1DriversSettings";
import { DevicesSettingsCard } from "@/components/settings/DevicesSettingsCard";
import type { AppSettings } from "@/lib/types";

const fetcher = () => getSettings().then((r) => r.data as AppSettings);

type TabKey = "conn" | "ai" | "image" | "scrapers" | "engines" | "storage" | "moved";

interface NavItem {
  id: TabKey;
  label: string;
  flag?: string;
  title: string;
  desc: string;
}

const TABS: NavItem[] = [
  {
    id: "conn",
    label: "Connections",
    title: "Connections",
    desc: "Accounts Studio talks to. Keys are never shown again after saving.",
  },
  {
    id: "ai",
    label: "AI models",
    title: "AI models",
    desc: "All AI goes through 9Router. Only models listed on its /v1/models can be picked.",
  },
  {
    id: "image",
    label: "AI image engine",
    title: "AI image engine",
    desc: "ChatGPT first, Codex as a share and backup, the template last.",
  },
  {
    id: "scrapers",
    label: "Scrapers",
    title: "Scrapers",
    desc: "How Studio collects Instagram posts, news and YouTube videos.",
  },
  {
    id: "engines",
    label: "Radar and video",
    title: "Viral radar and AI video",
    desc: "Engine settings shared by every brand that turns these modes on.",
  },
  {
    id: "storage",
    label: "Storage and labels",
    title: "Storage and gallery labels",
    desc: "Where media is kept and how gallery photos get labeled.",
  },
  {
    id: "moved",
    label: "Moved out",
    flag: "1",
    title: "Moved to a better place",
    desc: "These were in Global settings but belong elsewhere.",
  },
];

export default function SettingsPage() {
  const { data: settings, mutate } = useSWR("settings", fetcher);
  const [activeTab, setActiveTab] = useState<TabKey>("conn");
  const [form, setForm] = useState<Record<string, string | number | boolean | null>>({});
  const [saved, setSaved] = useState(false);
  const [loadingSave, setLoadingSave] = useState(false);
  const [loadingTest, setLoadingTest] = useState(false);
  const [replizResult, setReplizResult] = useState<{ ok?: boolean; message?: string } | null>(null);
  const [proxyTesting, setProxyTesting] = useState(false);
  const [proxyResults, setProxyResults] = useState<{
    results: { proxy: string; ok: boolean; ip?: string; ms?: number; error?: string }[];
    alive: number;
    total: number;
  } | null>(null);
  const [relayTesting, setRelayTesting] = useState(false);
  const [relayResults, setRelayResults] = useState<{
    results: { proxy: string; ok: boolean; ip?: string; ms?: number; error?: string }[];
    alive: number;
    total: number;
  } | null>(null);

  useEffect(() => {
    if (settings) {
      setForm({
        crawl_interval_minutes: settings.crawl_interval_minutes,
        max_post_age_days: settings.max_post_age_days ?? 2,
        ai_provider_primary: settings.ai_provider_primary,
        ai_provider_fallback: settings.ai_provider_fallback,
        storage_base_url: settings.storage_base_url ?? "",
        storage_base_path: settings.storage_base_path ?? "",
        ai_fallback_after_failures: settings.ai_fallback_after_failures,
        ai_fallback_reset_after_minutes: settings.ai_fallback_reset_after_minutes,
        telegram_chat_id: settings.telegram_chat_id ?? "",
        scraper_mode: settings.scraper_mode ?? "auto",
        scraper_proxies: settings.scraper_proxies ?? "",
        scraper_relays: settings.scraper_relays ?? "",
        nine_router_base_url: settings.nine_router_base_url ?? "",
        nine_router_model: settings.nine_router_model ?? "",
        nine_router_discussion_model: settings.nine_router_discussion_model ?? "",

        radar_sleep_start_wib: settings.radar_sleep_start_wib ?? "",
        radar_sleep_end_wib: settings.radar_sleep_end_wib ?? "",
        radar_very_hot_interval_min: settings.radar_very_hot_interval_min,
        radar_hot_interval_min: settings.radar_hot_interval_min,
        radar_cold_interval_min: settings.radar_cold_interval_min,
        radar_hot_window_h: settings.radar_hot_window_h,
        radar_track_max_age_h: settings.radar_track_max_age_h,
        radar_story_sharing: settings.radar_story_sharing ?? "shared",
        radar_share_max: settings.radar_share_max,
        radar_news_stagger_max_min: settings.radar_news_stagger_max_min,

        nine_router_api_key: "",
        gemini_api_key: "",
        groq_api_key: "",
        repliz_access_key: "",
        repliz_secret_key: "",
        telegram_bot_token: "",
        metrics_ingestion_enabled: settings.metrics_ingestion_enabled ?? false,
      });

      if (settings.scraper_mode === "flashapi") {
        setForm((prev) => ({ ...prev, scraper_mode: "viewer" }));
      }
    }
  }, [settings]);

  function set(key: string, value: string | number | boolean | null) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    setLoadingSave(true);
    try {
      const payload: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(form)) {
        if (typeof v === "string" && v === "") continue;
        payload[k] = v;
      }
      payload.scraper_proxies = form.scraper_proxies ?? "";
      payload.scraper_relays = form.scraper_relays ?? "";
      await updateSettings(payload);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
      mutate();
    } finally {
      setLoadingSave(false);
    }
  }

  async function handleTestRepliz() {
    if (!form.repliz_access_key || !form.repliz_secret_key) {
      setReplizResult({ message: "Enter Access Key and Secret Key first" });
      return;
    }
    setLoadingTest(true);
    try {
      const res = await testReplizCredentials(
        form.repliz_access_key as string,
        form.repliz_secret_key as string
      );
      setReplizResult({ ok: true, message: `Connected — ${res.data.fanpages_found} fanpages found` });
    } catch {
      setReplizResult({ ok: false, message: "Connection failed — check credentials" });
    } finally {
      setLoadingTest(false);
    }
  }

  async function handleTestProxies() {
    setProxyTesting(true);
    setProxyResults(null);
    try {
      const res = await testProxies((form.scraper_proxies as string) ?? "");
      setProxyResults(res.data);
    } catch {
      setProxyResults({ results: [], alive: 0, total: 0 });
    } finally {
      setProxyTesting(false);
    }
  }

  async function handleTestRelays() {
    setRelayTesting(true);
    setRelayResults(null);
    try {
      const res = await testRelays((form.scraper_relays as string) ?? "");
      setRelayResults(res.data);
    } catch {
      setRelayResults({ results: [], alive: 0, total: 0 });
    } finally {
      setRelayTesting(false);
    }
  }

  const currentTab = TABS.find((t) => t.id === activeTab) || TABS[0];

  const SaveButton = () => (
    <div className="flex items-center gap-3 pt-2">
      <button
        type="button"
        onClick={handleSave}
        disabled={loadingSave}
        className="h-9 px-4 rounded-[10px] bg-pri text-pri-fg font-semibold text-sm flex items-center gap-2 hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        {loadingSave ? (
          <Icon icon="svg-spinners:ring-resize" width={14} />
        ) : saved ? (
          <Icon icon="solar:check-circle-bold" width={16} />
        ) : (
          <Icon icon="solar:diskette-bold" width={16} />
        )}
        {saved ? "Saved!" : loadingSave ? "Saving…" : "Save changes"}
      </button>
      {saved && <span className="text-xs text-pri-text font-medium">Settings updated.</span>}
    </div>
  );

  return (
    <div className="space-y-6 max-w-[1240px] pb-24">
      {/* ── Page Header ── */}
      <div className="space-y-1">
        <h1 className="text-[26px] font-bold text-text tracking-tight">Settings</h1>
        <p className="text-sm text-text2">
          App-wide settings, grouped by what they connect or control. Each brand&apos;s own settings are in Brand settings.
        </p>
      </div>

      <div className="flex flex-col md:flex-row gap-5 items-start">
        {/* ── Section Nav ── */}
        <nav
          aria-label="Settings sections"
          className="w-full md:w-[230px] shrink-0 bg-surface border border-line rounded-[14px] p-2.5 flex flex-row md:flex-col gap-1 overflow-x-auto"
        >
          {TABS.map((t) => {
            const active = activeTab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => setActiveTab(t.id)}
                aria-current={active ? "page" : undefined}
                className={`flex items-center justify-between gap-2 min-h-[40px] px-3 rounded-[9px] text-sm text-left whitespace-nowrap transition-colors ${
                  active
                    ? "bg-pri-soft text-pri-text font-semibold"
                    : "text-text hover:bg-surface2 font-medium"
                }`}
              >
                <span>{t.label}</span>
                {t.flag && (
                  <span className="px-1.5 py-0.5 rounded-full text-[11px] font-bold bg-warn-soft text-warn">
                    {t.flag}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        {/* ── Pane Content ── */}
        <div className="flex-1 min-w-0 w-full space-y-4">
          <div className="space-y-0.5">
            <h2 className="text-[19px] font-bold text-text">{currentTab.title}</h2>
            <p className="text-sm text-text2">{currentTab.desc}</p>
          </div>

          {/* ────────────────── PANE: CONNECTIONS ────────────────── */}
          {activeTab === "conn" && (
            <div className="space-y-4">
              {/* Repliz API Card */}
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">Repliz</h3>
                  <div className="flex items-center gap-2">
                    {settings?.has_repliz_keys ? (
                      <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                        Connected
                      </span>
                    ) : (
                      <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-surface2 text-text2 border border-line">
                        Not configured
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={handleTestRepliz}
                      disabled={loadingTest}
                      className="h-[34px] px-3 rounded-[9px] border border-line bg-surface text-text text-[13px] hover:bg-surface2 transition-colors disabled:opacity-50"
                    >
                      {loadingTest ? "Testing…" : "Test connection"}
                    </button>
                  </div>
                </div>

                <p className="text-[13px] leading-relaxed text-text2">
                  Facebook Pages · synced every 6 h (Sync from Repliz on Home or Brands).
                </p>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Access Key
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="password"
                      placeholder="Leave blank to keep existing"
                      value={(form.repliz_access_key as string) ?? ""}
                      onChange={(e) => set("repliz_access_key", e.target.value)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Secret Key
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="password"
                      placeholder="Leave blank to keep existing"
                      value={(form.repliz_secret_key as string) ?? ""}
                      onChange={(e) => set("repliz_secret_key", e.target.value)}
                    />
                  </label>
                </div>

                {replizResult && (
                  <p className={`text-xs ${replizResult.ok ? "text-pri-text" : "text-bad"}`}>
                    {replizResult.message}
                  </p>
                )}
              </section>

              {/* Post Metrics Card */}
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">Post metrics (Repliz Gold)</h3>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                    {settings?.metrics_plan_status ? `Plan ${settings.metrics_plan_status}` : "Checked"}
                  </span>
                </div>

                <label className="flex items-center gap-3 text-sm text-text cursor-pointer">
                  <input
                    type="checkbox"
                    className="w-4 h-4 accent-pri rounded"
                    checked={(form.metrics_ingestion_enabled as boolean) ?? false}
                    onChange={(e) => set("metrics_ingestion_enabled", e.target.checked)}
                  />
                  <span>Pull likes, comments and shares every 5 min, plus an hourly backfill</span>
                </label>

                <div className="text-xs text-text2 space-y-0.5 pt-1 border-t border-line">
                  <p>
                    Last Checked:{" "}
                    {settings?.metrics_plan_checked_at
                      ? new Date(settings.metrics_plan_checked_at + "Z").toLocaleString()
                      : "Never"}
                  </p>
                  {settings?.metrics_last_error && (
                    <p className="text-bad">Last Error: {settings.metrics_last_error}</p>
                  )}
                </div>
              </section>

              {/* Telegram Alerts Card */}
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">Telegram alerts</h3>
                  {settings?.telegram_chat_id ? (
                    <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                      Configured
                    </span>
                  ) : (
                    <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-surface2 text-text2 border border-line">
                      Optional
                    </span>
                  )}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Bot Token
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="password"
                      placeholder="Leave blank to keep existing"
                      value={(form.telegram_bot_token as string) ?? ""}
                      onChange={(e) => set("telegram_bot_token", e.target.value)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Chat ID
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      placeholder="-100 4821 5530"
                      value={(form.telegram_chat_id as string) ?? ""}
                      onChange={(e) => set("telegram_chat_id", e.target.value)}
                    />
                  </label>
                </div>
              </section>

              {/* Hermes API Tokens */}
              <HermesTokensCard />

              <SaveButton />
            </div>
          )}

          {/* ────────────────── PANE: AI MODELS ────────────────── */}
          {activeTab === "ai" && (
            <div className="space-y-4">
              {/* 9Router Card */}
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">9Router (Primary AI)</h3>
                  {settings?.has_nine_router_key ? (
                    <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                      Reachable
                    </span>
                  ) : (
                    <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-surface2 text-text2 border border-line">
                      Not configured
                    </span>
                  )}
                </div>

                <p className="text-[13px] leading-relaxed text-text2">
                  Captions &amp; news copy route through 9Router first, then fall back to Gemini→Groq. Overrides NINE_ROUTER_* env vars.
                </p>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text sm:col-span-2">
                    Base URL
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      placeholder="http://your-9router-host:20128/v1"
                      value={(form.nine_router_base_url as string) ?? ""}
                      onChange={(e) => set("nine_router_base_url", e.target.value)}
                    />
                    <span className="text-[11px] font-normal text-text2">Must end with /v1</span>
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Main Model
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      placeholder="ag/claude-sonnet-4-6 (or combo)"
                      value={(form.nine_router_model as string) ?? ""}
                      onChange={(e) => set("nine_router_model", e.target.value)}
                    />
                    <span className="text-[11px] font-normal text-text2">Pick a model from /v1/models</span>
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Discussion Model
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      placeholder="smart-combo"
                      value={(form.nine_router_discussion_model as string) ?? ""}
                      onChange={(e) => set("nine_router_discussion_model", e.target.value)}
                    />
                    <span className="text-[11px] font-normal text-text2">Used only for discussion/hot-take cards</span>
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text sm:col-span-2">
                    API Key / Token
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="password"
                      placeholder="Leave blank to keep existing"
                      value={(form.nine_router_api_key as string) ?? ""}
                      onChange={(e) => set("nine_router_api_key", e.target.value)}
                    />
                  </label>
                </div>
              </section>

              {/* Backup Providers Card */}
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">Backup providers</h3>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                    Ready
                  </span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Gemini API Key
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="password"
                      placeholder="Leave blank to keep existing"
                      value={(form.gemini_api_key as string) ?? ""}
                      onChange={(e) => set("gemini_api_key", e.target.value)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Groq API Key
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="password"
                      placeholder="Leave blank to keep existing"
                      value={(form.groq_api_key as string) ?? ""}
                      onChange={(e) => set("groq_api_key", e.target.value)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Switch after N failures
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="number"
                      value={(form.ai_fallback_after_failures as number) ?? 3}
                      onChange={(e) => set("ai_fallback_after_failures", parseInt(e.target.value) || 3)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Retry Gemini after (min)
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="number"
                      value={(form.ai_fallback_reset_after_minutes as number) ?? 15}
                      onChange={(e) => set("ai_fallback_reset_after_minutes", parseInt(e.target.value) || 15)}
                    />
                  </label>
                </div>
              </section>

              <SaveButton />
            </div>
          )}

          {/* ────────────────── PANE: AI IMAGE ENGINE ────────────────── */}
          {activeTab === "image" && (
            <div className="space-y-4">
              <AiImageEngineSettingsCard settings={settings} onChanged={() => mutate()} />
              <ProPosterSettingsCard settings={settings} onChanged={() => mutate()} />
              <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
                <span className="text-xs text-text2">
                  Changes to AI image engine settings are saved automatically.
                </span>
                <SaveButton />
              </div>
            </div>
          )}

          {/* ────────────────── PANE: SCRAPERS ────────────────── */}
          {activeTab === "scrapers" && (
            <div className="space-y-4">
              {/* Instagram Scraper */}
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">Instagram</h3>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                    Running
                  </span>
                </div>

                <p className="text-[13px] leading-relaxed text-text2">
                  Crawling pauses 01:00–06:00 WIB. This pause is fixed and cannot be changed here. Each pass checks every Instagram account once.
                </p>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Crawl every (minutes)
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="number"
                      value={(form.crawl_interval_minutes as number) ?? 30}
                      onChange={(e) => set("crawl_interval_minutes", parseInt(e.target.value) || 30)}
                    />
                    <span className="text-[11px] font-normal text-text2">Minimum 15 minutes recommended</span>
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Ignore posts older than (days)
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      type="number"
                      min={1}
                      max={30}
                      value={(form.max_post_age_days as number) ?? 2}
                      onChange={(e) => set("max_post_age_days", parseInt(e.target.value) || 2)}
                    />
                    <span className="text-[11px] font-normal text-text2">e.g. 1 = today only, 2 = past 48 h</span>
                  </label>
                </div>
              </section>

              {/* News Proxies and Relays */}
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">News proxies and relays</h3>
                  {proxyResults ? (
                    <span
                      className={`px-2.5 py-0.5 rounded-full text-xs font-semibold ${
                        proxyResults.alive > 0
                          ? "bg-pri-soft text-pri-text"
                          : "bg-bad-soft text-bad"
                      }`}
                    >
                      {proxyResults.alive} / {proxyResults.total} alive
                    </span>
                  ) : typeof settings?.scraper_proxy_count === "number" && settings.scraper_proxy_count > 0 ? (
                    <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                      {settings.scraper_proxy_count} proxies configured
                    </span>
                  ) : null}
                </div>

                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[13px] font-semibold text-text">Proxy Pool</span>
                    <button
                      type="button"
                      onClick={handleTestProxies}
                      disabled={proxyTesting}
                      className="h-[30px] px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs hover:bg-surface2 transition-colors disabled:opacity-50"
                    >
                      {proxyTesting ? "Testing…" : "Test proxies"}
                    </button>
                  </div>
                  <textarea
                    className="w-full min-h-[100px] p-2.5 rounded-[10px] border border-line bg-surface text-text text-xs font-mono"
                    rows={4}
                    spellCheck={false}
                    placeholder={"http://user:pass@host:port\nOne proxy per line"}
                    value={(form.scraper_proxies as string) ?? ""}
                    onChange={(e) => set("scraper_proxies", e.target.value)}
                  />
                  <p className="text-[11px] text-text2 mt-1">
                    The news scraper picks one at random per request (and rotates on block/timeout).
                  </p>
                </div>

                <div className="pt-3 border-t border-line">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[13px] font-semibold text-text">Relay Pool</span>
                    <button
                      type="button"
                      onClick={handleTestRelays}
                      disabled={relayTesting}
                      className="h-[30px] px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs hover:bg-surface2 transition-colors disabled:opacity-50"
                    >
                      {relayTesting ? "Testing…" : "Test relays"}
                    </button>
                  </div>
                  <textarea
                    className="w-full min-h-[70px] p-2.5 rounded-[10px] border border-line bg-surface text-text text-xs font-mono"
                    rows={2}
                    spellCheck={false}
                    placeholder={"https://vercel-relay-xxx.vercel.app\nOne relay URL per line"}
                    value={(form.scraper_relays as string) ?? ""}
                    onChange={(e) => set("scraper_relays", e.target.value)}
                  />
                  <p className="text-[11px] text-text2 mt-1">
                    Fallback fetch path tried after the proxy pool.
                  </p>
                </div>
              </section>

              {/* YouTube Card */}
              <YouTubeSettingsCard settings={settings} onChanged={() => mutate()} />

              <SaveButton />
            </div>
          )}

          {/* ────────────────── PANE: ENGINES (RADAR & VIDEO) ────────────────── */}
          {activeTab === "engines" && (
            <div className="space-y-4">
              {/* Viral Radar Engine */}
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">Viral radar engine</h3>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                    Running
                  </span>
                </div>

                <p className="text-[13px] leading-relaxed text-text2">
                  Per-brand Radar rules (niches, shadow mode, minimum likes, ratios, max per day) live in each brand&apos;s settings.
                </p>

                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3.5">
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Quiet hours start (WIB)
                    <input
                      type="number"
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      placeholder="e.g. 0"
                      value={(form.radar_sleep_start_wib as number | string) ?? ""}
                      onChange={(e) =>
                        set("radar_sleep_start_wib", e.target.value !== "" ? parseInt(e.target.value) : null)
                      }
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Quiet hours end (WIB)
                    <input
                      type="number"
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      placeholder="e.g. 6"
                      value={(form.radar_sleep_end_wib as number | string) ?? ""}
                      onChange={(e) =>
                        set("radar_sleep_end_wib", e.target.value !== "" ? parseInt(e.target.value) : null)
                      }
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Very hot interval (min)
                    <input
                      type="number"
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.radar_very_hot_interval_min as number) ?? 8}
                      onChange={(e) => set("radar_very_hot_interval_min", parseInt(e.target.value) || 8)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Hot interval (min)
                    <input
                      type="number"
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.radar_hot_interval_min as number) ?? 12}
                      onChange={(e) => set("radar_hot_interval_min", parseInt(e.target.value) || 12)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Cold interval (min)
                    <input
                      type="number"
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.radar_cold_interval_min as number) ?? 50}
                      onChange={(e) => set("radar_cold_interval_min", parseInt(e.target.value) || 50)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Hot window (h)
                    <input
                      type="number"
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.radar_hot_window_h as number) ?? 2}
                      onChange={(e) => set("radar_hot_window_h", parseInt(e.target.value) || 2)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Track max age (h)
                    <input
                      type="number"
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.radar_track_max_age_h as number) ?? 48}
                      onChange={(e) => set("radar_track_max_age_h", parseInt(e.target.value) || 48)}
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Story sharing
                    <select
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.radar_story_sharing as string) ?? "shared"}
                      onChange={(e) => set("radar_story_sharing", e.target.value)}
                    >
                      <option value="shared">Shared (multiple brands)</option>
                      <option value="exclusive">Exclusive (one brand only)</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Share max fanpages
                    <input
                      type="number"
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.radar_share_max as number) ?? 0}
                      onChange={(e) => set("radar_share_max", parseInt(e.target.value) || 0)}
                    />
                    <span className="text-[11px] font-normal text-text2">0 = no limit</span>
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    News stagger max (min)
                    <input
                      type="number"
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.radar_news_stagger_max_min as number) ?? 10}
                      onChange={(e) => set("radar_news_stagger_max_min", parseInt(e.target.value) || 10)}
                    />
                  </label>
                </div>
              </section>

              {/* AI Video (GFlow) Card */}
              <AiVideoSettingsCard settings={settings} onChanged={() => mutate()} />

              <SaveButton />
            </div>
          )}

          {/* ────────────────── PANE: STORAGE AND LABELS ────────────────── */}
          {activeTab === "storage" && (
            <div className="space-y-4">
              {/* Media Storage Card */}
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3.5">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">Media storage</h3>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                    Active
                  </span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    VPS Storage Path
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.storage_base_path as string) ?? ""}
                      onChange={(e) => set("storage_base_path", e.target.value)}
                      placeholder="/opt/studio/media"
                    />
                  </label>
                  <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-text">
                    Public Base URL (HTTPS)
                    <input
                      className="h-10 px-3 rounded-[10px] border border-line bg-surface text-text text-sm font-normal"
                      value={(form.storage_base_url as string) ?? ""}
                      onChange={(e) => set("storage_base_url", e.target.value)}
                      placeholder="https://autostudio.pro/media"
                    />
                  </label>
                </div>
              </section>

              {/* Gallery Labels Card */}
              <GalleryLabelsSettingsCard settings={settings} onChanged={() => mutate()} />

              {/* F1 Drivers Card */}
              <F1DriversSettings />

              <SaveButton />
            </div>
          )}

          {/* ────────────────── PANE: MOVED OUT ────────────────── */}
          {activeTab === "moved" && (
            <div className="space-y-4">
              <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3">
                <div className="flex flex-wrap items-center justify-between gap-2.5">
                  <h3 className="text-base font-semibold text-text">Phones and Tailscale</h3>
                  <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-surface2 text-text2 border border-line">
                    Now in Brands › Accounts &amp; phones
                  </span>
                </div>
                <p className="text-[13px] leading-relaxed text-text2">
                  Phones, Facebook Profile channels and Repliz accounts are managed on Brands › Accounts &amp; phones.
                </p>
                <div>
                  <Link
                    href="/brands/accounts"
                    className="inline-flex items-center gap-1.5 text-xs font-semibold text-pri-text hover:underline"
                  >
                    <span>Open Accounts &amp; phones</span>
                    <Icon icon="solar:arrow-right-linear" width={14} />
                  </Link>
                </div>
              </section>

              {/* Full DevicesSettingsCard preserved below for device operations */}
              <DevicesSettingsCard />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
