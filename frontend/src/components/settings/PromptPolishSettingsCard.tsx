"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import type { AppSettings, PromptPolishHealth } from "@/lib/types";
import { updateSettings, getPromptPolishHealth } from "@/lib/api";

function extractError(err: any): string {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d.msg || JSON.stringify(d)).join(", ");
  }
  return "Failed to update prompt polish settings";
}

function formatProblemCode(code: string): string {
  if (code === "not_configured") return "No token set";
  if (code === "service_unreachable") return "Service unreachable";
  if (code === "service_unauthorized") return "Service unauthorized";
  if (code === "proxy_down") return "Phone proxy down";
  if (code === "no_account_ready") return "No Gemini account available";
  if (code.startsWith("account_needs_login:")) {
    return `Account ${code.slice("account_needs_login:".length)} needs login`;
  }
  if (code.startsWith("account_needs_tos:")) {
    return `Account ${code.slice("account_needs_tos:".length)} must accept the Gemini Terms`;
  }
  if (code.startsWith("account_abuse_flag:")) {
    return `Account ${code.slice("account_abuse_flag:".length)} flagged (resting 24 h)`;
  }
  if (code.startsWith("polish_failed:")) {
    return `Idea ${code.slice("polish_failed:".length)} failed polish`;
  }
  if (code.startsWith("brand_starving:")) {
    return `Brand ${code.slice("brand_starving:".length)} waiting for polished ideas`;
  }
  if (code.startsWith("last_run_unavailable:")) {
    return `Last run: ${code.slice("last_run_unavailable:".length)}`;
  }
  return code;
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatEpochTime(sec: number | null | undefined): string {
  if (!sec) return "—";
  try {
    const d = new Date(sec * 1000);
    if (Number.isNaN(d.getTime())) return "—";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "—";
  }
}

export function PromptPolishSettingsCard({
  settings,
  onChanged,
}: {
  settings: AppSettings | undefined;
  onChanged: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState(settings?.prompt_polish_base_url ?? "");
  const [token, setToken] = useState("");
  const [model, setModel] = useState(settings?.prompt_polish_model ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setBaseUrl(settings?.prompt_polish_base_url ?? "");
  }, [settings?.prompt_polish_base_url]);

  useEffect(() => {
    setModel(settings?.prompt_polish_model ?? "");
  }, [settings?.prompt_polish_model]);

  const { data: healthRes } = useSWR(
    "prompt-polish-health",
    getPromptPolishHealth,
    { refreshInterval: 60000 }
  );
  const health: PromptPolishHealth | undefined =
    (healthRes as any)?.data ?? healthRes;

  async function handleSave() {
    setError(null);
    setSaving(true);
    try {
      const changed: Record<string, unknown> = {};
      if (baseUrl !== (settings?.prompt_polish_base_url ?? "")) {
        changed.prompt_polish_base_url = baseUrl;
      }
      if (token) {
        changed.prompt_polish_token = token;
      }
      if (model !== (settings?.prompt_polish_model ?? "")) {
        changed.prompt_polish_model = model;
      }
      if (Object.keys(changed).length > 0) {
        await updateSettings(changed);
        setToken("");
        onChanged();
      }
    } catch (err: any) {
      setError(extractError(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleClearToken() {
    setError(null);
    setSaving(true);
    try {
      await updateSettings({ prompt_polish_token: "" });
      setToken("");
      onChanged();
    } catch (err: any) {
      setError(extractError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="bg-surface border border-line rounded-[14px] p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-text">
          Prompt polish (Gemini Argon)
        </h2>
        <p className="text-xs text-text2 mt-1">
          Rewrites video clip prompts with Gemini Argon to ensure logical continuity, consistent voice, and natural sound before generation.
        </p>
      </div>

      {error && (
        <p className="text-xs text-bad bg-bad-soft rounded-lg p-2.5">
          {error}
        </p>
      )}

      {/* Base URL */}
      <div>
        <label className="block text-xs font-semibold text-text mb-1">
          Base URL
        </label>
        <input
          type="text"
          className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
          placeholder="http://172.17.0.1:8098"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
      </div>

      {/* Token */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="block text-xs font-semibold text-text">
            Token{" "}
            {settings?.has_prompt_polish_token && (
              <span className="text-pri-text font-normal">✓ saved</span>
            )}
          </label>
          {settings?.has_prompt_polish_token && (
            <button
              type="button"
              onClick={handleClearToken}
              disabled={saving}
              className="text-xs text-bad hover:underline cursor-pointer disabled:opacity-50"
            >
              Clear
            </button>
          )}
        </div>
        <input
          type="password"
          className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
          placeholder={settings?.has_prompt_polish_token ? "✓ saved" : "paste new token"}
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
      </div>

      {/* Model */}
      <div>
        <label className="block text-xs font-semibold text-text mb-1">
          Model
        </label>
        <input
          type="text"
          className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
          placeholder="gemini-2.5-argon"
          value={model}
          onChange={(e) => setModel(e.target.value)}
        />
      </div>

      <div className="flex justify-end pt-1">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="h-9 px-4 rounded-lg bg-pri text-white text-xs font-semibold hover:bg-pri/90 disabled:opacity-50 transition-colors cursor-pointer"
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
      </div>

      {/* Status panel */}
      <div className="bg-surface2 border border-line rounded-lg p-3 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold text-text2 uppercase tracking-wider">
            Health & Status
          </span>
          {health ? (
            health.ok ? (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-pri-soft text-pri-text">
                Healthy
              </span>
            ) : (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-bad-soft text-bad">
                Issues detected
              </span>
            )
          ) : (
            <span className="text-xs text-text2 italic">Loading…</span>
          )}
        </div>

        {health && !health.ok && health.problems && health.problems.length > 0 && (
          <div className="space-y-1">
            <div className="text-xs font-semibold text-text">Problems detected:</div>
            <ul className="list-disc list-inside space-y-0.5 text-xs text-bad">
              {health.problems.map((prob, i) => (
                <li key={i}>{formatProblemCode(prob)}</li>
              ))}
            </ul>
          </div>
        )}

        {health?.service?.error && (
          <div className="text-xs text-bad bg-bad-soft p-2 rounded border border-bad/20">
            Service: {health.service.error}
          </div>
        )}

        {/* Counters */}
        {health && (
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 pt-1 border-t border-line text-xs">
            <div>
              <div className="text-text2 text-[11px]">Brands on</div>
              <div className="font-semibold text-text">{health.brands_on}</div>
            </div>
            <div>
              <div className="text-text2 text-[11px]">Ideas prepared</div>
              <div className="font-semibold text-text">{health.ideas?.prepared ?? 0}</div>
            </div>
            <div>
              <div className="text-text2 text-[11px]">Ideas waiting</div>
              <div className="font-semibold text-text">{health.ideas?.waiting ?? 0}</div>
            </div>
            <div>
              <div className="text-text2 text-[11px]">Failed (24 h)</div>
              <div className="font-semibold text-text">{health.ideas?.failed_24h ?? 0}</div>
            </div>
            <div>
              <div className="text-text2 text-[11px]">Last OK</div>
              <div className="font-semibold text-text truncate">
                {health.last_ok ? formatDateTime(health.last_ok) : "Never"}
              </div>
            </div>
          </div>
        )}

        {/* Accounts table */}
        <div className="space-y-1.5 pt-1 border-t border-line">
          <div className="text-[11px] font-semibold text-text2 uppercase tracking-wider">
            Gemini Accounts
          </div>
          {health?.service?.accounts && health.service.accounts.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-line text-[11px] text-text2">
                    <th className="pb-1 font-medium">Name</th>
                    <th className="pb-1 font-medium">Status</th>
                    <th className="pb-1 font-medium">Available</th>
                    <th className="pb-1 font-medium">Reason</th>
                    <th className="pb-1 font-medium">Used / Cap</th>
                    <th className="pb-1 font-medium">Next ready</th>
                    <th className="pb-1 font-medium">Sleep window</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line/50">
                  {health.service.accounts.map((a, idx) => (
                    <tr key={idx} className="text-text">
                      <td className="py-1 font-mono text-[11px]">{a.name}</td>
                      <td className="py-1">{a.status}</td>
                      <td className="py-1">{a.available ? "✓" : "—"}</td>
                      <td className="py-1 text-text2">{a.reason ?? "—"}</td>
                      <td className="py-1">{`${a.used_today ?? 0} / ${a.cap_today ?? "—"}`}</td>
                      <td className="py-1 text-text2">{formatEpochTime(a.next_ok_at)}</td>
                      <td className="py-1 text-text2">
                        {a.sleep_start && a.sleep_end
                          ? `${formatEpochTime(Number(a.sleep_start))}–${formatEpochTime(Number(a.sleep_end))}`
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-xs text-text2 italic">No Gemini accounts registered.</p>
          )}
        </div>
      </div>
    </section>
  );
}
