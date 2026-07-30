"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useEffect, useRef } from "react";
import useSWR from "swr";
import {
  getFanpage,
  updateFanpage,
  addIGSource,
  removeIGSourceByUsername,
  setSourceRecreateOverride,
  previewCaption,
  updateIGSource,
  listNewsSources,
  listGalleryNiches,
  listTemplates,
  addFanpageNewsSource,
  removeFanpageNewsSource,
  previewNewsCopy,
  uploadWatermarkImage,
  deleteWatermarkImage,
} from "@/lib/api";
import type { FanpageDetail, IGSourceRef } from "@/lib/types";
import { Icon } from "@iconify/react";
import { CaptionCriteriaEditor, captionFromSource, captionToPayload, type CaptionCriteria } from "@/components/CaptionCriteriaEditor";

const fetcher = (id: number) => getFanpage(id).then((r) => r.data as FanpageDetail);

const MAX_ALBUM = 10;

function IGSourceCard({
  source,
  fanpageId,
  fanpageRecreateEnabled,
  onRemove,
  onAlbumSaved,
}: {
  source: IGSourceRef;
  fanpageId: number;
  fanpageRecreateEnabled: boolean;
  onRemove: () => void;
  onAlbumSaved: () => void;
}) {
  const [indices, setIndices] = useState<number[]>(source.album_image_indices ?? [1]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [recreateSaving, setRecreateSaving] = useState(false);

  async function setRecreate(value: boolean | null) {
    setRecreateSaving(true);
    try {
      await setSourceRecreateOverride(fanpageId, source.id, value);
      onAlbumSaved();
    } finally {
      setRecreateSaving(false);
    }
  }

  const [capOpen, setCapOpen] = useState(false);
  const [capForm, setCapForm] = useState<CaptionCriteria>(captionFromSource(source));
  const [capSaving, setCapSaving] = useState(false);
  const capCount = [
    source.caption_tone, source.caption_language, source.caption_max_length,
    source.caption_hashtag_count, source.caption_cta_text, source.caption_custom_prompt,
  ].filter((v) => v !== null && v !== "").length;

  async function saveCaption() {
    setCapSaving(true);
    try {
      await updateIGSource(source.id, captionToPayload(capForm));
      onAlbumSaved();
      setCapOpen(false);
    } finally {
      setCapSaving(false);
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
    <div className="group relative rounded-lg border border-hairline bg-bg-paper-hover p-4 space-y-3 transition-colors hover:border-primary-main/30">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-pink-500 via-red-500 to-yellow-400 flex items-center justify-center shrink-0">
            <Icon icon="mdi:instagram" width={18} className="text-white" />
          </div>
          <div>
            <p className="text-sm font-semibold text-text-primary leading-tight">@{source.ig_username}</p>
            <p className="text-[11px] text-text-secondary leading-tight">Instagram source</p>
          </div>
        </div>
        <button
          onClick={onRemove}
          className="opacity-0 group-hover:opacity-100 transition-opacity text-text-secondary hover:text-error-main p-1.5 rounded-md hover:bg-error-lighter"
          title={`Remove @${source.ig_username}`}
        >
          <Icon icon="solar:trash-bin-trash-bold-duotone" width={15} />
        </button>
      </div>

      {/* Divider */}
      <div className="border-t border-hairline" />

      {/* Recreate/redesign vs plain repost override */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <Icon icon="solar:magic-stick-3-bold-duotone" width={13} className="text-text-secondary" />
          <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wide">
            Recreate design
          </span>
          {recreateSaving && <Icon icon="svg-spinners:ring-resize" width={11} className="text-primary-main" />}
        </div>
        <div className="flex gap-1">
          {([
            { v: null, label: `Inherit (${fanpageRecreateEnabled ? "recreate" : "plain"})` },
            { v: true, label: "Always recreate" },
            { v: false, label: "Always plain" },
          ] as const).map((opt) => (
            <button
              key={String(opt.v)}
              onClick={() => setRecreate(opt.v)}
              disabled={recreateSaving}
              className={`px-2 py-1 rounded-md text-[10px] font-medium border transition-colors disabled:opacity-48 ${
                source.ig_recreate_enabled === opt.v
                  ? "bg-primary-main text-white border-primary-main"
                  : "bg-bg-paper text-text-secondary border-hairline hover:border-primary-main hover:text-primary-main"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <p className="text-[10px] text-text-secondary">
          Plain = repost the original image, only the caption is AI-written. Recreate = redesign onto a quote/news template (Mode 3).
        </p>
      </div>

      {/* Divider */}
      <div className="border-t border-hairline" />

      {/* Album picker */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Icon icon="solar:gallery-wide-bold-duotone" width={13} className="text-text-secondary" />
            <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wide">
              Album download
            </span>
          </div>
          <div className="flex items-center gap-1.5 h-4">
            {saving && (
              <Icon icon="svg-spinners:ring-resize" width={12} className="text-primary-main" />
            )}
            {saved && !saving && (
              <span className="flex items-center gap-0.5 text-[10px] text-primary-main font-semibold">
                <Icon icon="solar:check-circle-bold" width={12} />
                Saved
              </span>
            )}
            {!saving && !saved && (
              <span className="text-[11px] text-text-secondary">{label}</span>
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
                    ? "bg-primary-main text-white border-primary-main"
                    : "bg-bg-paper text-text-secondary border-hairline hover:border-primary-main hover:text-primary-main"
                }`}
              >
                {n}
              </button>
            );
          })}
        </div>
      </div>

      {/* Divider */}
      <div className="border-t border-hairline" />

      {/* Per-source caption criteria */}
      <div className="space-y-2">
        <button
          onClick={() => { setCapForm(captionFromSource(source)); setCapOpen((o) => !o); }}
          className="flex items-center justify-between w-full text-left"
        >
          <span className="flex items-center gap-1.5 text-[11px] font-medium text-text-secondary uppercase tracking-wide">
            <Icon icon="solar:pen-new-square-bold-duotone" width={13} />
            Caption criteria
            {capCount > 0 && (
              <span className="normal-case rounded-full bg-primary-main/15 text-primary-main px-1.5 py-0.5 text-[10px] font-semibold">{capCount} set</span>
            )}
          </span>
          <Icon icon={capOpen ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={14} className="text-text-secondary" />
        </button>
        {capOpen && (
          <div className="pt-1">
            <CaptionCriteriaEditor
              value={capForm}
              onChange={setCapForm}
              onSave={saveCaption}
              saving={capSaving}
              hint="Applies to this Instagram source across all fanpages. Empty fields inherit this fanpage's caption criteria below."
            />
          </div>
        )}
      </div>

    </div>
  );
}

export default function FanpageEditPage() {
  const { id } = useParams<{ id: string }>();
  const fanpageId = parseInt(id);
  const router = useRouter();
  const { data: fp, mutate } = useSWR(`fanpage-${fanpageId}`, () => fetcher(fanpageId), {
    revalidateOnFocus: false,
  });

  const [form, setForm] = useState<Partial<FanpageDetail>>({});
  const [saving, setSaving] = useState(false);
  const formInitialized = useRef(false);
  const [newSource, setNewSource] = useState("");
  const [wmUploading, setWmUploading] = useState(false);

  async function handleWatermarkUpload(file: File) {
    setWmUploading(true);
    try {
      await uploadWatermarkImage(fanpageId, file);
      await mutate();
    } catch (ex: any) {
      alert(
        `Upload failed: ${ex.response?.data?.detail ?? "make sure the file is a valid image."}`
      );
    } finally {
      setWmUploading(false);
    }
  }
  async function handleWatermarkDelete() {
    setWmUploading(true);
    try {
      await deleteWatermarkImage(fanpageId);
      await mutate();
    } finally {
      setWmUploading(false);
    }
  }

  const [previewSrc, setPreviewSrc] = useState("");
  const [previewOrig, setPreviewOrig] = useState("");
  const [previewResult, setPreviewResult] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [mustIncludeInput, setMustIncludeInput] = useState("");
  const [mustAvoidInput, setMustAvoidInput] = useState("");

  // ── Mode 2 (news content) state ──
  const { data: allNewsSources = [] } = useSWR<{ id: number; name: string; category_url: string }[]>(
    "news-sources-picker",
    () => listNewsSources().then((r) => r.data as { id: number; name: string; category_url: string }[])
  );
  const { data: allGalleryNiches = [] } = useSWR<string[]>(
    "gallery-niches-picker",
    () => listGalleryNiches().then((r) => r.data as string[])
  );
  type TemplateRef = { id: number; name: string; is_default: boolean; category: "quote" | "news" | null; canvas_width: number; canvas_height: number };
  const { data: allTemplates = [] } = useSWR<TemplateRef[]>(
    `templates-picker-${fanpageId}`,
    () => listTemplates(fanpageId).then((r) => r.data as TemplateRef[])
  );
  const [newsPreviewTitle, setNewsPreviewTitle] = useState("");
  const [newsPreviewContent, setNewsPreviewContent] = useState("");
  const [newsPreviewResult, setNewsPreviewResult] = useState<{ title: string; caption: string } | null>(null);
  const [newsPreviewLoading, setNewsPreviewLoading] = useState(false);

  async function toggleNewsSource(sourceId: number, subscribed: boolean) {
    if (subscribed) {
      await removeFanpageNewsSource(fanpageId, sourceId);
    } else {
      await addFanpageNewsSource(fanpageId, sourceId);
    }
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, news_sources: fresh.news_sources }));
  }

  async function handleNewsPreview() {
    setNewsPreviewLoading(true);
    setNewsPreviewResult(null);
    try {
      const res = await previewNewsCopy(fanpageId, {
        title: newsPreviewTitle,
        content: newsPreviewContent,
      });
      setNewsPreviewResult(res.data);
    } catch (err: unknown) {
      const e = err as { response?: { data?: { detail?: string } } };
      alert(`Preview failed: ${e.response?.data?.detail ?? "unknown error"}`);
    } finally {
      setNewsPreviewLoading(false);
    }
  }

  useEffect(() => {
    if (fp && !formInitialized.current) {
      setForm({ ...fp });
      formInitialized.current = true;
    }
  }, [fp]);

  function set(key: string, value: unknown) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      await updateFanpage(fanpageId, form);
      // Revalidate without overwriting the local form — ig_sources may have changed
      mutate(undefined, { revalidate: true });
    } catch {
      alert("Save failed. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleAddSource() {
    if (!newSource.trim()) return;
    await addIGSource(fanpageId, newSource.trim());
    setNewSource("");
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, ig_sources: fresh.ig_sources, ig_source_usernames: fresh.ig_source_usernames }));
  }

  async function handleRemoveSource(username: string) {
    await removeIGSourceByUsername(fanpageId, username);
    const fresh = await mutate();
    if (fresh) setForm((prev) => ({ ...prev, ig_sources: fresh.ig_sources, ig_source_usernames: fresh.ig_source_usernames }));
  }

  async function handlePreview() {
    setPreviewLoading(true);
    setPreviewResult("");
    try {
      const res = await previewCaption(fanpageId, previewSrc, previewOrig);
      setPreviewResult(res.data.caption);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Preview failed";
      setPreviewResult(`Error: ${message}`);
    } finally {
      setPreviewLoading(false);
    }
  }

  function addTag(key: "caption_must_include" | "caption_must_avoid", value: string) {
    const arr = (form[key] as string[]) || [];
    if (value && !arr.includes(value)) {
      set(key, [...arr, value]);
    }
  }

  function removeTag(key: "caption_must_include" | "caption_must_avoid", value: string) {
    const arr = (form[key] as string[]) || [];
    set(key, arr.filter((v) => v !== value));
  }

  if (!fp) return <div className="text-sm text-text-secondary">Loading…</div>;

  return (
    <div className="max-w-3xl space-y-8">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => router.back()}
          className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-bg-paper-hover transition-colors text-text-secondary hover:text-text-primary"
        >
          <Icon icon="solar:alt-arrow-left-bold-duotone" width={20} />
        </button>
        <div>
          <h1 className="text-2xl font-bold text-text-primary">{fp.name}</h1>
          <p className="text-xs text-text-secondary">{fp.repliz_account_id}</p>
        </div>
      </div>

      {/* ── Section 1: IG Sources ──────────────────────── */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">Instagram Sources</h2>

        {(fp.ig_sources ?? []).length > 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {(fp.ig_sources ?? []).map((src) => (
              <IGSourceCard
                key={src.id}
                source={src}
                fanpageId={fanpageId}
                fanpageRecreateEnabled={!!fp.ig_recreate_enabled}
                onRemove={() => handleRemoveSource(src.ig_username)}
                onAlbumSaved={mutate}
              />
            ))}
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

      {/* ── Section: Design Templates (shared by Mode 2 news + Mode 3 ig_recreate) ── */}
      <section className="card space-y-5">
        <div>
          <h2 className="text-base font-semibold text-text-primary">Design Templates</h2>
          <p className="text-xs text-text-secondary mt-0.5">
            One setting per category, used everywhere that category of content is rendered — news designs from
            Mode 2 (news-scrape) and Mode 3 (IG recreate, classified &quot;news&quot;) share the News Template;
            Mode 3 posts classified &quot;quote&quot; use the Quote Template.
          </p>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label">Quote Template</label>
            <select
              className="input w-full"
              value={(form.default_quote_template_id as number | null) ?? ""}
              onChange={(e) => set("default_quote_template_id", e.target.value ? parseInt(e.target.value) : null)}
            >
              <option value="">— shared default (quote-tagged) —</option>
              {allTemplates.filter((t) => t.category !== "news").map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.canvas_width}×{t.canvas_height}){t.is_default ? " · shared default" : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">News Template</label>
            <select
              className="input w-full"
              value={(form.default_news_template_id as number | null) ?? ""}
              onChange={(e) => set("default_news_template_id", e.target.value ? parseInt(e.target.value) : null)}
            >
              <option value="">— shared default (news-tagged) —</option>
              {allTemplates.filter((t) => t.category !== "quote").map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name} ({t.canvas_width}×{t.canvas_height}){t.is_default ? " · shared default" : ""}
                </option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-[11px] text-text-secondary">
          Create, edit or tag templates by category in{" "}
          <a href="/templates" className="text-primary-main hover:underline">Template Designer</a>.
        </p>
      </section>

      {/* ── Section 2: Caption Criteria ────────────────── */}
      <section className="card space-y-5">
        <h2 className="text-base font-semibold text-text-primary">Caption Criteria</h2>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Tone</label>
            <input
              className="input-rect"
              value={(form.caption_tone as string) ?? ""}
              onChange={(e) => set("caption_tone", e.target.value)}
              placeholder="engaging, inspirational, humorous…"
            />
          </div>
          <div>
            <label className="label">Language</label>
            <select
              className="input-rect"
              value={(form.caption_language as string) ?? "en"}
              onChange={(e) => set("caption_language", e.target.value)}
            >
              <option value="en">English</option>
              <option value="id">Indonesian</option>
              <option value="es">Spanish</option>
              <option value="fr">French</option>
            </select>
          </div>
          <div>
            <label className="label">Max Length (chars)</label>
            <input
              className="input-rect"
              type="number"
              value={(form.caption_max_length as number) ?? 500}
              onChange={(e) => set("caption_max_length", parseInt(e.target.value))}
            />
          </div>
          <div>
            <label className="label">Hashtag Count</label>
            <input
              className="input-rect"
              type="number"
              value={(form.caption_hashtag_count as number) ?? 5}
              onChange={(e) => set("caption_hashtag_count", parseInt(e.target.value))}
            />
          </div>
        </div>

        {/* Must Include tags */}
        <div>
          <label className="label">Must Include Keywords</label>
          <div className="flex flex-wrap gap-2 mb-2">
            {((form.caption_must_include as string[]) ?? []).map((tag) => (
              <span key={tag} className="badge badge-blue gap-1">
                {tag}
                <button onClick={() => removeTag("caption_must_include", tag)}>
                  <Icon icon="solar:close-bold" width={10} />
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              className="input flex-1"
              placeholder="Add keyword, press Enter"
              value={mustIncludeInput}
              onChange={(e) => setMustIncludeInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  addTag("caption_must_include", mustIncludeInput.trim());
                  setMustIncludeInput("");
                }
              }}
            />
          </div>
        </div>

        {/* Must Avoid tags */}
        <div>
          <label className="label">Must Avoid Words</label>
          <div className="flex flex-wrap gap-2 mb-2">
            {((form.caption_must_avoid as string[]) ?? []).map((tag) => (
              <span key={tag} className="badge badge-red gap-1">
                {tag}
                <button onClick={() => removeTag("caption_must_avoid", tag)}>
                  <Icon icon="solar:close-bold" width={10} />
                </button>
              </span>
            ))}
          </div>
          <div className="flex gap-2">
            <input
              className="input flex-1"
              placeholder="Add word to avoid, press Enter"
              value={mustAvoidInput}
              onChange={(e) => setMustAvoidInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  addTag("caption_must_avoid", mustAvoidInput.trim());
                  setMustAvoidInput("");
                }
              }}
            />
          </div>
        </div>

        {/* CTA text */}
        <div>
          <label className="label">Call-to-Action Text</label>
          <input
            className="input-rect"
            value={(form.caption_cta_text as string) ?? ""}
            onChange={(e) => set("caption_cta_text", e.target.value)}
            placeholder="Follow for more! / Link in bio"
          />
        </div>

        {/* Attribution */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <input
              id="use-attribution"
              type="checkbox"
              className="w-4 h-4 rounded accent-primary-main"
              checked={(form.use_attribution as boolean) ?? true}
              onChange={(e) => set("use_attribution", e.target.checked)}
            />
            <label htmlFor="use-attribution" className="text-xs text-text-primary cursor-pointer">
              Add attribution
            </label>
          </div>
          {form.use_attribution && (
            <>
              <input
                className="input-rect flex-1"
                value={(form.caption_attribution_template as string) ?? "via @{source_username}"}
                onChange={(e) => set("caption_attribution_template", e.target.value)}
                placeholder="via @{source_username}"
              />
              <select
                className="input-rect"
                value={(form.attribution_position as string) ?? "caption_end"}
                onChange={(e) => set("attribution_position", e.target.value)}
              >
                <option value="caption_end">At end</option>
                <option value="caption_start">At start</option>
              </select>
            </>
          )}
        </div>

        {/* Custom prompt */}
        <div>
          <label className="label">Additional AI Instructions</label>
          <textarea
            className="input-rect h-24 resize-none"
            value={(form.caption_custom_prompt as string) ?? ""}
            onChange={(e) => set("caption_custom_prompt", e.target.value)}
            placeholder="e.g. Always mention our brand name. Keep it family-friendly."
          />
        </div>

        {/* Watermark text (per fanpage) */}
        <div>
          <label className="label">Watermark Text</label>
          <input
            className="input-rect"
            value={(form.watermark_text as string) ?? ""}
            onChange={(e) => set("watermark_text", e.target.value)}
            placeholder="e.g. @yourbrand — leave empty to skip watermarking"
          />
          <p className="text-[11px] text-text-secondary mt-1">
            Stamped onto post images before publishing. Leave empty to skip.
          </p>
        </div>

        {/* Watermark logo (image) — overrides the text watermark on designs */}
        <div>
          <label className="label">Watermark Logo (image)</label>
          <div className="flex items-center gap-3">
            {fp.watermark_image_url ? (
              <div className="flex items-center gap-3">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={fp.watermark_image_url} alt="watermark" className="h-12 rounded-md border border-hairline bg-[rgba(0,0,0,0.3)] p-1" />
                <button onClick={handleWatermarkDelete} disabled={wmUploading} className="btn-ghost text-error-main flex items-center gap-1">
                  <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} /> Remove
                </button>
              </div>
            ) : (
              <label className={`btn-ghost flex items-center gap-2 cursor-pointer ${wmUploading ? "opacity-50" : ""}`}>
                {wmUploading ? <Icon icon="svg-spinners:ring-resize" width={14} /> : <Icon icon="solar:upload-bold-duotone" width={14} />}
                Upload logo
                <input type="file" accept="image/*" className="hidden" disabled={wmUploading}
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) handleWatermarkUpload(f); e.currentTarget.value = ""; }} />
              </label>
            )}
          </div>
          <p className="text-[11px] text-text-secondary mt-1">
            A logo (PNG with transparency recommended) placed on every design. When set, it overrides the text watermark.
          </p>
        </div>
      </section>

      {/* ── Section 3: Publish Mode ────────────────────── */}
      <section className="card space-y-3">
        <h2 className="text-base font-semibold text-text-primary">Publish Mode</h2>
        <div className="flex gap-4">
          {(["auto", "manual_review"] as const).map((mode) => (
            <label key={mode} className="flex items-center gap-3 cursor-pointer">
              <input
                type="radio"
                name="publish-mode"
                value={mode}
                checked={form.publish_mode === mode}
                onChange={() => set("publish_mode", mode)}
                className="accent-primary-main"
              />
              <div>
                <span className="text-sm text-text-primary font-medium">
                  {mode === "auto" ? "Auto-publish" : "Manual Review"}
                </span>
                <p className="text-xs text-text-secondary">
                  {mode === "auto"
                    ? "Posts go directly to Repliz after AI caption generation."
                    : "You approve each post before it's published."}
                </p>
              </div>
            </label>
          ))}
        </div>
      </section>

      {/* ── Section: Publish Pacing (anti-bot-detection) ─── */}
      <section className="card space-y-3">
        <div>
          <h2 className="text-base font-semibold text-text-primary">Publish Pacing</h2>
          <p className="text-xs text-text-secondary mt-0.5">
            Keeps auto-publish looking like a real page admin — a sleep window
            (no posts at 3am) and a daily cap (a page that never stops posting
            is itself a bot signal). Applies to every content mode.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label className="label">Sleep window (WIB)</label>
            <div className="flex items-center gap-2">
              <select
                className="input-rect py-1.5 text-sm w-20"
                value={form.publish_sleep_start_hour ?? ""}
                onChange={(e) => set("publish_sleep_start_hour", e.target.value === "" ? null : Number(e.target.value))}
              >
                <option value="">Off</option>
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>
                ))}
              </select>
              <span className="text-xs text-text-secondary">to</span>
              <select
                className="input-rect py-1.5 text-sm w-20"
                value={form.publish_sleep_end_hour ?? ""}
                onChange={(e) => set("publish_sleep_end_hour", e.target.value === "" ? null : Number(e.target.value))}
                disabled={form.publish_sleep_start_hour === null || form.publish_sleep_start_hour === undefined}
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="label">Max posts / day</label>
            <input
              type="number"
              min={1}
              className="input-rect py-1.5 text-sm w-24"
              value={form.publish_daily_limit ?? 35}
              onChange={(e) => set("publish_daily_limit", Math.max(1, Number(e.target.value) || 1))}
            />
          </div>
        </div>
      </section>

      {/* ── Section: Mode 2 — News Content ─────────────── */}
      <section className="card space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-text-primary">Mode 2: News Content</h2>
            <p className="text-xs text-text-secondary mt-0.5">
              Scraped news → AI copywriter → image design → publish
            </p>
          </div>
          <button
            onClick={() => set("mode2_news_content_enabled", !form.mode2_news_content_enabled)}
            className={`relative w-11 h-6 rounded-full transition-colors ${
              form.mode2_news_content_enabled ? "bg-primary-main" : "bg-hairline"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                form.mode2_news_content_enabled ? "translate-x-5" : ""
              }`}
            />
          </button>
        </div>

        {form.mode2_news_content_enabled && (
          <>
            {/* News source subscriptions */}
            <div>
              <label className="label">News Sources</label>
              {allNewsSources.length === 0 ? (
                <p className="text-xs text-text-secondary">
                  No news sources configured yet — add them on the News Sources page first.
                </p>
              ) : (
                <div className="space-y-1.5">
                  {allNewsSources.map((ns) => {
                    const subscribed = ((form.news_sources as FanpageDetail["news_sources"]) ?? []).some(
                      (s) => s.id === ns.id
                    );
                    return (
                      <label
                        key={ns.id}
                        className="flex items-center gap-3 p-2.5 rounded-lg border border-hairline hover:border-primary-main/30 cursor-pointer transition-colors"
                      >
                        <input
                          type="checkbox"
                          className="w-4 h-4 rounded accent-primary-main"
                          checked={subscribed}
                          onChange={() => toggleNewsSource(ns.id, subscribed)}
                        />
                        <div className="min-w-0">
                          <p className="text-sm text-text-primary font-medium leading-tight">{ns.name}</p>
                          <p className="text-[11px] text-text-secondary truncate">{ns.category_url}</p>
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Publish mode for news content */}
            <div>
              <label className="label">News Publish Mode</label>
              <div className="flex gap-4">
                {(["manual_review", "auto"] as const).map((mode) => (
                  <label key={mode} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="mode2-publish-mode"
                      value={mode}
                      checked={form.mode2_publish_mode === mode}
                      onChange={() => set("mode2_publish_mode", mode)}
                      className="accent-primary-main"
                    />
                    <span className="text-sm text-text-primary">
                      {mode === "auto" ? "Auto-publish" : "Manual Review (open in designer)"}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            {/* Gallery niches */}
            <div>
              <label className="label">Gallery Niches (for image selection)</label>
              <p className="text-[11px] text-text-secondary mb-2">
                Any image tagged with a keyword under a subscribed niche is eligible — add a new keyword to a
                niche in <a href="/gallery" className="text-primary-main hover:underline">Gallery</a> and every
                fanpage subscribed to it picks it up automatically, no per-fanpage list to maintain.
              </p>
              {allGalleryNiches.length === 0 ? (
                <p className="text-[11px] text-text-secondary italic">No niches yet — create gallery keywords with a niche first.</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {allGalleryNiches.map((n) => {
                    const active = ((form.mode2_gallery_niches as string[]) ?? []).includes(n);
                    return (
                      <button
                        key={n}
                        onClick={() => {
                          const cur = (form.mode2_gallery_niches as string[]) ?? [];
                          set("mode2_gallery_niches", active ? cur.filter((v) => v !== n) : [...cur, n]);
                        }}
                        className={`text-xs px-3 py-1.5 rounded-full border font-medium transition-colors ${
                          active
                            ? "bg-primary-main text-white border-primary-main"
                            : "border-hairline text-text-secondary hover:border-primary-main hover:text-primary-main"
                        }`}
                      >
                        {n}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Mode 2 caption criteria */}
            <div className="border-t border-hairline pt-4 space-y-4">
              <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                News Copywriting Criteria (separate from Mode 1)
              </p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">Tone</label>
                  <input
                    className="input-rect"
                    value={(form.mode2_caption_tone as string) ?? ""}
                    onChange={(e) => set("mode2_caption_tone", e.target.value)}
                    placeholder="informative, breaking-news, casual…"
                  />
                </div>
                <div>
                  <label className="label">Language</label>
                  <select
                    className="input-rect"
                    value={(form.mode2_caption_language as string) ?? "en"}
                    onChange={(e) => set("mode2_caption_language", e.target.value)}
                  >
                    <option value="en">English</option>
                    <option value="id">Indonesian</option>
                    <option value="es">Spanish</option>
                    <option value="fr">French</option>
                  </select>
                </div>
                <div>
                  <label className="label">Caption Max Length (chars)</label>
                  <input
                    className="input-rect"
                    type="number"
                    value={(form.mode2_caption_max_length as number) ?? 500}
                    onChange={(e) => set("mode2_caption_max_length", parseInt(e.target.value))}
                  />
                </div>
                <div>
                  <label className="label">Hashtag Count</label>
                  <input
                    className="input-rect"
                    type="number"
                    value={(form.mode2_caption_hashtag_count as number) ?? 5}
                    onChange={(e) => set("mode2_caption_hashtag_count", parseInt(e.target.value))}
                  />
                </div>
                <div>
                  <label className="label">Design Title Max Chars</label>
                  <input
                    className="input-rect"
                    type="number"
                    value={(form.mode2_title_max_chars as number) ?? 80}
                    onChange={(e) => set("mode2_title_max_chars", parseInt(e.target.value))}
                  />
                </div>
                <div>
                  <label className="label">Call-to-Action Text</label>
                  <input
                    className="input-rect"
                    value={(form.mode2_caption_cta_text as string) ?? ""}
                    onChange={(e) => set("mode2_caption_cta_text", e.target.value)}
                    placeholder="Follow for more MotoGP news!"
                  />
                </div>
              </div>
              <div className="flex items-center gap-2">
                <input
                  id="mode2-attribution"
                  type="checkbox"
                  className="w-4 h-4 rounded accent-primary-main"
                  checked={(form.mode2_source_attribution as boolean) ?? true}
                  onChange={(e) => set("mode2_source_attribution", e.target.checked)}
                />
                <label htmlFor="mode2-attribution" className="text-xs text-text-primary cursor-pointer">
                  Add source attribution to caption (e.g. &quot;Source: Motosan&quot;)
                </label>
              </div>
              <div>
                <label className="label">Additional AI Instructions</label>
                <textarea
                  className="input-rect h-20 resize-none"
                  value={(form.mode2_caption_custom_prompt as string) ?? ""}
                  onChange={(e) => set("mode2_caption_custom_prompt", e.target.value)}
                  placeholder="e.g. Never speculate beyond the article facts."
                />
              </div>
            </div>

            {/* Editorial AI gate */}
            <div className="border-t border-hairline pt-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-text-primary">AI Editorial Gate</p>
                  <p className="text-xs text-text-secondary mt-0.5 max-w-md">
                    Before copywriting, 9Router web-searches to fact-check each article and judges whether it&apos;s worth posting (likely engagement) — rejected articles get no post for this fanpage. Adds 2 AI calls per article.
                  </p>
                </div>
                <button
                  onClick={() => set("mode2_editorial_gate_enabled", !form.mode2_editorial_gate_enabled)}
                  className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
                    form.mode2_editorial_gate_enabled ? "bg-primary-main" : "bg-hairline"
                  }`}
                >
                  <span
                    className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                      form.mode2_editorial_gate_enabled ? "translate-x-5" : ""
                    }`}
                  />
                </button>
              </div>
            </div>

            {/* News copy preview */}
            <div className="border-t border-hairline pt-4 space-y-3">
              <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                Copywriter Preview
              </p>
              <input
                className="input-rect"
                value={newsPreviewTitle}
                onChange={(e) => setNewsPreviewTitle(e.target.value)}
                placeholder="Paste an article title…"
              />
              <textarea
                className="input-rect h-24 resize-none"
                value={newsPreviewContent}
                onChange={(e) => setNewsPreviewContent(e.target.value)}
                placeholder="Paste article content…"
              />
              <button
                onClick={handleNewsPreview}
                disabled={newsPreviewLoading || !newsPreviewTitle || !newsPreviewContent}
                className="btn-primary"
              >
                <Icon icon="solar:magic-stick-3-bold-duotone" width={16} />
                {newsPreviewLoading ? "Generating…" : "Generate Title + Caption"}
              </button>
              {newsPreviewResult && (
                <div className="p-4 bg-bg-paper-hover rounded-lg space-y-3">
                  <div>
                    <p className="text-[11px] text-text-secondary mb-1 font-semibold uppercase tracking-wide">
                      Design Title (goes on the image)
                    </p>
                    <p className="text-sm text-text-primary font-semibold">{newsPreviewResult.title}</p>
                  </div>
                  <div>
                    <p className="text-[11px] text-text-secondary mb-1 font-semibold uppercase tracking-wide">
                      FB Caption
                    </p>
                    <p className="text-sm text-text-primary whitespace-pre-wrap">{newsPreviewResult.caption}</p>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </section>

      {/* ── Section: Mode 3 — IG Recreate ─────────────── */}
      <section className="card space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-text-primary">Mode 3: IG Recreate</h2>
            <p className="text-xs text-text-secondary mt-0.5">
              Classify each scraped IG post (9Router vision) → rebuild it on a quote/news template
            </p>
          </div>
          <button
            onClick={() => set("ig_recreate_enabled", !form.ig_recreate_enabled)}
            className={`relative w-11 h-6 rounded-full transition-colors ${
              form.ig_recreate_enabled ? "bg-primary-main" : "bg-hairline"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                form.ig_recreate_enabled ? "translate-x-5" : ""
              }`}
            />
          </button>
        </div>

        {form.ig_recreate_enabled && (
          <>
            <p className="text-[11px] text-text-secondary">
              Each IG post image is classified as <strong>quote</strong>, <strong>news</strong>, or{" "}
              <strong>other</strong>. Quotes and news are rebuilt using the fanpage&apos;s Quote/News Template
              (set above in <strong>Design Templates</strong>) — using the IG image as the photo; news headlines
              are AI-rewritten; anything else is skipped. Uses the fanpage&apos;s Mode 1 caption criteria for the
              Facebook post text.
            </p>

            {/* Smart layout (opt-in, experimental) */}
            <div className="border-t border-hairline pt-4">
              <div className="flex items-center justify-between">
                <div>
                  <label className="label mb-0">Smart layout (2-photo / split) <span className="text-[10px] text-amber-500">experimental</span></label>
                  <p className="text-[11px] text-text-secondary mt-0.5">
                    AI decides 1 vs 2 people → single+inset or split, picks face/action photos, face-aware crop.
                  </p>
                </div>
                <button
                  onClick={() => set("ig_recreate_smart_layout", !form.ig_recreate_smart_layout)}
                  className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
                    form.ig_recreate_smart_layout ? "bg-primary-main" : "bg-hairline"
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
                <div className="mt-3">
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
                  <p className="text-[11px] text-text-secondary mt-1">
                    Used when a news headline is about two people. Should be a two-slot split template.
                  </p>
                </div>
              )}
            </div>
          </>
        )}
      </section>

      {/* ── Section: Image Framing (news + IG recreate) ── */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">Image Framing</h2>
        <div className="flex items-center justify-between">
          <div>
            <label className="label mb-0">Expand image to fill (auto)</label>
            <p className="text-[11px] text-text-secondary mt-0.5">
              When a photo would be heavily cropped, fill the frame instead — mirror-extend
              action shots, blurred fit for close-up faces. Photos that already fit are untouched.
              Applies to news &amp; IG-recreate designs.
            </p>
          </div>
          <button
            onClick={() => set("design_expand", !form.design_expand)}
            className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${
              form.design_expand ? "bg-primary-main" : "bg-hairline"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                form.design_expand ? "translate-x-5" : ""
              }`}
            />
          </button>
        </div>
      </section>

      {/* ── Section 4: Live Caption Preview ───────────── */}
      <section className="card space-y-4">
        <h2 className="text-base font-semibold text-text-primary">Caption Preview</h2>
        <p className="text-xs text-text-secondary">
          Test the AI caption with an example original caption.
        </p>
        <div>
          <label className="label">Source IG Username</label>
          <input
            className="input-rect"
            value={previewSrc}
            onChange={(e) => setPreviewSrc(e.target.value)}
            placeholder="e.g. natgeo"
          />
        </div>
        <div>
          <label className="label">Original Caption (from IG)</label>
          <textarea
            className="input-rect h-24 resize-none"
            value={previewOrig}
            onChange={(e) => setPreviewOrig(e.target.value)}
            placeholder="Paste an Instagram caption here…"
          />
        </div>
        <button
          onClick={handlePreview}
          disabled={previewLoading || !previewSrc || !previewOrig}
          className="btn-primary"
        >
          <Icon icon="solar:magic-stick-3-bold-duotone" width={16} />
          {previewLoading ? "Generating…" : "Generate Preview"}
        </button>
        {previewResult && (
          <div className="p-4 bg-bg-paper-hover rounded-lg">
            <p className="text-[11px] text-text-secondary mb-1 font-semibold uppercase tracking-wide">
              Generated Caption
            </p>
            <p className="text-sm text-text-primary whitespace-pre-wrap">{previewResult}</p>
          </div>
        )}
      </section>

      {/* Save */}
      <div className="flex gap-3">
        <button onClick={handleSave} disabled={saving} className="btn-primary">
          {saving ? "Saving…" : "Save Changes"}
        </button>
        <button onClick={() => router.back()} className="btn-secondary">
          Cancel
        </button>
      </div>
    </div>
  );
}
