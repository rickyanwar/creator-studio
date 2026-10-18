"use client";

import { useEffect, useState } from "react";
import { Icon } from "@iconify/react";
import { updateSettings, testYouTubeAccess, clearYouTubeBlock } from "@/lib/api";
import type { AppSettings } from "@/lib/types";

type Props = {
  settings: AppSettings | undefined;
  onChanged: () => void;
};

type TestResult = { ok: boolean; title?: string; ms?: number; error?: string };

function errorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

/** Mode 7: YouTube access. The VPS works without cookies or a proxy today;
 * both are the fallback for when YouTube starts blocking it. Saves only its
 * own fields — cookies are uploaded as a file and never shown again. */
export function YouTubeSettingsCard({ settings, onChanged }: Props) {
  const [proxy, setProxy] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);

  useEffect(() => {
    setProxy(settings?.youtube_proxy ?? "");
  }, [settings?.youtube_proxy]);

  const blockedUntil = settings?.youtube_blocked_until ? new Date(`${settings.youtube_blocked_until}Z`) : null;
  const isBlocked = !!blockedUntil && blockedUntil.getTime() > Date.now();

  async function run(action: () => Promise<unknown>, ok: string, fallback: string) {
    setBusy(true);
    setMessage(null);
    try {
      await action();
      setMessage(ok);
      onChanged();
    } catch (err) {
      setMessage(errorMessage(err, fallback));
    } finally {
      setBusy(false);
    }
  }

  async function uploadCookies(file: File | undefined) {
    if (!file) return;
    const text = await file.text();
    await run(() => updateSettings({ youtube_cookies: text }), "Cookies saved (encrypted).", "Couldn't save the cookies.");
  }

  async function runTest() {
    setBusy(true);
    setTest(null);
    try {
      const res = await testYouTubeAccess();
      setTest(res.data as TestResult);
      onChanged();
    } catch (err) {
      setTest({ ok: false, error: errorMessage(err, "Test failed.") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="bg-surface border border-line rounded-[14px] p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-text">YouTube clips</h2>
        <p className="text-xs text-text2 mt-0.5">
          The server reaches YouTube without cookies or a proxy today. If YouTube starts blocking it (&quot;sign in to
          confirm you&apos;re not a bot&quot;), all YouTube work pauses for an hour and the reason shows here. Then add
          the cookies of a <strong>spare</strong> Google account — never your personal one — and optionally one
          sticky residential proxy.
        </p>
      </div>

      {isBlocked && (
        <div className="p-3 rounded-lg bg-bad-soft text-sm text-bad space-y-1 border border-bad/30">
          <p className="font-semibold">Paused until {blockedUntil?.toLocaleString()}</p>
          {settings?.youtube_last_error && <p className="text-xs break-words">{settings.youtube_last_error}</p>}
          <button
            onClick={() => run(clearYouTubeBlock, "Resumed.", "Couldn't resume.")}
            disabled={busy}
            className="text-xs font-semibold underline"
          >
            Resume now
          </button>
        </div>
      )}

      <div>
        <label className="block text-xs font-semibold text-text mb-1">
          Cookies (cookies.txt) {settings?.has_youtube_cookies && <span className="text-pri-text">✓ saved</span>}
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <label className="h-8 px-3 rounded-[8px] border border-line bg-surface text-text hover:bg-surface2 text-xs font-medium cursor-pointer inline-flex items-center gap-1.5 transition-colors">
            <Icon icon="solar:upload-bold-duotone" width={14} />
            Upload cookies.txt
            <input type="file" accept=".txt,text/plain" className="hidden" disabled={busy} onChange={(e) => uploadCookies(e.target.files?.[0])} />
          </label>
          {settings?.has_youtube_cookies && (
            <button
              onClick={() => run(() => updateSettings({ youtube_cookies: "" }), "Cookies removed.", "Couldn't remove the cookies.")}
              disabled={busy}
              className="text-xs text-bad hover:underline font-semibold"
            >
              Remove
            </button>
          )}
        </div>
        <p className="text-xs text-text2 mt-1">
          Export from youtube.com while signed in to the spare account (a &quot;Get cookies.txt&quot; browser extension).
          Stored encrypted; never shown again.
        </p>
      </div>

      <div>
        <label className="block text-xs font-semibold text-text mb-1">Proxy (optional)</label>
        <div className="flex gap-2">
          <input
            className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm flex-1"
            value={proxy}
            onChange={(e) => setProxy(e.target.value)}
            placeholder="http://user:pass@host:port"
          />
          <button
            onClick={() => run(() => updateSettings({ youtube_proxy: proxy }), "Proxy saved.", "Couldn't save the proxy.")}
            disabled={busy}
            className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text hover:bg-surface2 text-xs font-medium transition-colors"
          >
            Save
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 pt-1">
        <button
          onClick={runTest}
          disabled={busy}
          className="h-8 px-3 rounded-[8px] bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 flex items-center gap-1.5 transition-opacity disabled:opacity-50"
        >
          <Icon icon="solar:play-circle-bold-duotone" width={14} />
          {busy ? "Working…" : "Test YouTube access"}
        </button>
        {test && (
          <span className={`text-xs ${test.ok ? "text-pri-text font-semibold" : "text-bad"}`}>
            {test.ok ? `OK — ${test.title} (${test.ms} ms)` : `Failed: ${test.error}`}
          </span>
        )}
        {message && <span className="text-xs text-text2">{message}</span>}
      </div>
    </section>
  );
}
