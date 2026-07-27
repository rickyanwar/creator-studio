"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useRef } from "react";
import useSWR from "swr";
import dynamic from "next/dynamic";
import { Icon } from "@iconify/react";
import { getTemplate, updateTemplate } from "@/lib/api";
import type { EditorApi } from "@/components/designer/TemplateEditor";
import TemplateThumbnail from "@/components/designer/TemplateThumbnail";

const TemplateEditor = dynamic(() => import("@/components/designer/TemplateEditor"), { ssr: false });

type TemplateDetail = {
  id: number;
  name: string;
  canvas_width: number;
  canvas_height: number;
  is_default: boolean;
  template_json: Record<string, unknown> | null;
};

export default function TemplateEditorPage() {
  const { id } = useParams<{ id: string }>();
  const templateId = parseInt(id);
  const router = useRouter();
  const apiRef = useRef<EditorApi | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [previewJson, setPreviewJson] = useState<Record<string, unknown> | null>(null);

  const { data: template } = useSWR<TemplateDetail>(
    `template-${templateId}`,
    () => getTemplate(templateId).then((r) => r.data as TemplateDetail),
    { revalidateOnFocus: false }
  );

  async function handleSave() {
    if (!apiRef.current) return;
    // Reset the WYSIWYG sample content back to placeholders so we save the clean
    // template, then restore the preview.
    apiRef.current.hideSample();
    const { json, placeholderConfig } = apiRef.current.toTemplateJson();
    apiRef.current.showSample();
    const cfg = placeholderConfig as { has_title?: boolean; has_image_slot?: boolean };
    if (!cfg.has_title || !cfg.has_image_slot) {
      if (!confirm("Template is missing a Headline and/or Image Slot placeholder — auto-render needs both. Save anyway?")) return;
    }
    setSaving(true);
    try {
      await updateTemplate(templateId, { template_json: json, placeholder_config: placeholderConfig });
      setSavedAt(new Date().toLocaleTimeString());
    } catch {
      alert("Save failed. Please try again.");
    } finally {
      setSaving(false);
    }
  }

  if (!template) return <div className="text-sm text-ink-48">Loading…</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <button
            onClick={() => router.push("/templates")}
            className="w-9 h-9 flex items-center justify-center rounded-full hover:bg-parchment transition-colors text-ink-48 hover:text-ink"
          >
            <Icon icon="solar:alt-arrow-left-bold-duotone" width={20} />
          </button>
          <div>
            <h1 className="text-xl font-bold text-ink">{template.name}</h1>
            <p className="text-[11px] text-ink-48">
              {template.canvas_width}×{template.canvas_height} template
              {savedAt && <span className="text-emerald-600 font-semibold"> · saved {savedAt}</span>}
            </p>
          </div>
        </div>
        <button onClick={handleSave} disabled={saving} className="btn btn-primary flex items-center gap-2">
          {saving ? <Icon icon="svg-spinners:ring-resize" width={14} /> : <Icon icon="solar:diskette-bold-duotone" width={14} />}
          Save Template
        </button>
      </div>

      {/* Editable canvas (raw placeholders — you position the layers here) */}
      <div className="card p-6">
        <TemplateEditor
          width={template.canvas_width}
          height={template.canvas_height}
          initialJson={template.template_json}
          sampleOnLoad
          onReady={(api) => { apiRef.current = api; }}
        />
      </div>

      {/* Live preview BELOW the editor — how the template ACTUALLY renders
          (sample headline + highlight + subtitle + scrim + auto-fit) = publish. */}
      <div className="card p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-ink flex items-center gap-1.5">
              <Icon icon="solar:eye-bold-duotone" width={16} /> Live preview — real result
            </h2>
            <p className="text-[11px] text-ink-48 mt-0.5">
              How this template renders with news content (highlight, subtitle, scrim, auto-fit). Sample text/photo — real ones vary.
            </p>
          </div>
          <button
            onClick={() => {
              if (!apiRef.current) return;
              // Snapshot the CLEAN canvas, not the sample-injected one — otherwise
              // the thumbnail re-applies sample content on top of already-fitted
              // sample text, compounding the auto-fit sizing on every click.
              apiRef.current.hideSample();
              const json = apiRef.current.toTemplateJson().json;
              apiRef.current.showSample();
              setPreviewJson(json);
            }}
            className="btn btn-secondary text-xs flex items-center gap-1.5 shrink-0"
            title="Re-render the preview from your current edits"
          >
            <Icon icon="solar:refresh-bold-duotone" width={14} /> Refresh preview
          </button>
        </div>
        <div className="flex justify-center pt-1">
          <TemplateThumbnail
            json={previewJson ?? template.template_json}
            width={template.canvas_width}
            height={template.canvas_height}
            className="w-auto max-h-[70vh] rounded-lg border border-hairline shadow-sm"
          />
        </div>
      </div>
    </div>
  );
}
