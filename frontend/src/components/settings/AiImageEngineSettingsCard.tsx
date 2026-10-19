"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import type { AppSettings, AiEngineStatus, Fanpage } from "@/lib/types";
import { updateSettings, listFanpages, getAiEngineStatus } from "@/lib/api";
import { getGptcliStatus, type GptcliStatus } from "@/lib/gptcliStatusApi";

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "0s";
  const s = Math.round(seconds);
  const m = Math.floor(s / 60);
  const remS = s % 60;
  if (m === 0) return `${remS}s`;
  return `${m}m ${remS}s`;
}

function formatTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  try {
    const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function formatUnavailable(
  until: string | null | undefined,
  reason: string | null | undefined
): string {
  if (!until) return "Available";
  try {
    const d = new Date(until.endsWith("Z") ? until : `${until}Z`);
    if (!Number.isNaN(d.getTime()) && d.getTime() <= Date.now()) {
      return "Available";
    }
    const timeStr = !Number.isNaN(d.getTime())
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : until;
    return `Unavailable until ${timeStr}${reason ? ` (${reason})` : ""}`;
  } catch {
    return `Unavailable until ${until}${reason ? ` (${reason})` : ""}`;
  }
}

function extractError(err: any): string {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d.msg || JSON.stringify(d)).join(", ");
  }
  return "Failed to update AI image engine settings";
}

function ClampedNumberInput({
  label,
  value,
  min,
  max,
  defaultVal,
  onCommit,
}: {
  label: string;
  value: number | undefined;
  min: number;
  max: number;
  defaultVal: number;
  onCommit: (val: number) => void;
}) {
  const [local, setLocal] = useState<string | number>(value ?? defaultVal);

  useEffect(() => {
    setLocal(value ?? defaultVal);
  }, [value, defaultVal]);

  const commit = () => {
    const parsed = typeof local === "string" ? parseInt(local, 10) : local;
    const clamped = Math.max(min, Math.min(max, Number.isNaN(parsed) ? defaultVal : parsed));
    setLocal(clamped);
    if (clamped !== (value ?? defaultVal)) {
      onCommit(clamped);
    }
  };

  return (
    <div>
      <label className="block text-xs font-semibold text-text mb-1">{label}</label>
      <input
        type="number"
        min={min}
        max={max}
        className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
    </div>
  );
}

function TextInput({
  label,
  value,
  placeholder,
  onCommit,
}: {
  label: string;
  value: string | undefined | null;
  placeholder?: string;
  onCommit: (val: string) => void;
}) {
  const [local, setLocal] = useState<string>(value ?? "");

  useEffect(() => {
    setLocal(value ?? "");
  }, [value]);

  const commit = () => {
    if (local !== (value ?? "")) {
      onCommit(local);
    }
  };

  return (
    <div>
      <label className="block text-xs font-semibold text-text mb-1">{label}</label>
      <input
        type="text"
        className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
        placeholder={placeholder}
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
    </div>
  );
}

function CodexUsageUrlInput({
  value,
  onCommit,
}: {
  value: string | undefined | null;
  onCommit: (val: string) => void;
}) {
  const [local, setLocal] = useState<string>(value ?? "");

  useEffect(() => {
    setLocal(value ?? "");
  }, [value]);

  const commit = () => {
    const trimmed = local.trim();
    if (trimmed !== (value ?? "")) {
      onCommit(trimmed);
    }
  };

  return (
    <div className="sm:col-span-2 md:col-span-3">
      <label className="label">Codex usage URL (empty = estimate)</label>
      <input
        type="text"
        className="input-rect font-mono text-xs"
        placeholder="https://..."
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
    </div>
  );
}

