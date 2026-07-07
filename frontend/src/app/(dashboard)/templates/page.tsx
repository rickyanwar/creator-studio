"use client";

import useSWR from "swr";
import { useState } from "react";
import Link from "next/link";
import { Icon } from "@iconify/react";
import { formatDistanceToNowStrict } from "date-fns";
import { listTemplates, createTemplate, updateTemplate, deleteTemplate, listFanpages } from "@/lib/api";

type Template = {
  id: number;
  fanpage_id: number | null;
  name: string;
  canvas_width: number;
  canvas_height: number;
  is_default: boolean;
  has_content: boolean;
  updated_at: string | null;
};

type FanpageLite = { id: number; name: string };

const SIZE_PRESETS = [
  { label: "Square 1080×1080 (FB post)", w: 1080, h: 1080 },
  { label: "Portrait 1080×1350", w: 1080, h: 1350 },
  { label: "Landscape 1200×630 (link card)", w: 1200, h: 630 },
];

export default function TemplatesPage() {
  const { data: templates = [], mutate } = useSWR<Template[]>(
    "design-templates",
    () => listTemplates().then((r) => r.data as Template[])
  );
  const { data: fanpages = [] } = useSWR<FanpageLite[]>(
    "fanpages-lite",
    () => listFanpages().then((r) => r.data as FanpageLite[])
  );

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [fanpageId, setFanpageId] = useState<string>("");
  const [sizeIdx, setSizeIdx] = useState(0);
  const [saving, setSaving] = useState(false);

  async function handleCreate() {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const preset = SIZE_PRESETS[sizeIdx];
      const r = await createTemplate({
        name: name.trim(),
        fanpage_id: fanpageId ? parseInt(fanpageId) : null,
        canvas_width: preset.w,
        canvas_height: preset.h,
      });
      setShowForm(false);
      setName("");
      mutate();
      window.location.href = `/templates/${(r.data as Template).id}`;
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(t: Template) {
    if (!confirm(`Delete template "${t.name}"?`)) return;
    await deleteTemplate(t.id);
    mutate();
  }

  const fanpageName = (id: number | null) =>
    id === null ? "Shared (all fanpages)" : fanpages.find((f) => f.id === id)?.name ?? `Fanpage #${id}`;

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-display-md text-ink" style={{ fontFamily: "'SF Pro Display', system-ui, sans-serif" }}>
            Design Templates
          </h1>
          <p className="text-caption text-ink-48 mt-1">
            Fabric.js templates with headline + image placeholders — used by the News-to-Image designer
          </p>
        </div>
        <button onClick={() => setShowForm(true)} className="btn btn-primary flex items-center gap-2 shrink-0">
          <Icon icon="solar:add-circle-bold-duotone" width={16} />
          New Template
        </button>
      </div>

      {showForm && (
        <div className="card p-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-semibold text-ink-80 mb-1">Name</label>
              <input className="input-rect py-2" value={name} onChange={(e) => setName(e.target.value)} placeholder="MotoGP breaking news" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-ink-80 mb-1">Fanpage</label>
              <select className="input-rect py-2" value={fanpageId} onChange={(e) => setFanpageId(e.target.value)}>
                <option value="">Shared (all fanpages)</option>
                {fanpages.map((f) => (
                  <option key={f.id} value={f.id}>{f.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-ink-80 mb-1">Canvas Size</label>
              <select className="input-rect py-2" value={sizeIdx} onChange={(e) => setSizeIdx(parseInt(e.target.value))}>
                {SIZE_PRESETS.map((p, i) => (
                  <option key={p.label} value={i}>{p.label}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowForm(false)} className="btn btn-secondary">Cancel</button>
            <button onClick={handleCreate} disabled={saving || !name.trim()} className="btn btn-primary">
              {saving ? "Creating…" : "Create & Open Editor"}
            </button>
          </div>
        </div>
      )}

      {templates.length === 0 && !showForm ? (
        <div className="card p-10 text-center text-ink-48">
          <Icon icon="solar:palette-bold-duotone" width={40} className="mx-auto mb-3 opacity-40" />
          <p className="text-sm">No templates yet. Create one to design your news post layout.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {templates.map((t) => (
            <div key={t.id} className="card p-5 space-y-3">
              <div className="flex items-start justify-between">
                <div>
                  <p className="text-sm font-bold text-ink flex items-center gap-2">
                    {t.name}
                    {t.is_default && (
                      <span className="inline-flex items-center rounded-full bg-primary-main/10 text-primary-main px-2 py-0.5 text-[10px] font-semibold">default</span>
                    )}
                  </p>
                  <p className="text-[11px] text-ink-48 mt-0.5">{fanpageName(t.fanpage_id)}</p>
                </div>
                <button onClick={() => handleDelete(t)} className="p-1.5 rounded-lg text-ink-48 hover:text-red-600 hover:bg-red-50 transition-colors">
                  <Icon icon="solar:trash-bin-trash-bold-duotone" width={15} />
                </button>
              </div>
              <p className="text-[11px] text-ink-48">
                {t.canvas_width}×{t.canvas_height}
                {" · "}
                {t.has_content ? "designed" : "empty"}
                {t.updated_at && <> · {formatDistanceToNowStrict(new Date(t.updated_at), { addSuffix: true })}</>}
              </p>
              <div className="flex gap-2">
                <Link href={`/templates/${t.id}`} className="btn btn-secondary flex-1 text-center flex items-center justify-center gap-1.5">
                  <Icon icon="solar:pen-bold-duotone" width={13} />
                  Open Editor
                </Link>
                {!t.is_default && (
                  <button
                    onClick={() => updateTemplate(t.id, { is_default: true }).then(() => mutate())}
                    className="btn btn-secondary"
                    title="Make default"
                  >
                    <Icon icon="solar:star-bold-duotone" width={13} />
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
