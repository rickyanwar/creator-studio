"use client";

import { useState, useEffect } from "react";
import { Icon } from "@iconify/react";
import { updateIGSource, setSourceTrigger } from "@/lib/api";
import { setSourceFormat, type IGFormat } from "@/lib/igFormatApi";
import type { FanpageDetail, IGSourceRef } from "@/lib/types";

const MAX_ALBUM = 10;

function getSourceFormat(s: IGSourceRef): IGFormat {
  if ((s as any).format) return (s as any).format as IGFormat;
  if (s.ig_recreate_enabled === null) return "follow";
  if (s.ig_recreate_enabled === false) return "repost";
  if ((s as any).visual_engine_override === "chatgpt") return "chatgpt";
  return "template";
}

export function IGSourceCard({
  source,
  fanpageId,
  fanpageIgModeLabel,
  onRemove,
  onAlbumSaved,
}: {
  source: IGSourceRef;
  fanpageId: number;
  fanpageIgModeLabel: string;
  onRemove: () => void;
  onAlbumSaved: () => void;
}) {
  const [indices, setIndices] = useState<number[]>(source.album_image_indices ?? [1]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [formatSaving, setFormatSaving] = useState(false);
  const [triggerSaving, setTriggerSaving] = useState(false);
  const [localTrigger, setLocalTrigger] = useState(source.trigger ?? "every_post");
  const [localFormat, setLocalFormat] = useState<IGFormat>(() => getSourceFormat(source));

  useEffect(() => {
    setLocalFormat(getSourceFormat(source));
  }, [(source as any).format, source.ig_recreate_enabled, (source as any).visual_engine_override]);

  async function handleFormatChange(value: IGFormat) {
    const prev = localFormat;
    setLocalFormat(value);
    setFormatSaving(true);
    try {
      await setSourceFormat(fanpageId, source.id, value);
      onAlbumSaved();
    } catch (err: any) {
      setLocalFormat(prev);
      alert(err.response?.data?.detail || "Failed to update format");
    } finally {
      setFormatSaving(false);
    }
  }

  async function setTrigger(value: "every_post" | "viral_only") {
    const prev = localTrigger;
    setLocalTrigger(value);
    setTriggerSaving(true);
    try {
      await setSourceTrigger(fanpageId, source.id, value);
      onAlbumSaved();
    } catch (err: any) {
      setLocalTrigger(prev);
      alert(err.response?.data?.detail || "Failed to update trigger");
    } finally {
      setTriggerSaving(false);
    }
  }

  useEffect(() => {
    setIndices(source.album_image_indices ?? [1]);
  }, [source.album_image_indices?.join(",")]);

  async function toggle(n: number) {
    const next = indices.includes(n)
      ? indices.filter((x) => x !== n)
      : [...indices, n].sort((a, b) => a - b);
    if (next.length === 0) return;
    setIndices(next);
    setSaving(true);
    setSaved(false);
    try {
      await updateIGSource(source.id, { album_image_indices: next });
      setSaved(true);
      onAlbumSaved();
      setTimeout(() => setSaved(false), 1800);
    } finally {
      setSaving(false);
    }
  }

  const label =
    indices.length === MAX_ALBUM
      ? "All images"
      : indices.length === 1
      ? `Image ${indices[0]} only`
      : `Images ${indices.join(", ")}`;

  return (
    <div className="group relative rounded-lg border border-line bg-surface2 p-4 space-y-3 transition-colors hover:border-pri/30">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-pink-500 via-red-500 to-yellow-400 flex items-center justify-center shrink-0">
            <Icon icon="mdi:instagram" width={18} className="text-white" />
          </div>
          <div>
            <p className="text-sm font-semibold text-text leading-tight">@{source.ig_username}</p>
            <p className="text-[11px] text-text2 leading-tight">Instagram source</p>
          </div>
        </div>
        <button
          onClick={onRemove}
          className="opacity-0 group-hover:opacity-100 transition-opacity text-text2 hover:text-bad p-1.5 rounded-md hover:bg-bad-soft"
          title={`Remove @${source.ig_username}`}
        >
          <Icon icon="solar:trash-bin-trash-bold-duotone" width={15} />
        </button>
      </div>

      {/* Divider */}
      <div className="border-t border-line" />

      {/* Format select (Follow brand default / Repost as-is / Recreate with template / Recreate with ChatGPT edit) */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <Icon icon="solar:magic-stick-3-bold-duotone" width={13} className="text-text2" />
          <label htmlFor={`format-select-${source.id}`} className="text-[11px] font-medium text-text2 uppercase tracking-wide">
            Format
          </label>
          {formatSaving && <Icon icon="svg-spinners:ring-resize" width={11} className="text-pri" />}
        </div>
        <select
          id={`format-select-${source.id}`}
          value={localFormat}
          onChange={(e) => handleFormatChange(e.target.value as IGFormat)}
          disabled={formatSaving}
          className="w-full text-xs rounded-md bg-surface border border-line text-text px-2.5 py-1.5 focus:outline-none focus:border-pri disabled:opacity-50"
        >
          <option value="follow">
            {fanpageIgModeLabel ? `Follow brand default (${fanpageIgModeLabel})` : "Follow brand default"}
          </option>
          <option value="repost">Repost as-is</option>
          <option value="template">Recreate with template</option>
          <option value="chatgpt">Recreate with ChatGPT edit</option>
        </select>
        <p className="text-[10px] text-text2">
          Repost = original image + new caption. Template = re-render onto template. ChatGPT = AI image redesign.
        </p>
      </div>

      {/* Trigger */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <Icon icon="solar:radar-2-bold-duotone" width={13} className="text-text2" />
          <span className="text-[11px] font-medium text-text2 uppercase tracking-wide">
            Trigger
          </span>
          {triggerSaving && <Icon icon="svg-spinners:ring-resize" width={11} className="text-pri" />}
        </div>
        <div className="flex gap-1">
          {([
            { v: "every_post", label: "Every post" },
            { v: "viral_only", label: "Viral only" },
          ] as const).map((opt) => (
            <button
              key={opt.v}
              onClick={() => setTrigger(opt.v)}
              disabled={triggerSaving}
              className={`px-2 py-1 rounded-md text-[10px] font-medium border transition-colors disabled:opacity-48 ${
                localTrigger === opt.v
                  ? "bg-pri text-pri-fg border-pri"
                  : "bg-surface text-text2 border-line hover:border-pri hover:text-pri"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-text2 group/tooltip relative">
          Viral only: the post waits until it passes this fanpage&apos;s radar rules, then is reposted/recreated. Nothing is posted while Radar shadow mode is on.
        </p>
      </div>

      {/* Divider */}
      <div className="border-t border-line" />

      {/* Album picker */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Icon icon="solar:gallery-wide-bold-duotone" width={13} className="text-text2" />
            <span className="text-[11px] font-medium text-text2 uppercase tracking-wide">
              Album download
            </span>
          </div>
          <div className="flex items-center gap-1.5 h-4">
            {saving && (
              <Icon icon="svg-spinners:ring-resize" width={12} className="text-pri" />
            )}
            {saved && !saving && (
              <span className="flex items-center gap-0.5 text-[10px] text-pri font-semibold">
                <Icon icon="solar:check-circle-bold" width={12} />
                Saved
              </span>
            )}
            {!saving && !saved && (
              <span className="text-[11px] text-text2">{label}</span>
            )}
          </div>
        </div>

        <div className="flex gap-1 flex-wrap">
          {Array.from({ length: MAX_ALBUM }, (_, i) => i + 1).map((n) => {
            const on = indices.includes(n);
            return (
              <button
                key={n}
                onClick={() => toggle(n)}
                disabled={saving}
                className={`w-7 h-7 rounded-md text-xs font-semibold border transition-all disabled:opacity-48 ${
                  on
                    ? "bg-pri text-pri-fg border-pri"
                    : "bg-surface text-text2 border-line hover:border-pri hover:text-pri"
                }`}
              >
                {n}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export interface IgTabProps {
  fanpageId: number;
  form: Partial<FanpageDetail>;
  set: (key: string, value: unknown) => void;
  fp: FanpageDetail;
  mode1Saving: boolean;
  handleMode1Toggle: (value: boolean) => Promise<void>;
  handleIgModeChange: (mode: "repost" | "recreate_template" | "recreate_chatgpt") => void;
  allTemplates: Array<{ id: number; name: string; is_default: boolean; category: "quote" | "news" | "discussion" | null; canvas_width: number; canvas_height: number }>;
  handleRemoveSource: (username: string) => Promise<void>;
  mutate: () => Promise<any>;
  newSource: string;
  setNewSource: (v: string) => void;
  handleAddSource: () => Promise<void>;
}

export function IgTab({
  fanpageId,
  form,
  set,
  fp,
  mode1Saving,
  handleMode1Toggle,
  handleIgModeChange,
  allTemplates,
  handleRemoveSource,
  mutate,
  newSource,
  setNewSource,
  handleAddSource,
}: IgTabProps) {
  return (
    <div role="tabpanel" id="tab-panel-ig" aria-labelledby="tab-ig" className="space-y-8">
      {/* ── Section 1: IG Sources ──────────────────────── */}
      {/* ── Section: IG (Instagram) ── */}
      <section className="card space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-text">IG (Instagram)</h2>
            <p className="text-xs text-text2 mt-0.5">
              Crawl and process posts from the Instagram source accounts below.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className="text-xs text-text2 whitespace-nowrap">IG Mode</span>
            <button
              onClick={() => !mode1Saving && handleMode1Toggle(!form.mode1_ig_repost_enabled)}
              disabled={mode1Saving}
              className={`relative w-11 h-6 rounded-full transition-colors ${
                form.mode1_ig_repost_enabled ? "bg-pri" : "bg-line"
              } ${mode1Saving ? "opacity-50 cursor-not-allowed" : ""}`}
              aria-label="Toggle IG Mode"
            >
              {mode1Saving ? (
                <span className="absolute inset-0 flex items-center justify-center">
                  <Icon icon="svg-spinners:ring-resize" width={14} className="text-white" />
                </span>
              ) : (
                <span
                  className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                    form.mode1_ig_repost_enabled ? "translate-x-5" : ""
                  }`}
                />
              )}
            </button>
          </div>
        </div>

        {!form.mode1_ig_repost_enabled ? (
          <p className="text-xs text-text2 italic">
            IG Mode is off — source accounts will not be crawled or posted for this fanpage.
          </p>
        ) : (
          <div className="space-y-4 border-t border-line pt-3">
            <div>
              <label className="label">IG format</label>
              {(() => {
                const currentMode = !form.ig_recreate_enabled
                  ? "repost"
                  : form.visual_engine === "chatgpt" || form.visual_engine === "auto" || form.visual_engine === "gflow"
                  ? "recreate_chatgpt"
                  : "recreate_template";

                const options = [
                  {
                    id: "repost",
                    title: "Repost as-is",
                    desc: "Original image + new caption",
                  },
                  {
                    id: "recreate_template",
                    title: "Recreate with template",
                    desc: "Text placed on the fanpage's News/Quote template",
                  },
                  {
                    id: "recreate_chatgpt",
                    title: "Recreate with ChatGPT edit",
                    desc: "One ChatGPT image call cleans up/restyles the source image, then our template adds the text. Requires a cx/gpt-image model on 9Router.",
                  },
                ] as const;

                return (
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-1.5">
                    {options.map((opt) => (
                      <label
                        key={opt.id}
                        className={`flex flex-col p-3 rounded-lg border cursor-pointer transition-all ${
                          currentMode === opt.id
                            ? "border-pri bg-pri-soft shadow-sm"
                            : "border-line bg-surface hover:border-text2/30"
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <input
                            type="radio"
                            name="ig_mode_choice"
                            value={opt.id}
                            checked={currentMode === opt.id}
                            onChange={() => handleIgModeChange(opt.id)}
                            className="text-pri focus:ring-pri accent-pri"
                          />
                          <span className="text-xs font-semibold text-text">
                            {opt.title}
                          </span>
                        </div>
                        <p className="text-[11px] text-text2 mt-1 pl-5">
                          {opt.desc}
                        </p>
                      </label>
                    ))}
                  </div>
                );
              })()}
            </div>

            {/* Smart layout if recreate is chosen */}
            {form.ig_recreate_enabled && (
              <div className="border border-line rounded-lg p-3 bg-surface2 space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <label className="label mb-0">Smart layout (2-photo / split) <span className="text-[10px] text-warn">experimental</span></label>
                    <p className="text-[11px] text-text2 mt-0.5">
                      AI decides 1 vs 2 people → single+inset or split, selecting face/action photos.
                    </p>
                  </div>
                  <button
                    onClick={() => set("ig_recreate_smart_layout", !form.ig_recreate_smart_layout)}
                    className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
                      form.ig_recreate_smart_layout ? "bg-pri" : "bg-line"
                    }`}
                  >
                    <span
                      className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                        form.ig_recreate_smart_layout ? "translate-x-5" : ""
                      }`}
                    />
                  </button>
                </div>

                {form.ig_recreate_smart_layout && (
                  <div>
                    <label className="label">Split Template (2-person news)</label>
                    <select
                      className="input w-full"
                      value={(form.ig_recreate_split_template_id as number | null) ?? ""}
                      onChange={(e) => set("ig_recreate_split_template_id", e.target.value ? parseInt(e.target.value) : null)}
                    >
                      <option value="">— none (falls back to News Template) —</option>
                      {allTemplates.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name} ({t.canvas_width}×{t.canvas_height})
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            )}

            <div className="flex items-center justify-between p-3 rounded-lg border border-line">
              <div>
                <label htmlFor="qa-ig" className="text-xs font-medium text-text cursor-pointer">
                  Check AI results
                </label>
                <p className="text-[11px] text-text2 mt-0.5">
                  On: every AI image is checked for the right face and event (series, vehicle, logos) and replaced by the template card when wrong. Off: AI images are used without these checks.
                </p>
              </div>
              <input
                id="qa-ig"
                type="checkbox"
                className="w-4 h-4 rounded accent-pri shrink-0 cursor-pointer ml-3"
                checked={(form.qa_ig_enabled as boolean) ?? true}
                onChange={(e) => set("qa_ig_enabled", e.target.checked)}
              />
            </div>
          </div>
        )}

        {(fp.ig_sources ?? []).length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2">
            {(() => {
              const currentIgModeLabel = !form.ig_recreate_enabled
                ? "repost"
                : form.visual_engine === "chatgpt" || form.visual_engine === "auto" || form.visual_engine === "gflow"
                ? "recreate ChatGPT"
                : "recreate template";

              return (fp.ig_sources ?? []).map((src) => (
                <IGSourceCard
                  key={src.id}
                  source={src}
                  fanpageId={fanpageId}
                  fanpageIgModeLabel={currentIgModeLabel}
                  onRemove={() => handleRemoveSource(src.ig_username)}
                  onAlbumSaved={mutate}
                />
              ));
            })()}
          </div>
        )}

        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="@username or username"
            value={newSource}
            onChange={(e) => setNewSource(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAddSource()}
          />
          <button onClick={handleAddSource} className="btn-ghost">
            <Icon icon="solar:add-circle-bold-duotone" width={14} />
            Add
          </button>
        </div>
      </section>
    </div>
  );
}
