"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useEffect, useRef } from "react";
import useSWR from "swr";
import {
  getFanpage,
  updateFanpage,
  addIGSource,
  removeIGSourceByUsername,
  previewCaption,
  updateIGSource,
} from "@/lib/api";
import type { FanpageDetail, IGSourceRef } from "@/lib/types";
import { Icon } from "@iconify/react";

const fetcher = (id: number) => getFanpage(id).then((r) => r.data as FanpageDetail);

const MAX_ALBUM = 10;

function IGSourceCard({
  source,
  onRemove,
  onAlbumSaved,
}: {
  source: IGSourceRef;
  onRemove: () => void;
  onAlbumSaved: () => void;
}) {
  const [indices, setIndices] = useState<number[]>(source.album_image_indices ?? [1]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [editEnabled, setEditEnabled] = useState(source.image_edit_enabled ?? false);
  const [editPrompt, setEditPrompt] = useState(source.image_edit_custom_prompt ?? "");
  const [editSaving, setEditSaving] = useState(false);

  useEffect(() => {
    setIndices(source.album_image_indices ?? [1]);
  }, [source.album_image_indices?.join(",")]);

  useEffect(() => {
    setEditEnabled(source.image_edit_enabled ?? false);
    setEditPrompt(source.image_edit_custom_prompt ?? "");
  }, [source.image_edit_enabled, source.image_edit_custom_prompt]);

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

  async function toggleImageEdit() {
    const next = !editEnabled;
    setEditEnabled(next);
    setEditSaving(true);
    try {
      await updateIGSource(source.id, { image_edit_enabled: next });
      onAlbumSaved();
    } finally {
      setEditSaving(false);
    }
  }

  async function saveEditPrompt() {
    setEditSaving(true);
    try {
      await updateIGSource(source.id, { image_edit_custom_prompt: editPrompt });
      onAlbumSaved();
    } finally {
      setEditSaving(false);
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

      {/* AI image edit (Nano Banana) */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Icon icon="solar:magic-stick-3-bold-duotone" width={13} className="text-text-secondary" />
            <span className="text-[11px] font-medium text-text-secondary uppercase tracking-wide">
              Auto-edit image (remove watermark)
            </span>
          </div>
          <button
            onClick={toggleImageEdit}
            disabled={editSaving}
            className={`relative w-9 h-5 rounded-full transition-colors disabled:opacity-48 ${
              editEnabled ? "bg-primary-main" : "bg-hairline"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                editEnabled ? "translate-x-4" : ""
              }`}
            />
          </button>
        </div>
        {editEnabled && (
          <textarea
            className="input-rect h-16 resize-none text-xs"
            value={editPrompt}
            onChange={(e) => setEditPrompt(e.target.value)}
            onBlur={saveEditPrompt}
            placeholder="Optional extra instructions for the image edit (e.g. replace account name)"
          />
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
  const [previewSrc, setPreviewSrc] = useState("");
  const [previewOrig, setPreviewOrig] = useState("");
  const [previewResult, setPreviewResult] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [mustIncludeInput, setMustIncludeInput] = useState("");
  const [mustAvoidInput, setMustAvoidInput] = useState("");

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
            Stamped onto images (via Nano Banana) for posts from sources with image editing enabled.
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
