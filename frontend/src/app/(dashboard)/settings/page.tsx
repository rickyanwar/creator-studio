"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { getSettings, updateSettings, testReplizCredentials, testProxies, testRelays } from "@/lib/api";
import { YouTubeSettingsCard } from "@/components/settings/YouTubeSettingsCard";
import { F1DriversSettings } from "@/components/settings/F1DriversSettings";
import type { AppSettings } from "@/lib/types";
import { Icon } from "@iconify/react";

const fetcher = () => getSettings().then((r) => r.data as AppSettings);

export default function SettingsPage() {
  const { data: settings, mutate } = useSWR("settings", fetcher);
  const [form, setForm] = useState<Record<string, string | number | null>>({});
  const [saved, setSaved]               = useState(false);
  const [loadingSave, setLoadingSave]   = useState(false);
  const [loadingTest, setLoadingTest]   = useState(false);
  const [replizResult, setReplizResult] = useState<{ ok?: boolean; message?: string } | null>(null);
  const [proxyTesting, setProxyTesting] = useState(false);
  const [proxyResults, setProxyResults] = useState<{ results: { proxy: string; ok: boolean; ip?: string; ms?: number; error?: string }[]; alive: number; total: number } | null>(null);
  const [relayTesting, setRelayTesting] = useState(false);
  const [relayResults, setRelayResults] = useState<{ results: { proxy: string; ok: boolean; ip?: string; ms?: number; error?: string }[]; alive: number; total: number } | null>(null);

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
      });

      if (settings.scraper_mode === "flashapi") {
        setForm((prev) => ({ ...prev, scraper_mode: "viewer" }));
      }
    }
  }, [settings]);

  function set(key: string, value: string | number | null) {
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
      // Proxy/relay pools must be sendable even when emptied (to clear them)
      payload.scraper_proxies = form.scraper_proxies ?? "";
      payload.scraper_relays = form.scraper_relays ?? "";
      await updateSettings(payload);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      mutate();
    } finally { setLoadingSave(false); }
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
    } finally { setLoadingTest(false); }
  }

  async function handleTestProxies() {
    setProxyTesting(true);
    setProxyResults(null);
    try {
      const res = await testProxies((form.scraper_proxies as string) ?? "");
      setProxyResults(res.data);
    } catch {
      setProxyResults({ results: [], alive: 0, total: 0 });
    } finally { setProxyTesting(false); }
  }

  async function handleTestRelays() {
    setRelayTesting(true);
    setRelayResults(null);
    try {
      const res = await testRelays((form.scraper_relays as string) ?? "");
      setRelayResults(res.data);
    } catch {
      setRelayResults({ results: [], alive: 0, total: 0 });
    } finally { setRelayTesting(false); }
  }

  return (
    <div className="max-w-2xl space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-text-primary">Settings</h1>
        <p className="text-sm text-text-secondary mt-1">Global configuration for the reposter</p>
      </div>

      {/* Repliz credentials */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">Repliz API</h2>
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          {settings?.has_repliz_keys ? (
            <>
              <Icon icon="solar:check-circle-bold-duotone" width={14} className="text-primary-main" />
              Keys saved
            </>
          ) : (
            <>
              <Icon icon="solar:close-circle-bold-duotone" width={14} className="text-error-main" />
              Not configured
            </>
          )}
        </div>
        <div>
          <label className="label">Access Key</label>
          <input className="input-rect" type="password" placeholder="Leave blank to keep existing"
            value={form.repliz_access_key as string ?? ""} onChange={(e) => set("repliz_access_key", e.target.value)} />
        </div>
        <div>
          <label className="label">Secret Key</label>
          <input className="input-rect" type="password" placeholder="Leave blank to keep existing"
            value={form.repliz_secret_key as string ?? ""} onChange={(e) => set("repliz_secret_key", e.target.value)} />
        </div>
        <div className="flex items-center gap-3">
          <button onClick={handleTestRepliz} disabled={loadingTest} className="btn-ghost">
            <Icon icon="solar:refresh-bold-duotone" width={14} className={loadingTest ? "animate-spin" : "hidden"} />
            {loadingTest ? "Testing…" : "Test Connection"}
          </button>
          {replizResult && (
            <span className={`text-xs ${replizResult.ok ? "text-primary-main" : "text-error-main"}`}>
              {replizResult.message}
            </span>
          )}
        </div>
      </section>

      {/* AI providers */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">AI Providers</h2>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">
              Gemini API Key{" "}
              {settings?.has_gemini_key && <span className="text-primary-main">✓ saved</span>}
            </label>
            <input className="input-rect" type="password" placeholder="Leave blank to keep existing"
              value={form.gemini_api_key as string ?? ""} onChange={(e) => set("gemini_api_key", e.target.value)} />
          </div>
          <div>
            <label className="label">
              Groq API Key{" "}
              {settings?.has_groq_key && <span className="text-primary-main">✓ saved</span>}
            </label>
            <input className="input-rect" type="password" placeholder="Leave blank to keep existing"
              value={form.groq_api_key as string ?? ""} onChange={(e) => set("groq_api_key", e.target.value)} />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Failover after N failures</label>
            <input className="input-rect" type="number"
              value={form.ai_fallback_after_failures as number} onChange={(e) => set("ai_fallback_after_failures", parseInt(e.target.value))} />
          </div>
          <div>
            <label className="label">Reset Gemini after (min)</label>
            <input className="input-rect" type="number"
              value={form.ai_fallback_reset_after_minutes as number} onChange={(e) => set("ai_fallback_reset_after_minutes", parseInt(e.target.value))} />
          </div>
        </div>
      </section>

      {/* Storage */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">Media Storage</h2>
        <div>
          <label className="label">VPS Storage Path</label>
          <input className="input-rect" value={form.storage_base_path as string ?? ""}
            onChange={(e) => set("storage_base_path", e.target.value)} placeholder="/var/www/media" />
        </div>
        <div>
          <label className="label">Public Base URL (HTTPS)</label>
          <input className="input-rect" value={form.storage_base_url as string ?? ""}
            onChange={(e) => set("storage_base_url", e.target.value)} placeholder="https://cdn.yourdomain.com/media" />
        </div>
      </section>

      {/* Instagram Scraper (crawl schedule + fetch mode) */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">Instagram Scraper</h2>
        <p className="text-xs text-text-secondary">
          How often the crawler runs and how it fetches posts. <strong>Auto</strong> tries your burner accounts first and falls back to the Web Viewer scraper (GramSnap / AnonyIG / IGStoryViewer) when no burner is available.
        </p>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Crawl Interval (minutes)</label>
            <input className="input-rect" type="number"
              value={form.crawl_interval_minutes as number} onChange={(e) => set("crawl_interval_minutes", parseInt(e.target.value))} />
            <p className="text-xs text-text-secondary mt-1">Minimum 15 minutes recommended</p>
          </div>
          <div>
            <label className="label">Max Post Age (days)</label>
            <input className="input-rect" type="number" min={1} max={30}
              value={form.max_post_age_days as number} onChange={(e) => set("max_post_age_days", parseInt(e.target.value))} />
            <p className="text-xs text-text-secondary mt-1">Skip posts older than this — e.g. 1 = today only</p>
          </div>
        </div>

        <div>
          <label className="label">Scraper Mode</label>
          <div className="flex gap-3 mt-1">
            {(["auto", "instagrapi", "viewer"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => set("scraper_mode", mode)}
                className={`px-4 py-2 rounded-lg text-sm font-medium border transition-colors ${
                  form.scraper_mode === mode
                    ? "bg-primary-main text-white border-primary-main"
                    : "bg-surface-secondary text-text-secondary border-border-default hover:border-primary-main"
                }`}
              >
                {mode === "auto" && "Auto"}
                {mode === "instagrapi" && "Burner Accounts"}
                {mode === "viewer" && "Web Viewer (no login)"}
              </button>
            ))}
          </div>
          <p className="text-xs text-text-secondary mt-2">
            {form.scraper_mode === "auto" && "Tries burner accounts first; falls back to Web Viewer if none are available."}
            {form.scraper_mode === "instagrapi" && "Always uses burner accounts. Stops crawling if all burners are rate-limited."}
            {form.scraper_mode === "viewer" && "Always uses Web Viewer (GramSnap / AnonyIG / IGStoryViewer). No login or burner accounts needed."}
          </p>
        </div>
      </section>

      {/* News Scraper */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">News Scraper</h2>
        <div>
          <label className="label">
            Proxy Pool{" "}
            {typeof settings?.scraper_proxy_count === "number" && settings.scraper_proxy_count > 0 && (
              <span className="text-primary-main">✓ {settings.scraper_proxy_count} proxies</span>
            )}
          </label>
          <textarea
            className="input-rect font-mono text-xs"
            rows={6}
            spellCheck={false}
            placeholder={"One proxy per line, e.g.\nhttp://user:pass@host:port\nhttp://user:pass@1.2.3.4:8000"}
            value={form.scraper_proxies as string ?? ""}
            onChange={(e) => set("scraper_proxies", e.target.value)}
          />
          <p className="text-xs text-text-secondary mt-1">
            The news scraper picks one at random per request (and rotates on block/timeout). One proxy per
            line — a trailing label after the URL is ignored. Leave empty to scrape directly (no proxy).
          </p>

          <div className="mt-2 flex items-center gap-3">
            <button
              type="button"
              onClick={handleTestProxies}
              disabled={proxyTesting}
              className="btn btn-secondary text-xs"
            >
              {proxyTesting ? "Testing…" : "Test Proxies"}
            </button>
            {proxyResults && (
              <span className={`text-xs font-semibold ${proxyResults.alive > 0 ? "text-primary-main" : "text-red-500"}`}>
                {proxyResults.alive}/{proxyResults.total} alive
              </span>
            )}
          </div>

          {proxyResults && proxyResults.results.length > 0 && (
            <div className="mt-2 max-h-52 overflow-y-auto rounded-lg border border-hairline divide-y divide-hairline text-xs font-mono">
              {proxyResults.results.map((r) => (
                <div key={r.proxy} className="flex items-center justify-between px-3 py-1.5">
                  <span className="text-text-primary">{r.proxy}</span>
                  {r.ok ? (
                    <span className="text-emerald-600">✓ {r.ip} · {r.ms}ms</span>
                  ) : (
                    <span className="text-red-500 truncate max-w-[55%]" title={r.error}>✗ {r.error}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="pt-2 border-t border-hairline">
          <label className="label">
            Relay Pool{" "}
            {typeof settings?.scraper_relay_count === "number" && settings.scraper_relay_count > 0 && (
              <span className="text-primary-main">✓ {settings.scraper_relay_count} relays</span>
            )}
          </label>
          <textarea
            className="input-rect font-mono text-xs"
            rows={3}
            spellCheck={false}
            placeholder={"One relay base URL per line, e.g.\nhttps://vercel-relay-xxx.vercel.app"}
            value={form.scraper_relays as string ?? ""}
            onChange={(e) => set("scraper_relays", e.target.value)}
          />
          <p className="text-xs text-text-secondary mt-1">
            Fallback fetch path tried after the proxy pool — a small server-side relay (e.g. deployed via
            9Router&apos;s &quot;Deploy Relay&quot;) that fetches a URL and returns its raw response. Egresses
            from the relay platform&apos;s own IP (e.g. Vercel&apos;s datacenter range) — clears sites that
            block on request fingerprint, not sites that specifically block datacenter IPs. One URL per line.
          </p>

          <div className="mt-2 flex items-center gap-3">
            <button
              type="button"
              onClick={handleTestRelays}
              disabled={relayTesting}
              className="btn btn-secondary text-xs"
            >
              {relayTesting ? "Testing…" : "Test Relays"}
            </button>
            {relayResults && (
              <span className={`text-xs font-semibold ${relayResults.alive > 0 ? "text-primary-main" : "text-red-500"}`}>
                {relayResults.alive}/{relayResults.total} alive
              </span>
            )}
          </div>

          {relayResults && relayResults.results.length > 0 && (
            <div className="mt-2 max-h-52 overflow-y-auto rounded-lg border border-hairline divide-y divide-hairline text-xs font-mono">
              {relayResults.results.map((r) => (
                <div key={r.proxy} className="flex items-center justify-between px-3 py-1.5">
                  <span className="text-text-primary">{r.proxy}</span>
                  {r.ok ? (
                    <span className="text-emerald-600">✓ {r.ip} · {r.ms}ms</span>
                  ) : (
                    <span className="text-red-500 truncate max-w-[55%]" title={r.error}>✗ {r.error}</span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      {/* 9Router (primary AI) */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">9Router — Primary AI (OpenAI-compatible)</h2>
        <p className="text-xs text-text-secondary -mt-2">
          Captions &amp; news copy route through 9Router first, then fall back to Gemini→Groq. Overrides the
          NINE_ROUTER_* env vars. Leave Base URL blank to disable and use Gemini directly.
        </p>
        <div>
          <label className="label">Base URL</label>
          <input
            className="input-rect"
            placeholder="http://your-9router-host:20128/v1"
            value={form.nine_router_base_url as string ?? ""}
            onChange={(e) => set("nine_router_base_url", e.target.value)}
          />
          <p className="text-xs text-text-secondary mt-1">Must end with <code>/v1</code>.</p>
        </div>
        <div>
          <label className="label">Model</label>
          <input
            className="input-rect"
            placeholder="ag/claude-sonnet-4-6 (or a combo name)"
            value={form.nine_router_model as string ?? ""}
            onChange={(e) => set("nine_router_model", e.target.value)}
          />
          <p className="text-xs text-text-secondary mt-1">A model id from your dashboard, or a combo name. Not <code>auto</code>.</p>
        </div>
        <div>
          <label className="label">Discussion / Hot Take Model</label>
          <input
            className="input-rect"
            placeholder="smart-combo"
            value={form.nine_router_discussion_model as string ?? ""}
            onChange={(e) => set("nine_router_discussion_model", e.target.value)}
          />
          <p className="text-xs text-text-secondary mt-1">
            Used only for Mode 4 discussion/hot-take copy — everything else keeps using the Model above.
            Leave blank to use the built-in default (<code>smart-combo</code>); swap it here if that combo
            starts misbehaving.
          </p>
        </div>
        <div>
          <label className="label">
            API Key / Token{" "}
            {settings?.has_nine_router_key && <span className="text-primary-main">✓ saved</span>}
          </label>
          <input
            className="input-rect"
            type="password"
            placeholder="Leave blank to keep existing"
            value={form.nine_router_api_key as string ?? ""}
            onChange={(e) => set("nine_router_api_key", e.target.value)}
          />
          <p className="text-xs text-text-secondary mt-1">Copy from the 9Router dashboard.</p>
        </div>
      </section>

      {/* Radar Settings */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">Viral Radar Engine</h2>
        <p className="text-xs text-text-secondary -mt-2">
          Global polling limits and scraping behavior for Viral Radar (Feature 3).
        </p>
        
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">Sleep Window Start (Hour, WIB)</label>
            <input
              type="number"
              className="input-rect"
              placeholder="e.g. 0"
              value={form.radar_sleep_start_wib as number | string ?? ""}
              onChange={(e) => set("radar_sleep_start_wib", e.target.value !== "" ? parseInt(e.target.value) : null)}
            />
          </div>
          <div>
            <label className="label">Sleep Window End (Hour, WIB)</label>
            <input
              type="number"
              className="input-rect"
              placeholder="e.g. 6"
              value={form.radar_sleep_end_wib as number | string ?? ""}
              onChange={(e) => set("radar_sleep_end_wib", e.target.value !== "" ? parseInt(e.target.value) : null)}
            />
          </div>
        </div>
        
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="label">Very Hot Interval (min)</label>
            <input
              type="number"
              className="input-rect"
              value={form.radar_very_hot_interval_min as number ?? 8}
              onChange={(e) => set("radar_very_hot_interval_min", parseInt(e.target.value) || 8)}
            />
          </div>
          <div>
            <label className="label">Hot Interval (min)</label>
            <input
              type="number"
              className="input-rect"
              value={form.radar_hot_interval_min as number ?? 12}
              onChange={(e) => set("radar_hot_interval_min", parseInt(e.target.value) || 12)}
            />
          </div>
          <div>
            <label className="label">Cold Interval (min)</label>
            <input
              type="number"
              className="input-rect"
              value={form.radar_cold_interval_min as number ?? 50}
              onChange={(e) => set("radar_cold_interval_min", parseInt(e.target.value) || 50)}
            />
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">Hot Window (h)</label>
            <input
              type="number"
              className="input-rect"
              value={form.radar_hot_window_h as number ?? 2}
              onChange={(e) => set("radar_hot_window_h", parseInt(e.target.value) || 2)}
            />
            <p className="text-[10px] text-text-secondary mt-1">Posts newer than this get hot/very-hot intervals.</p>
          </div>
          <div>
            <label className="label">Track Max Age (h)</label>
            <input
              type="number"
              className="input-rect"
              value={form.radar_track_max_age_h as number ?? 48}
              onChange={(e) => set("radar_track_max_age_h", parseInt(e.target.value) || 48)}
            />
            <p className="text-[10px] text-text-secondary mt-1">Stop tracking likes after this age.</p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div>
            <label className="label">Sharing Mode</label>
            <select
              className="input w-full"
              value={form.radar_story_sharing as string ?? "shared"}
              onChange={(e) => set("radar_story_sharing", e.target.value)}
            >
              <option value="shared">Shared</option>
              <option value="exclusive">Exclusive</option>
            </select>
          </div>
          <div>
            <label className="label">Share Max Fanpages</label>
            <input
              type="number"
              className="input-rect"
              value={form.radar_share_max as number ?? 0}
              onChange={(e) => set("radar_share_max", parseInt(e.target.value) || 0)}
            />
            <p className="text-[10px] text-text-secondary mt-1">0 = infinite</p>
          </div>
          <div>
            <label className="label">News Stagger Max (min)</label>
            <input
              type="number"
              className="input-rect"
              value={form.radar_news_stagger_max_min as number ?? 10}
              onChange={(e) => set("radar_news_stagger_max_min", parseInt(e.target.value) || 10)}
            />
          </div>
        </div>
      </section>

      <YouTubeSettingsCard settings={settings} onChanged={() => mutate()} />

      {/* Telegram */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">Telegram Notifications (optional)</h2>
        <div>
          <label className="label">Bot Token</label>
          <input className="input-rect" type="password" placeholder="Leave blank to keep existing"
            value={form.telegram_bot_token as string ?? ""} onChange={(e) => set("telegram_bot_token", e.target.value)} />
        </div>
        <div>
          <label className="label">Chat ID</label>
          <input className="input-rect" value={form.telegram_chat_id as string ?? ""}
            onChange={(e) => set("telegram_chat_id", e.target.value)} placeholder="-1001234567890" />
        </div>
      </section>

      <button onClick={handleSave} disabled={loadingSave} className="btn-primary mb-6">
        <Icon icon="solar:refresh-bold-duotone" width={14} className={loadingSave ? "animate-spin" : "hidden"} />
        {saved ? "Saved!" : loadingSave ? "Saving…" : "Save Settings"}
      </button>

      <F1DriversSettings />
    </div>
  );
}
