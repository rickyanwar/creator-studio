"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import { getSettings, updateSettings, testReplizCredentials } from "@/lib/api";
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
        gemini_api_key: "",
        groq_api_key: "",
        repliz_access_key: "",
        repliz_secret_key: "",
        telegram_bot_token: "",
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

      {/* Crawl */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">Crawler</h2>
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