export function AiImageEngineSettingsCard({
  settings,
  onChanged,
}: {
  settings: AppSettings | undefined;
  onChanged: () => void;
}) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  const { data: fanpages } = useSWR<Fanpage[]>("fanpages", () =>
    listFanpages().then((r) => (Array.isArray(r.data) ? r.data : []))
  );

  const { data: statusData, error: statusError } = useSWR<AiEngineStatus>(
    "ai-engine-status",
    () => getAiEngineStatus().then((r) => r.data),
    { refreshInterval: 60000 }
  );

  const { data: gptcliStatus } = useSWR<GptcliStatus>(
    "gptcli-status",
    () => getGptcliStatus().then((r) => r.data),
    { refreshInterval: 20000 }
  );

  async function update(key: string, value: unknown) {
    setError(null);
    try {
      await updateSettings({ [key]: value });
      onChanged();
    } catch (err: any) {
      setError(extractError(err));
    }
  }

  function toggleFanpage(id: number) {
    const current = settings?.gptcli_fanpage_ids ?? [];
    const next = current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id];
    update("gptcli_fanpage_ids", next);
  }

  const selectedFanpageIds = settings?.gptcli_fanpage_ids ?? [];

  return (
    <section className="bg-surface border border-line rounded-[14px] p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-text">
          AI Image Engine (ChatGPT / Codex)
        </h2>
        <p className="text-xs text-text2 mt-1">
          ChatGPT (gpt serve) is the primary image engine; Codex (9Router) shares the load and
          is the fallback; the template is the last resort. Switch off = previous behaviour.
        </p>
      </div>

      {error && (
        <p className="text-xs text-bad bg-bad-soft rounded-lg p-2.5">
          {error}
        </p>
      )}

      {/* ChatGPT status */}
      <div className="bg-surface2 border border-line rounded-lg p-3 text-xs">
        <div className="text-[11px] font-semibold text-text2 uppercase tracking-wider mb-1.5">
          ChatGPT status
        </div>
        {!gptcliStatus ? (
          <p className="text-text2 italic">Loading status…</p>
        ) : !gptcliStatus.reachable ? (
          <div className="flex items-center gap-2 text-bad">
            <span className="inline-block w-2 h-2 rounded-full bg-bad" />
            <span className="font-medium">Not reachable</span>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-text">
            <div className="flex items-center gap-1.5">
              <span
                className={`inline-block w-2 h-2 rounded-full ${
                  gptcliStatus.running ? "bg-good animate-pulse" : "bg-text2/40"
                }`}
              />
              <span className="font-medium">
                {gptcliStatus.running
                  ? `Running for ${formatDuration(gptcliStatus.running_for_s)}`
                  : "Idle"}
              </span>
            </div>
            <span className="text-line">•</span>
            <span className="text-text2">
              <strong className="text-text font-semibold">{gptcliStatus.queue_len}</strong> waiting
            </span>
            <span className="text-line">•</span>
            <span className="text-text2">
              <strong className="text-text font-semibold">{gptcliStatus.today_count ?? 0}</strong>/
              {gptcliStatus.daily_cap ?? "—"} today
            </span>
            {gptcliStatus.sleeping && (
              <>
                <span className="text-line">•</span>
                <span className="text-warn font-medium">
                  Sleeping (00:00–{settings?.ai_day_start || "05:30"})
                </span>
              </>
            )}
            {gptcliStatus.quota_until && (
              <>
                <span className="text-line">•</span>
                <span className="text-warn font-medium">
                  Quota pause until {formatTime(gptcliStatus.quota_until) || gptcliStatus.quota_until}
                </span>
              </>
            )}
          </div>
        )}
      </div>

      {/* Toggle */}
      <div>
        <label className="flex items-center gap-3 text-sm text-text cursor-pointer">
          <input
            type="checkbox"
            className="w-4 h-4 accent-pri rounded"
            checked={settings?.gptcli_enabled ?? false}
            onChange={(e) => update("gptcli_enabled", e.target.checked)}
          />
          <span>Use ChatGPT (gpt-cli)</span>
        </label>
        {settings?.gptcli_enabled && (
          <p className="text-xs text-warn mt-1">
            Only enable after the server checks (phase A) passed.
          </p>
        )}
      </div>

      {/* Base URL */}
      <TextInput
        label="gpt serve base URL"
        value={settings?.gptcli_base_url ?? "http://172.17.0.1:8099"}
        placeholder="http://172.17.0.1:8099"
        onCommit={(val) => update("gptcli_base_url", val)}
      />

      {/* Token */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="block text-xs font-semibold text-text">
            gpt serve token{" "}
            {settings?.has_gptcli_token && (
              <span className="text-pri-text">✓ saved</span>
            )}
          </label>
          {settings?.has_gptcli_token && (
            <button
              type="button"
              onClick={() => {
                update("gptcli_token", "");
                setToken("");
              }}
              className="text-xs text-bad hover:underline cursor-pointer"
            >
              Clear
            </button>
          )}
        </div>
        <input
          type="password"
          className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
          placeholder="Leave blank to keep existing"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          onBlur={() => {
            if (token !== "") {
              update("gptcli_token", token);
              setToken("");
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && token !== "") {
              update("gptcli_token", token);
              setToken("");
            }
          }}
        />
      </div>

      {/* Fanpages */}
      <div>
        <label className="block text-xs font-semibold text-text">Brands / Fanpages</label>
        <p className="text-xs text-text2 mb-2">
          None selected = all fanpages
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-48 overflow-y-auto p-2 rounded-lg border border-line bg-surface2">
          {fanpages && fanpages.length > 0 ? (
            fanpages.map((fp) => {
              const checked = selectedFanpageIds.includes(fp.id);
              return (
                <label
                  key={fp.id}
                  className="flex items-center gap-2 text-xs text-text cursor-pointer"
                >
                  <input
                    type="checkbox"
                    className="w-4 h-4 accent-pri rounded"
                    checked={checked}
                    onChange={() => toggleFanpage(fp.id)}
                  />
                  <span className="truncate">{fp.name}</span>
                </label>
              );
            })
          ) : (
            <span className="text-xs text-text2">No fanpages found</span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <TextInput
          label="Thread"
          value={settings?.gptcli_thread ?? "default"}
          placeholder="default"
          onCommit={(val) => update("gptcli_thread", val || "default")}
        />
        <TextInput
          label="Size"
          value={settings?.gptcli_size ?? "1080x1350"}
          placeholder="1080x1350"
          onCommit={(val) => update("gptcli_size", val || "1080x1350")}
        />
        <TextInput
          label="Day starts (WIB)"
          value={settings?.ai_day_start ?? "05:30"}
          placeholder="05:30"
          onCommit={(val) => update("ai_day_start", val || "05:30")}
        />
      </div>

      {/* Codex section */}
      <div className="pt-2 border-t border-line space-y-3">
        <h3 className="text-sm font-semibold text-text">
          Codex (9Router cx/*)
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          <ClampedNumberInput
            label="Day share max %"
            value={settings?.codex_share_percent}
            min={0}
            max={100}
            defaultVal={35}
            onCommit={(val) => update("codex_share_percent", val)}
          />
          <ClampedNumberInput
            label="Share window (jobs)"
            value={settings?.codex_share_window_jobs}
            min={1}
            max={1000}
            defaultVal={40}
            onCommit={(val) => update("codex_share_window_jobs", val)}
          />
          <ClampedNumberInput
            label="Overflow queue length"
            value={settings?.codex_overflow_queue_len}
            min={0}
            max={100}
            defaultVal={3}
            onCommit={(val) => update("codex_overflow_queue_len", val)}
          />
          <ClampedNumberInput
            label="Share only while 5 h usage <"
            value={settings?.codex_share_max_pct}
            min={0}
            max={100}
            defaultVal={60}
            onCommit={(val) => update("codex_share_max_pct", val)}
          />
          <ClampedNumberInput
            label="Fallback allowed while 5 h usage <"
            value={settings?.codex_fallback_max_pct}
            min={0}
            max={100}
            defaultVal={85}
            onCommit={(val) => update("codex_fallback_max_pct", val)}
          />
          <ClampedNumberInput
            label="Night stops at"
            value={settings?.codex_night_max_pct}
            min={0}
            max={100}
            defaultVal={70}
            onCommit={(val) => update("codex_night_max_pct", val)}
          />
          <ClampedNumberInput
            label="Rest at"
            value={settings?.codex_rest_pct}
            min={0}
            max={100}
            defaultVal={95}
            onCommit={(val) => update("codex_rest_pct", val)}
          />
          <ClampedNumberInput
            label="Estimated 5 h capacity (images)"
            value={settings?.codex_window_capacity_est}
            min={1}
            max={10000}
            defaultVal={60}
            onCommit={(val) => update("codex_window_capacity_est", val)}
          />
          <ClampedNumberInput
            label="Night gap (s)"
            value={settings?.codex_night_min_gap_s}
            min={0}
            max={3600}
            defaultVal={240}
            onCommit={(val) => update("codex_night_min_gap_s", val)}
          />
          <ClampedNumberInput
            label="Night gap jitter (s)"
            value={settings?.codex_night_gap_jitter_s}
            min={0}
            max={3600}
            defaultVal={60}
            onCommit={(val) => update("codex_night_gap_jitter_s", val)}
          />
          <ClampedNumberInput
            label="Codex daily cap"
            value={settings?.visual_engine_daily_max}
            min={0}
            max={1000}
            defaultVal={60}
            onCommit={(val) => update("visual_engine_daily_max", val)}
          />
          <CodexUsageUrlInput
            value={settings?.codex_usage_url}
            onCommit={(val) => update("codex_usage_url", val)}
          />
        </div>
      </div>

      {/* Today status box */}
      <div className="pt-2 border-t border-line space-y-2">
        <h3 className="text-sm font-semibold text-text">Today</h3>
        {statusError ? (
          <p className="text-xs text-text2 italic">Status unavailable</p>
        ) : !statusData ? (
          <p className="text-xs text-text2 italic">Loading status…</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-xs">
            <div className="bg-surface2 p-2.5 rounded-lg">
              <span className="text-text2 block text-[11px]">ChatGPT</span>
              <span className="font-semibold text-text text-sm">
                {statusData.today?.gptcli?.success ?? 0} ok / {statusData.today?.gptcli?.recovered ?? 0} rec / {statusData.today?.gptcli?.failed ?? 0} fail
              </span>
            </div>
            <div className="bg-surface2 p-2.5 rounded-lg">
              <span className="text-text2 block text-[11px]">Codex</span>
              <span className="font-semibold text-text text-sm">
                {statusData.today?.["9router"]?.success ?? 0} ok / {statusData.today?.["9router"]?.recovered ?? 0} rec / {statusData.today?.["9router"]?.failed ?? 0} fail
              </span>
            </div>
            <div className="bg-surface2 p-2.5 rounded-lg">
              <span className="text-text2 block text-[11px]">Deferred</span>
              <span className="font-semibold text-text text-base">
                {statusData.today?.deferred ?? 0}
              </span>
            </div>
            <div className="bg-surface2 p-2.5 rounded-lg">
              <span className="text-text2 block text-[11px]">Face-check rejects</span>
              <span className="font-semibold text-text text-base">
                {statusData.identity_rejects_today ?? 0}
              </span>
            </div>
            <div className="bg-surface2 p-2.5 rounded-lg">
              <span className="text-text2 block text-[11px]">Codex day share</span>
              <span className="font-semibold text-text text-base">
                {statusData.codex_day_share != null
                  ? `${Math.round(statusData.codex_day_share <= 1 && statusData.codex_day_share > 0 ? statusData.codex_day_share * 100 : statusData.codex_day_share)}%`
                  : "N/A"}
              </span>
            </div>
            <div className="bg-surface2 p-2.5 rounded-lg">
              <span className="text-text2 block text-[11px]">Codex 5 h usage</span>
              <span className="font-semibold text-text text-sm">
                {statusData.codex_usage
                  ? `${Math.round(statusData.codex_usage.window_pct ?? 0)}% (${statusData.codex_usage.source || "estimate"}${formatTime(statusData.codex_usage.resets_at) ? ` · reset ${formatTime(statusData.codex_usage.resets_at)}` : ""})`
                  : "N/A"}
              </span>
            </div>
            <div className="bg-surface2 p-2.5 rounded-lg">
              <span className="text-text2 block text-[11px]">gpt serve</span>
              <span className="font-semibold text-text text-sm">
                {formatUnavailable(
                  statusData.gptcli_unavailable_until,
                  statusData.gptcli_unavailable_reason
                )}
              </span>
            </div>
            <div className="bg-surface2 p-2.5 rounded-lg">
              <span className="text-text2 block text-[11px]">Visual queue</span>
              <span className="font-semibold text-text text-base">
                {statusData.visual_queue_len ?? 0}
              </span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
