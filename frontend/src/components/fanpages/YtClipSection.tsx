"use client";

import { useState } from "react";
import { Icon } from "@iconify/react";
import { addYtClipSource, updateYtClipSource, deleteYtClipSource, checkYtClipSource } from "@/lib/api";
import type { FanpageDetail, YtClipSourceRef } from "@/lib/types";
import { YtClipIdeaQueue, YtVideoList } from "./YtClipLists";

type Props = {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  /** Re-fetch the fanpage and return its fresh source list. */
  refreshSources: () => Promise<void>;
};

const KIND_ICON: Record<YtClipSourceRef["kind"], string> = {
  channel: "solar:user-circle-bold-duotone",
  playlist: "solar:list-bold-duotone",
  video: "solar:videocamera-record-bold-duotone",
};

function errorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

function NumberField({ label, value, min, max, onChange, hint }: {
  label: string; value: number; min: number; max: number; onChange: (v: number) => void; hint?: string;
}) {
  return (
    <div>
      <label className="label">{label}</label>
      <input
        type="number"
        min={min}
        max={max}
        className="input w-full"
        value={value}
        onChange={(e) => onChange(parseInt(e.target.value || "0"))}
      />
      {hint && <p className="text-[11px] text-text-secondary mt-1">{hint}</p>}
    </div>
  );
}

function Toggle({ on, onClick, label }: { on: boolean; onClick: () => void; label: string }) {
  return (
    <label className="flex items-center gap-2 cursor-pointer text-sm text-text-primary">
      <input type="checkbox" className="w-4 h-4 rounded accent-primary-main" checked={on} onChange={onClick} />
      {label}
    </label>
  );
}

function YtClipSettings({ form, set }: Pick<Props, "form" | "set">) {
  const num = (key: keyof FanpageDetail, fallback: number) => (form[key] as number | undefined) ?? fallback;
  return (
    <>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <NumberField label="Clips per day" value={num("yt_clip_daily_count", 2)} min={0} max={20} onChange={(v) => set("yt_clip_daily_count", v)} />
        <NumberField label="Min clip length (s)" value={num("yt_clip_min_s", 60)} min={15} max={170} onChange={(v) => set("yt_clip_min_s", v)} />
        <NumberField label="Max clip length (s)" value={num("yt_clip_max_s", 120)} min={25} max={180} onChange={(v) => set("yt_clip_max_s", v)} />
        <NumberField label="Max clips per video" value={num("yt_clip_per_video", 3)} min={1} max={10} onChange={(v) => set("yt_clip_per_video", v)} />
        <NumberField
          label="Min virality score"
          value={num("yt_clip_min_score", 6)}
          min={0}
          max={10}
          onChange={(v) => set("yt_clip_min_score", v)}
          hint="AI score 1–10; lower-scored moments are dropped."
        />
        <NumberField
          label="Max video age (days)"
          value={num("yt_clip_max_video_age_days", 7)}
          min={1}
          max={365}
          onChange={(v) => set("yt_clip_max_video_age_days", v)}
          hint="Channels only — playlists and single links have no age limit."
        />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label className="label">Clip Publish Mode</label>
          <div className="flex gap-4 pt-2">
            {(["manual_review", "auto"] as const).map((mode) => (
              <label key={mode} className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="yt-clip-publish-mode"
                  checked={form.yt_clip_publish_mode === mode}
                  onChange={() => set("yt_clip_publish_mode", mode)}
                  className="accent-primary-main"
                />
                <span className="text-sm text-text-primary">{mode === "auto" ? "Auto-publish" : "Manual Review"}</span>
              </label>
            ))}
          </div>
        </div>
        <div>
          <label className="label">Overlays</label>
          <div className="flex gap-5 pt-2">
            <Toggle on={form.yt_clip_captions ?? true} onClick={() => set("yt_clip_captions", !(form.yt_clip_captions ?? true))} label="Captions" />
            <Toggle on={form.yt_clip_watermark ?? true} onClick={() => set("yt_clip_watermark", !(form.yt_clip_watermark ?? true))} label="Watermark" />
          </div>
        </div>
      </div>
      <p className="text-[11px] text-text-secondary">
        Captions are word-by-word in the video&apos;s own language (they&apos;re what the speaker says). The watermark
        uses this fanpage&apos;s logo, or its watermark text. The Facebook caption uses the News/Mode 2 caption
        settings above. Facebook may claim official race/fight footage — interviews and press conferences are safer.
      </p>
    </>
  );
}

