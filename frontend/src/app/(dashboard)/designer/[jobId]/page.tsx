"use client";

import { useParams, useRouter } from "next/navigation";
import { useRef, useState, useEffect } from "react";
import Link from "next/link";
import useSWR from "swr";
import dynamic from "next/dynamic";
import { Icon } from "@iconify/react";
import {
  getDesignPayload,
  uploadDesignImage,
  proxyImageUrl,
  skipJob,
  listTemplates,
} from "@/lib/api";
import { saveDesignDraft, deleteDesignDraft } from "@/lib/designerApi";
import type { EditorApi } from "@/components/designer/TemplateEditor";
import { StatusBadge } from "@/components/ui/StatusBadge";

const TemplateEditor = dynamic(
  () => import("@/components/designer/TemplateEditor"),
  { ssr: false }
);

type DesignPayload = {
  job_id: number;
  status: string;
  design_title: string | null;
  design_subtitle: string | null;
  design_caption: string | null;
  caption: string | null;
  design_image_url: string | null;
  article_title: string | null;
  article_url: string | null;
  fanpage_id: number;
  fanpage_name: string | null;
  template: {
    id: number;
    name: string;
    canvas_width: number;
    canvas_height: number;
    template_json: Record<string, unknown> | null;
    category?: string | null;
  } | null;
  image_candidates: { public_url: string; keyword: string; is_used: boolean }[];
  design_draft_json?: Record<string, unknown> | null;
  saved_at?: string | null;
  design_draft_saved_at?: string | null;
};

type TemplateItem = {
  id: number;
  name: string;
  canvas_width: number;
  canvas_height: number;
  template_json?: Record<string, unknown> | null;
  category?: string | null;
  style?: string | null;
  is_default?: boolean;
};

function formatDraftTime(isoString?: string | null): string {
  if (!isoString) return "";
  const d = new Date(isoString);
  const hours = String(d.getHours()).padStart(2, "0");
  const minutes = String(d.getMinutes()).padStart(2, "0");
  return `${hours}:${minutes}`;
}

