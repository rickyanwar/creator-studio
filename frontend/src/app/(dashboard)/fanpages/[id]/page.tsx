"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import useSWR from "swr";
import {
  getFanpage,
  updateFanpage,
  addIGSource,
  removeIGSourceByUsername,
  previewCaption,
} from "@/lib/api";
import type { FanpageDetail } from "@/lib/types";
import { Icon } from "@iconify/react";

const fetcher = (id: number) => getFanpage(id).then((r) => r.data as FanpageDetail);

export default function FanpageEditPage() {
  const { id } = useParams<{ id: string }>();
  const fanpageId = parseInt(id);
  const router = useRouter();
  const { data: fp, mutate } = useSWR(`fanpage-${fanpageId}`, () => fetcher(fanpageId));

  const [form, setForm] = useState<Partial<FanpageDetail>>({});
  const [saving, setSaving] = useState(false);
  const [newSource, setNewSource] = useState("");
  const [previewSrc, setPreviewSrc] = useState("");
  const [previewOrig, setPreviewOrig] = useState("");
  const [previewResult, setPreviewResult] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [mustIncludeInput, setMustIncludeInput] = useState("");
  const [mustAvoidInput, setMustAvoidInput] = useState("");

  useEffect(() => {
    if (fp) setForm({ ...fp });
  }, [fp]);

  function set(key: string, value: unknown) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      await updateFanpage(fanpageId, form);
      mutate();
    } finally {
      setSaving(false);
    }
  }

  async function handleAddSource() {
    if (!newSource.trim()) return;
    await addIGSource(fanpageId, newSource.trim());
    setNewSource("");
    mutate();
  }

  async function handleRemoveSource(username: string) {
    await removeIGSourceByUsername(fanpageId, username);
    mutate();
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

        <div className="flex flex-wrap gap-2">
          {(fp.ig_source_usernames ?? []).map((uname) => (
            <span
              key={uname}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-bg-paper-hover text-text-primary text-xs rounded-full"
            >
              @{uname}
              <button
                onClick={() => handleRemoveSource(uname)}
                className="ml-1 w-4 h-4 flex items-center justify-center rounded-full bg-red-500 hover:bg-red-600 text-white text-[10px] font-bold leading-none transition-colors flex-shrink-0"
                title={`Remove @${uname}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>

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