function YtClipSources({ fanpageId, form, refreshSources }: Omit<Props, "set">) {
  const [url, setUrl] = useState("");
  const [direction, setDirection] = useState("");
  const [busy, setBusy] = useState(false);
  const sources = (form.yt_clip_sources as YtClipSourceRef[] | undefined) ?? [];

  async function add() {
    if (!url.trim()) return;
    setBusy(true);
    try {
      await addYtClipSource(fanpageId, { url: url.trim(), direction: direction.trim() || undefined });
      setUrl("");
      setDirection("");
      await refreshSources();
    } catch (err) {
      alert(errorMessage(err, "Couldn't add that link."));
    } finally {
      setBusy(false);
    }
  }

  async function act(fn: () => Promise<unknown>, fallback: string) {
    try {
      await fn();
      await refreshSources();
    } catch (err) {
      alert(errorMessage(err, fallback));
    }
  }

  async function checkNow(s: YtClipSourceRef) {
    try {
      const res = await checkYtClipSource(fanpageId, s.id);
      await refreshSources();
      alert(`${(res.data as { new_videos: number }).new_videos} new video(s) found.`);
    } catch (err) {
      alert(errorMessage(err, "Couldn't check that source."));
    }
  }

  return (
    <div className="border-t border-hairline pt-4 space-y-3">
      <div>
        <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide">YouTube Sources</p>
        <p className="text-[11px] text-text-secondary mt-0.5">
          Paste a channel (@handle or /channel/ link — new uploads are picked up automatically), a playlist, or a
          single video link. Shorts are skipped. One video can become several clips.
        </p>
      </div>
      <div className="space-y-1.5">
        {sources.length === 0 ? (
          <p className="text-[11px] text-text-secondary italic">No sources yet.</p>
        ) : (
          sources.map((s) => (
            <div key={s.id} className="flex items-center gap-3 p-2.5 rounded-lg border border-hairline">
              <input
                type="checkbox"
                className="w-4 h-4 rounded accent-primary-main shrink-0"
                checked={s.is_active}
                onChange={() => act(() => updateYtClipSource(fanpageId, s.id, { is_active: !s.is_active }), "Couldn't update the source.")}
                title={s.is_active ? "Active — click to pause" : "Paused — click to activate"}
              />
              <Icon icon={KIND_ICON[s.kind]} width={18} className="text-text-secondary shrink-0" />
              <div className="min-w-0 flex-1">
                <p className={`text-sm leading-tight truncate ${s.is_active ? "text-text-primary" : "text-text-secondary line-through"}`}>
                  {s.label || s.url}
                </p>
                <p className="text-[11px] text-text-secondary truncate">
                  {s.kind} · {s.videos_found} video(s) found
                  {s.direction ? ` · “${s.direction}”` : ""}
                  {s.last_error ? ` · ⚠ ${s.last_error}` : ""}
                </p>
              </div>
              <button onClick={() => checkNow(s)} className="text-text-secondary hover:text-text-primary shrink-0" title="Check for new videos now">
                <Icon icon="solar:refresh-bold-duotone" width={17} />
              </button>
              <button
                onClick={() => act(() => deleteYtClipSource(fanpageId, s.id), "Couldn't delete the source.")}
                className="text-text-secondary hover:text-error-main shrink-0"
                title="Delete source"
              >
                <Icon icon="solar:trash-bin-trash-bold-duotone" width={18} />
              </button>
            </div>
          ))
        )}
      </div>
      <div className="flex flex-col gap-2">
        <input
          type="text"
          className="input w-full"
          placeholder="YouTube channel, playlist or video link"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <div className="flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            className="input flex-1"
            placeholder="Optional AI direction (e.g. focus on the heated moments, skip the sponsor read)"
            value={direction}
            onChange={(e) => setDirection(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <button onClick={add} disabled={busy || !url.trim()} className="btn-primary shrink-0 disabled:opacity-50">
            {busy ? "Adding…" : "Add"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Mode 7 — YouTube clips, on the fanpage edit page. */
export function YtClipSection({ fanpageId, form, set, refreshSources }: Props) {
  const enabled = !!form.yt_clip_enabled;
  return (
    <section className="card space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-text-primary">Mode 7: YouTube Clips</h2>
          <p className="text-xs text-text-secondary mt-0.5">
            Long YouTube videos → the AI picks the best moments from the transcript → vertical 9:16 clips (the crop
            follows the speaker&apos;s face; action shots keep the whole frame) → posted as Reels on this fanpage&apos;s
            own pacing.
          </p>
        </div>
        <button
          onClick={() => set("yt_clip_enabled", !enabled)}
          className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${enabled ? "bg-primary-main" : "bg-hairline"}`}
          aria-label="Toggle Mode 7"
        >
          <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${enabled ? "translate-x-5" : ""}`} />
        </button>
      </div>
      {enabled && (
        <>
          <YtClipSettings form={form} set={set} />
          <YtClipSources fanpageId={fanpageId} form={form} refreshSources={refreshSources} />
          <YtClipIdeaQueue fanpageId={fanpageId} />
          <YtVideoList fanpageId={fanpageId} />
        </>
      )}
    </section>
  );
}
