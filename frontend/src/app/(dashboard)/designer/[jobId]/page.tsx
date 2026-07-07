"use client";

/**
 * Review-mode job designer (spec Fitur 2 §D):
 * loads the fanpage template preloaded with the AI headline and a selected
 * image; the admin edits freely, then Export saves the PNG and moves the
 * job to pending_publish.
 */

import { useParams, useRouter } from "next/navigation";
import { useRef, useState, useEffect } from "react";
import useSWR from "swr";
import dynamic from "next/dynamic";
import { Icon } from "@iconify/react";
import { getDesignPayload, uploadDesignImage, proxyImageUrl } from "@/lib/api";
import type { EditorApi } from "@/components/designer/TemplateEditor";

const TemplateEditor = dynamic(() => import("@/components/designer/TemplateEditor"), { ssr: false });

type DesignPayload = {
  job_id: number;
  status: string;
  design_title: string | null;
  caption: string | null;
  design_image_url: string | null;
  article_title: string | null;
  article_url: string | null;
  fanpage_name: string | null;
  template: {
    id: number;
    name: string;
    canvas_width: number;
    canvas_height: number;
    template_json: Record<string, unknown> | null;
  } | null;
  image_candidates: { public_url: string; keyword: string; is_used: boolean }[];
};

export default function JobDesignerPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const id = parseInt(jobId);
  const router = useRouter();
  const apiRef = useRef<EditorApi | null>(null);
  // state (not just the ref) so the preload effect re-runs when the editor
  // mounts after the payload has already arrived
  const [editorApi, setEditorApi] = useState<EditorApi | null>(null);
  const [exporting, setExporting] = useState(false);
  const [preloaded, setPreloaded] = useState(false);
  const [imageLoading, setImageLoading] = useState(false);

  const { data: payload } = useSWR<DesignPayload>(
    `design-payload-${id}`,
    () => getDesignPayload(id).then((r) => r.data as DesignPayload),
    { revalidateOnFocus: false }
  );

  // Preload title + first image once the editor and payload are both ready
  useEffect(() => {
    const api = editorApi;
    if (!payload || !api || preloaded) return;
    setPreloaded(true);
    if (payload.design_title) api.injectTitle(payload.design_title);
    const first = payload.image_candidates[0];
    if (first) {
      setImageLoading(true);
      proxyImageUrl(first.public_url)
        .then((src) => api.injectImage(src))
        .catch(() => {})
        .finally(() => setImageLoading(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload, editorApi, preloaded]);

  async function pickImage(url: string) {
    if (!apiRef.current) return;
    setImageLoading(true);
    try {
      const src = await proxyImageUrl(url);
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

  if (!payload) return <div className="text-sm text-ink-48">Loading…</div>;

  if (!payload.template || !payload.template.template_json) {
    return (
      <div className="card p-10 text-center text-ink-48 space-y-3">
        <Icon icon="solar:palette-bold-duotone" width={40} className="mx-auto opacity-40" />
        <p className="text-sm">
          No design template configured for <strong>{payload.fanpage_name}</strong>.
        </p>
        <button onClick={() => router.push("/templates")} className="btn btn-primary">Create a Template</button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-4 min-w-0">
          <button
            onClick={() => router.back()}
            className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-parchment transition-colors text-ink-48 hover:text-ink shrink-0"
          >
            <Icon icon="solar:alt-arrow-left-bold-duotone" width={20} />
          </button>
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-ink truncate">Design: {payload.design_title ?? payload.article_title}</h1>
            <p className="text-[11px] text-ink-48 truncate">
              {payload.fanpage_name} · template &quot;{payload.template.name}&quot;
              {payload.article_url && (
                <> · <a href={payload.article_url} target="_blank" rel="noreferrer" className="text-primary-main hover:underline">source article</a></>
              )}
            </p>
          </div>
        </div>
        <button onClick={handleExport} disabled={exporting} className="btn btn-primary flex items-center gap-2 shrink-0">
          {exporting ? <Icon icon="svg-spinners:ring-resize" width={14} /> : <Icon icon="solar:export-bold-duotone" width={14} />}
          Export PNG → Ready to Publish
        </button>
      </div>

      <div className="card p-6">
        <TemplateEditor
          width={payload.template.canvas_width}
          height={payload.template.canvas_height}
          initialJson={payload.template.template_json}
          onReady={(api) => { apiRef.current = api; setEditorApi(api); }}
        />
      </div>

      {/* Image candidates */}
      <div className="card p-5 space-y-3">
        <p className="text-xs font-semibold text-ink-80 uppercase tracking-wide flex items-center gap-2">
          <Icon icon="solar:gallery-wide-bold-duotone" width={14} />
          Pick a different image
          {imageLoading && <Icon icon="svg-spinners:ring-resize" width={12} className="text-primary-main" />}
        </p>
        {payload.image_candidates.length === 0 ? (
          <p className="text-xs text-ink-48">No gallery images match this fanpage&apos;s keywords — upload some in the Gallery page.</p>
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-1">
            {payload.image_candidates.map((c) => (
              <button
                key={c.public_url}
                onClick={() => pickImage(c.public_url)}
                className="shrink-0 rounded-lg overflow-hidden border border-hairline hover:border-primary-main transition-colors relative group"
                title={c.keyword}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={c.public_url} alt={c.keyword} className="w-24 h-24 object-cover" loading="lazy" />
                <span className="absolute bottom-0 inset-x-0 bg-black/50 text-white text-[9px] px-1 py-0.5 truncate">{c.keyword}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Caption preview */}
      {payload.caption && (
        <div className="card p-5">
          <p className="text-xs font-semibold text-ink-80 uppercase tracking-wide mb-2">FB Caption (from copywriter)</p>
          <p className="text-sm text-ink whitespace-pre-wrap">{payload.caption}</p>
        </div>
      )}
    </div>
  );
}