export default function JobDesignerPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const id = parseInt(jobId);
  const router = useRouter();
  const apiRef = useRef<EditorApi | null>(null);
  const [editorApi, setEditorApi] = useState<EditorApi | null>(null);
  const [exporting, setExporting] = useState(false);
  const [preloaded, setPreloaded] = useState(false);
  const [imageLoading, setImageLoading] = useState(false);
  const [selectedPhotoIdx, setSelectedPhotoIdx] = useState<number>(0);
  const [currentImageSrc, setCurrentImageSrc] = useState<string | null>(null);

  const [savingDraft, setSavingDraft] = useState(false);
  const [savedText, setSavedText] = useState<string | null>(null);
  const [draftDismissed, setDraftDismissed] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [currentTemplateId, setCurrentTemplateId] = useState<number | null>(null);

  const { data: payload } = useSWR<DesignPayload>(
    `design-payload-${id}`,
    () => getDesignPayload(id).then((r) => r.data as DesignPayload),
    { revalidateOnFocus: false }
  );

  const { data: brandTemplates } = useSWR<TemplateItem[]>(
    payload?.fanpage_id ? `/templates?fanpage_id=${payload.fanpage_id}` : null,
    () => listTemplates(payload?.fanpage_id).then((r) => r.data as TemplateItem[]),
    { revalidateOnFocus: false }
  );

  // Preload title + first image once editor and payload are ready
  useEffect(() => {
    const api = editorApi;
    if (!payload || !api || preloaded) return;
    setPreloaded(true);
    if (payload.design_title) api.injectTitle(payload.design_title);
    if (payload.design_subtitle) api.injectSubtitle(payload.design_subtitle);
    if (payload.design_caption) api.injectCaption(payload.design_caption);
    const first = payload.image_candidates[0];
    if (first) {
      setImageLoading(true);
      proxyImageUrl(first.public_url)
        .then((src) => {
          setCurrentImageSrc(src);
          return api.injectImage(src);
        })
        .catch(() => {})
        .finally(() => setImageLoading(false));
    }
  }, [payload, editorApi, preloaded]);

  async function pickImage(url: string, index: number) {
    if (!apiRef.current) return;
    setSelectedPhotoIdx(index);
    setImageLoading(true);
    try {
      const src = await proxyImageUrl(url);
      setCurrentImageSrc(src);
      await apiRef.current.injectImage(src);
    } catch (e) {
      alert(`Could not load image: ${e instanceof Error ? e.message : "unknown error"}`);
    } finally {
      setImageLoading(false);
    }
  }

  async function handleExport() {
    if (!apiRef.current) return;
    setExporting(true);
    try {
      const blob = await apiRef.current.exportPng();
      await uploadDesignImage(id, blob);
      alert("Design exported — job is now ready to publish.");
      router.push("/queue");
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      alert(`Export failed: ${err.response?.data?.detail ?? "unknown error"}`);
    } finally {
      setExporting(false);
    }
  }

  async function handleSaveDraft() {
    if (!editorApi) return;
    setSavingDraft(true);
    try {
      const draft = editorApi.getDraft();
      const res = await saveDesignDraft(id, draft);
      const timeStr = res.saved_at ? formatDraftTime(res.saved_at) : formatDraftTime(new Date().toISOString());
      setSavedText(`Draft saved ${timeStr}`);
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      alert(`Save draft failed: ${err.response?.data?.detail ?? "unknown error"}`);
    } finally {
      setSavingDraft(false);
    }
  }

  async function handleRestoreDraft() {
    if (!editorApi || !payload?.design_draft_json) return;
    try {
      await editorApi.loadDraft(payload.design_draft_json);
      setDraftDismissed(true);
      const draftTime = payload.saved_at || payload.design_draft_saved_at;
      if (draftTime) {
        setSavedText(`Draft saved ${formatDraftTime(draftTime)}`);
      }
    } catch {
      alert("Could not restore draft");
    }
  }

  async function handleDiscardDraft() {
    try {
      await deleteDesignDraft(id);
      setDraftDismissed(true);
      setSavedText(null);
    } catch {
      alert("Could not discard draft");
    }
  }

  async function handleSkipPost() {
    const ok = window.confirm("Skip this post? It will be marked skipped and moved out of queue.");
    if (!ok) return;
    setSkipping(true);
    try {
      await skipJob(id);
      router.push("/queue");
    } catch (e) {
      const err = e as { response?: { data?: { detail?: string } } };
      alert(`Skip failed: ${err.response?.data?.detail ?? "unknown error"}`);
      setSkipping(false);
    }
  }

  async function handleSwitchTemplate(newTemplateId: number) {
    if (!editorApi) return;
    const tpl = (brandTemplates || []).find((t) => t.id === newTemplateId);
    if (!tpl || !tpl.template_json) return;
    setCurrentTemplateId(tpl.id);
    await editorApi.loadDraft(tpl.template_json);
    if (payload?.design_title) editorApi.injectTitle(payload.design_title);
    if (payload?.design_subtitle) editorApi.injectSubtitle(payload.design_subtitle);
    if (payload?.design_caption) editorApi.injectCaption(payload.design_caption);
    if (currentImageSrc) {
      await editorApi.injectImage(currentImageSrc);
    } else if (payload?.image_candidates[selectedPhotoIdx]) {
      try {
        const src = await proxyImageUrl(payload.image_candidates[selectedPhotoIdx].public_url);
        setCurrentImageSrc(src);
        await editorApi.injectImage(src);
      } catch {}
    }
  }

  if (!payload) {
    return (
      <div className="rounded-xl border border-line bg-surface p-12 text-center text-text2 text-sm animate-pulse">
        Loading designer…
      </div>
    );
  }

  if (!payload.template || !payload.template.template_json) {
    return (
      <div className="rounded-xl border border-line bg-surface p-10 text-center space-y-4">
        <Icon
          icon="solar:palette-bold-duotone"
          width={40}
          className="mx-auto text-text2 opacity-40"
        />
        <div>
          <h2 className="text-base font-semibold text-text">No template configured</h2>
          <p className="text-sm text-text2 mt-1">
            No design template configured for <strong>{payload.fanpage_name}</strong>.
          </p>
        </div>
        <Link
          href="/templates"
          className="inline-flex items-center gap-1.5 h-10 px-5 rounded-lg bg-pri text-pri-fg text-sm font-semibold hover:opacity-90 transition-opacity"
        >
          Create a Template
        </Link>
      </div>
    );
  }

  const jobCategory = payload.template.category || "news";
  const availableTemplates = (brandTemplates || []).filter(
    (t) => (t.category || "news") === jobCategory || t.id === (currentTemplateId ?? payload.template?.id)
  );

  const activeTemplateName =
    (brandTemplates || []).find((t) => t.id === (currentTemplateId ?? payload.template?.id))?.name ??
    payload.template.name;

  return (
    <div className="space-y-4">
      {/* Top Header */}
      <header className="flex flex-wrap items-center justify-between gap-4 p-4 rounded-xl bg-surface border border-line">
        <div className="flex items-center gap-3 min-w-0">
          <Link
            href="/queue"
            className="inline-flex items-center gap-1.5 h-9 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold transition-colors shrink-0"
          >
            <Icon icon="solar:alt-arrow-left-bold" width={14} />
            Queue
          </Link>

          <div className="min-w-0">
            <h1 className="text-base font-bold text-text truncate">
              {payload.design_title ?? payload.article_title ?? "Card design"}
            </h1>
            <p className="text-xs text-text2 truncate flex items-center gap-1.5">
              <span>{payload.fanpage_name}</span>
              <span>·</span>
              <span>Template &quot;{activeTemplateName}&quot;</span>
              {payload.article_url && (
                <>
                  <span>·</span>
                  <a
                    href={payload.article_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-pri hover:underline"
                  >
                    source article
                  </a>
                </>
              )}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 shrink-0 flex-wrap">
          <StatusBadge status={payload.status} />

          {savedText && (
            <span className="text-xs text-text2 font-medium">
              {savedText}
            </span>
          )}

          <button
            type="button"
            disabled={skipping}
            onClick={handleSkipPost}
            className="inline-flex items-center gap-1.5 h-9 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold transition-colors"
          >
            {skipping ? (
              <Icon icon="svg-spinners:ring-resize" width={14} />
            ) : (
              <Icon icon="solar:forward-bold" width={14} />
            )}
            Skip post
          </button>

          <button
            type="button"
            disabled={savingDraft}
            onClick={handleSaveDraft}
            className="inline-flex items-center gap-1.5 h-9 px-3 rounded-lg border border-line bg-surface hover:bg-surface2 text-text text-xs font-semibold transition-colors"
          >
            {savingDraft ? (
              <Icon icon="svg-spinners:ring-resize" width={14} />
            ) : (
              <Icon icon="solar:diskette-bold" width={14} />
            )}
            Save draft
          </button>

          <button
            type="button"
            disabled={exporting}
            onClick={handleExport}
            className="inline-flex items-center gap-1.5 h-9 px-4 rounded-lg bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 transition-opacity"
          >
            {exporting ? (
              <Icon icon="svg-spinners:ring-resize" width={14} />
            ) : (
              <Icon icon="solar:check-circle-bold-duotone" width={14} />
            )}
            Finish · ready to publish
          </button>
        </div>
      </header>

      {/* Restore draft banner */}
      {!draftDismissed && Boolean(payload.design_draft_json) && (
        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 rounded-xl bg-warn-soft text-warn border border-line text-xs font-medium">
          <div className="flex items-center gap-2">
            <Icon icon="solar:history-bold" width={16} className="shrink-0" />
            <span>
              Restore your draft from {formatDraftTime(payload.saved_at || payload.design_draft_saved_at)}?
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleRestoreDraft}
              className="h-8 px-3 rounded-lg bg-surface text-text border border-line font-semibold hover:bg-surface2 transition-colors"
            >
              Restore
            </button>
            <button
              type="button"
              onClick={handleDiscardDraft}
              className="h-8 px-3 rounded-lg bg-transparent text-text2 hover:text-bad font-semibold transition-colors"
            >
              Discard
            </button>
          </div>
        </div>
      )}

      {/* Info note */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 rounded-xl bg-info-soft text-info border border-line text-xs">
        <div className="flex items-center gap-2">
          <Icon icon="solar:info-circle-bold-duotone" width={16} className="shrink-0" />
          <span>The Designer opens only for News and Instagram recreate posts.</span>
        </div>
        <span className="font-medium text-[11px]">
          Finish uploads this card as the post image and sets the post to Ready to publish.
        </span>
      </div>

      {/* Workspace Grid */}
      <div className="flex flex-col xl:flex-row gap-4 items-start">
        {/* Left Aside: Photos */}
        <aside
          aria-label="Photo"
          className="w-full xl:w-60 shrink-0 rounded-xl bg-surface border border-line p-4 space-y-3"
        >
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-bold text-text uppercase tracking-wide">Photo</h2>
            <span className="text-[11px] text-text2 font-medium">
              {payload.image_candidates.length} gallery match
              {payload.image_candidates.length !== 1 ? "es" : ""}
            </span>
          </div>

          {payload.image_candidates.length === 0 ? (
            <p className="text-xs text-text2">
              No gallery images match this fanpage&apos;s keywords — upload some in the Gallery page.
            </p>
          ) : (
            <div className="grid grid-cols-3 xl:grid-cols-2 gap-2">
              {payload.image_candidates.map((c, idx) => (
                <button
                  key={c.public_url}
                  type="button"
                  onClick={() => pickImage(c.public_url, idx)}
                  className={`aspect-square rounded-lg overflow-hidden border relative group transition-all text-left ${
                    selectedPhotoIdx === idx
                      ? "border-pri ring-2 ring-pri/30"
                      : "border-line hover:border-pri"
                  }`}
                  title={c.keyword}
                >
                  <img
                    src={c.public_url}
                    alt={c.keyword}
                    className="w-full h-full object-cover"
                    loading="lazy"
                  />
                  <span className="absolute bottom-1 left-1 right-1 px-1 py-0.5 rounded bg-surface/80 backdrop-blur-sm text-text text-[9px] truncate">
                    {c.keyword}
                  </span>
                </button>
              ))}
            </div>
          )}

          <p className="text-[11px] text-text2 leading-relaxed">
            Gallery photos that match this article&apos;s keywords. The identity check
            runs only on AI-made images, not on gallery photos.
          </p>
        </aside>

        {/* Center: Canvas Editor */}
        <section
          aria-label="Canvas"
          className="flex-1 min-w-0 w-full rounded-xl bg-surface border border-line p-4 flex flex-col items-center gap-3 overflow-x-auto"
        >
          <TemplateEditor
            width={payload.template.canvas_width}
            height={payload.template.canvas_height}
            initialJson={payload.template.template_json}
            onReady={(api) => {
              apiRef.current = api;
              setEditorApi(api);
            }}
          />

          <span className="text-[11px] text-text2">
            {payload.template.canvas_width} × {payload.template.canvas_height} · what the renderer will output
          </span>
        </section>

        {/* Right Aside: Text & Caption */}
        <aside
          aria-label="Text and Caption"
          className="w-full xl:w-72 shrink-0 rounded-xl bg-surface border border-line p-4 space-y-4"
        >
          <div>
            <h2 className="text-xs font-bold text-text uppercase tracking-wide mb-2">
              Article &amp; Copy
            </h2>
            <div className="space-y-2 text-xs">
              <div>
                <span className="text-text2 block text-[11px] font-medium">Headline</span>
                <p className="text-text font-semibold mt-0.5 leading-snug">
                  {payload.design_title ?? "—"}
                </p>
              </div>

              {payload.design_subtitle && (
                <div>
                  <span className="text-text2 block text-[11px] font-medium">Subtitle</span>
                  <p className="text-text mt-0.5 leading-snug">{payload.design_subtitle}</p>
                </div>
              )}
            </div>
          </div>

          {payload.caption && (
            <div className="border-t border-line pt-3">
              <span className="text-text2 block text-[11px] font-medium mb-1">
                Facebook Caption
              </span>
              <p className="text-xs text-text leading-relaxed whitespace-pre-wrap bg-surface2 p-2.5 rounded-lg border border-line">
                {payload.caption}
              </p>
            </div>
          )}

          {/* Template switcher */}
          <div className="border-t border-line pt-3">
            <label className="text-text2 block text-[11px] font-medium mb-1.5">
              Template
            </label>
            <select
              value={currentTemplateId ?? payload.template.id}
              onChange={(e) => handleSwitchTemplate(Number(e.target.value))}
              className="w-full h-9 px-2.5 rounded-lg border border-line bg-surface text-text text-xs focus:outline-none focus:border-pri"
            >
              {availableTemplates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>

          <p className="text-[11px] text-text2 leading-relaxed border-t border-line pt-3">
            Colors and branding follow the brand&apos;s look. Change them in Brand settings.
          </p>
        </aside>
      </div>
    </div>
  );
}
