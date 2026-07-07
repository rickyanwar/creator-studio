"use client";

import { useParams, useRouter } from "next/navigation";
import { useState, useRef } from "react";
import useSWR from "swr";
import dynamic from "next/dynamic";
import { Icon } from "@iconify/react";
import { getTemplate, updateTemplate } from "@/lib/api";
import type { EditorApi } from "@/components/designer/TemplateEditor";

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

  const { data: template } = useSWR<TemplateDetail>(
    `template-${templateId}`,
    () => getTemplate(templateId).then((r) => r.data as TemplateDetail),
    { revalidateOnFocus: false }
  );

  async function handleSave() {
    if (!apiRef.current) return;
    const { json, placeholderConfig } = apiRef.current.toTemplateJson();
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

      <div className="card p-6">
        <TemplateEditor
          width={template.canvas_width}
          height={template.canvas_height}
          initialJson={template.template_json}
          onReady={(api) => { apiRef.current = api; }}
        />
      </div>
    </div>
  );
}
