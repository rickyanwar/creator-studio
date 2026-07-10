"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { getSettings, updateSettings, testReplizCredentials, testProxies } from "@/lib/api";
import type { AppSettings } from "@/lib/types";
import { Icon } from "@iconify/react";

const fetcher = () => getSettings().then((r) => r.data as AppSettings);

export default function SettingsPage() {
  const { data: settings, mutate } = useSWR("settings", fetcher);
  const [form, setForm] = useState<Record<string, string | number>>({});
  const [saved, setSaved]               = useState(false);
  const [loadingSave, setLoadingSave]   = useState(false);
  const [loadingTest, setLoadingTest]   = useState(false);
  const [replizResult, setReplizResult] = useState<{ ok?: boolean; message?: string } | null>(null);
  const [proxyTesting, setProxyTesting] = useState(false);
  const [proxyResults, setProxyResults] = useState<{ results: { proxy: string; ok: boolean; ip?: string; ms?: number; error?: string }[]; alive: number; total: number } | null>(null);

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
        nine_router_base_url: settings.nine_router_base_url ?? "",
        nine_router_model: settings.nine_router_model ?? "",
        nine_router_api_key: "",
        gemini_api_key: "",
        groq_api_key: "",
        repliz_access_key: "",
        repliz_secret_key: "",
        telegram_bot_token: "",
        flashapi_api_key: "",
      });
    }
  }, [settings]);

  function set(key: string, value: string | number) {
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
      // Proxy pool must be sendable even when emptied (to clear it)
      payload.scraper_proxies = form.scraper_proxies ?? "";
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
          How often the crawler runs and how it fetches posts. <strong>Auto</strong> tries your burner accounts first and falls back to FlashAPI when all are unavailable.
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
            {(["auto", "instagrapi", "flashapi"] as const).map((mode) => (
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
                {mode === "flashapi" && "FlashAPI"}
              </button>
            ))}
          </div>
          <p className="text-xs text-text-secondary mt-2">
            {form.scraper_mode === "auto" && "Tries burner accounts first; falls back to FlashAPI if none are available."}
            {form.scraper_mode === "instagrapi" && "Always uses burner accounts. Stops crawling if all burners are rate-limited."}
            {form.scraper_mode === "flashapi" && "Always uses FlashAPI. No burner accounts needed. Requires an API key below."}
          </p>
        </div>

        <div>
          <label className="label">
            FlashAPI Key{" "}
            {settings?.has_flashapi_key && <span className="text-primary-main">✓ saved</span>}
          </label>
          <input
            className="input-rect"
            type="password"
            placeholder="Leave blank to keep existing"
            value={form.flashapi_api_key as string ?? ""}
            onChange={(e) => set("flashapi_api_key", e.target.value)}
          />
          <p className="text-xs text-text-secondary mt-1">
            Required when mode is <strong>FlashAPI</strong> or as auto-fallback. Get a key at flashapi.ru.
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

      <button onClick={handleSave} disabled={loadingSave} className="btn-primary">
        <Icon icon="solar:refresh-bold-duotone" width={14} className={loadingSave ? "animate-spin" : "hidden"} />
        {saved ? "Saved!" : loadingSave ? "Saving…" : "Save Settings"}
      </button>
    </div>
  );
}
