"use client";

import { useState, useEffect } from "react";
import useSWR from "swr";
import Link from "next/link";
import { Icon } from "@iconify/react";
import {
  listTemplates,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  listFanpages,
} from "@/lib/api";
import {
  listTemplateStyles,
  updateStyleDefaults,
  type StyleInfo,
  type TemplateStyle,
} from "@/lib/designTemplatesApi";
import {
  SWATCHES,
  STYLE_LABELS,
  STYLE_NOTES,
  CARD_USAGE,
  resolveLook,
  templateDisplayName,
} from "@/lib/brandLook";
import TemplateThumbnail from "@/components/designer/TemplateThumbnail";

type Template = {
  id: number;
  fanpage_id: number | null;
  name: string;
  canvas_width: number;
  canvas_height: number;
  is_default: boolean;
  category: "quote" | "news" | "discussion" | null;
  has_content: boolean;
  template_json: unknown | null;
  updated_at: string | null;
  style?: TemplateStyle | null;
  is_hidden?: boolean;
  replacement_style?: TemplateStyle | null;
};

type FanpageLite = {
  id: number;
  name: string;
  template_style?: TemplateStyle | null;
};

const SIZE_PRESETS = [
  { label: "Square 1080×1080 (FB post)", w: 1080, h: 1080 },
  { label: "Portrait 1080×1350", w: 1080, h: 1350 },
  { label: "Landscape 1200×630 (link card)", w: 1200, h: 630 },
];

const STYLE_PALETTES: Record<
  TemplateStyle,
  { label: string; a: string; b: string; q?: string }[]
> = {
  gp: [
    { a: "#E8362B", b: "#E8362B", q: "", label: "Racing red" },
    { a: "#E8362B", b: "#FFC21A", q: "", label: "Red + gold" },
    { a: "#2563EB", b: "#FFC21A", q: "", label: "Blue + gold" },
  ],
  highlight: [
    { a: "#C7F754", b: "#C7F754", q: "#FEFEFC", label: "Lime" },
    { a: "#22D3EE", b: "#22D3EE", q: "#FEFEFC", label: "Cyan" },
    { a: "#FFC21A", b: "#FFC21A", q: "#FEFEFC", label: "Gold" },
  ],
  classic: [
    { a: "#EC3324", b: "#EC3324", q: "", label: "Red" },
    { a: "#FFC21A", b: "#FFC21A", q: "", label: "Yellow" },
    { a: "#16A34A", b: "#16A34A", q: "", label: "Green" },
  ],
};

const SAMPLE_TITLES: Record<"news" | "quote" | "discussion", string> = {
  news: "Rookie stuns the paddock with a **record lap** in qualifying",
  quote: "We gave everything today and the **bike felt perfect** from the first lap",
  discussion: "Who takes the title if **rain hits** on Sunday?",
};

const SAMPLE_LABELS: Record<"news" | "quote" | "discussion", string | null> = {
  news: "BREAKING",
  quote: null,
  discussion: "DISCUSSION",
};

const STYLE_NUMS: Record<TemplateStyle, number> = {
  gp: 1,
  highlight: 2,
  classic: 3,
};

function StyleSection({
  info,
  fanpages,
  allTemplates,
  onMutateStyles,
}: {
  info: StyleInfo;
  fanpages: FanpageLite[];
  allTemplates: Template[];
  onMutateStyles: () => Promise<unknown>;
}) {
  const defP = info.defaults.primary || (info.style === "highlight" ? "#C7F754" : "#E8362B");
  const defS = info.defaults.secondary || defP;
  const defSame = !info.defaults.secondary || info.defaults.secondary.toLowerCase() === defP.toLowerCase();
  const defQuote = info.defaults.quoteMarks ?? (info.style === "highlight" ? "#FEFEFC" : "");

  const [primary, setPrimary] = useState<string>(defP);
  const [secondary, setSecondary] = useState<string>(defS);
  const [same, setSame] = useState<boolean>(defSame);
  const [quoteMarks, setQuoteMarks] = useState<string>(defQuote);
  const [streak, setStreak] = useState<boolean>(info.defaults.streak !== false);

  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const defKey = JSON.stringify(info.defaults);
  useEffect(() => {
    const p = info.defaults.primary || (info.style === "highlight" ? "#C7F754" : "#E8362B");
    const s = info.defaults.secondary || p;
    setPrimary(p);
    setSecondary(s);
    setSame(!info.defaults.secondary || info.defaults.secondary.toLowerCase() === p.toLowerCase());
    setQuoteMarks(info.defaults.quoteMarks ?? (info.style === "highlight" ? "#FEFEFC" : ""));
    setStreak(info.defaults.streak !== false);
  }, [defKey, info.style, info.defaults]);

  const secondaryEff = same ? primary : secondary;
  const currentLook = resolveLook(
    {
      primary,
      secondary: secondaryEff,
      quoteMarks: quoteMarks || primary,
      streak: info.style === "gp" ? streak : true,
    },
    primary
  );

  const handleSave = async () => {
    setSaving(true);
    setSaveMsg(null);
    setSaveError(null);
    try {
      const quotePayload =
        quoteMarks && quoteMarks.toUpperCase() !== primary.toUpperCase()
          ? quoteMarks
          : null;
      await updateStyleDefaults(info.style, {
        primary,
        secondary: same ? null : secondary,
        quoteMarks: quotePayload,
        streak: info.style === "gp" ? streak : null,
      });
      setSaveMsg("Saved.");
      await onMutateStyles();
      setTimeout(() => setSaveMsg(null), 3500);
    } catch {
      setSaveError("Save failed. Please try again.");
      setTimeout(() => setSaveError(null), 4000);
    } finally {
      setSaving(false);
    }
  };

  const matchingFans = fanpages.filter((f) => f.template_style === info.style);
  const usedByText =
    matchingFans.length > 0
      ? `Used by: ${matchingFans.map((f) => f.name).join(", ")}`
      : "Used by: No project yet";

  const num = STYLE_NUMS[info.style] || 1;
  const note = STYLE_NOTES[info.style]?.note || "";

  return (
    <section className="card p-6 space-y-6">
      <div className="flex flex-wrap justify-between items-baseline gap-2">
        <h2 className="text-h5 font-bold text-text-primary">
          {num} · {info.label || STYLE_LABELS[info.style]}
        </h2>
        <span className="text-sm text-text-secondary">{note}</span>
      </div>

      <div className="space-y-4 rounded-xl border border-divider-soft bg-bg-paper p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-bold text-text-primary mr-1">Default colors</span>
            {STYLE_PALETTES[info.style]?.map((p) => {
              const isSelected =
                primary.toLowerCase() === p.a.toLowerCase() &&
                (same ? p.a.toLowerCase() === p.b.toLowerCase() : secondary.toLowerCase() === p.b.toLowerCase()) &&
                (!p.q || quoteMarks.toUpperCase() === p.q.toUpperCase());
              return (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => {
                    setPrimary(p.a);
                    setSecondary(p.b);
                    setSame(p.a.toUpperCase() === p.b.toUpperCase());
                    setQuoteMarks(p.q || (info.style === "highlight" ? "#FEFEFC" : ""));
                  }}
                  className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs transition-colors ${
                    isSelected
                      ? "border-primary-main bg-primary-main/10 text-text-primary font-semibold"
                      : "border-divider-soft bg-bg-paper text-text-secondary hover:text-text-primary hover:bg-bg-paper-hover"
                  }`}
                >
                  <span className="flex w-5 h-3 rounded overflow-hidden border border-divider-soft">
                    <span className="flex-1" style={{ background: p.a }} />
                    <span className="flex-1" style={{ background: p.b }} />
                  </span>
                  <span>{p.label}</span>
                </button>
              );
            })}
          </div>
          <span className="text-xs text-text-secondary">{usedByText}</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2 border-t border-divider-soft">
          {/* Primary */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-text-primary">Primary</span>
              <span className="font-mono text-xs text-text-secondary uppercase">{primary}</span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {SWATCHES.map((s) => (
                <button
                  key={s.hex}
                  type="button"
                  title={s.label}
                  onClick={() => setPrimary(s.hex)}
                  className={`w-6 h-6 rounded-md border border-divider-soft transition-transform ${
                    primary.toLowerCase() === s.hex.toLowerCase() ? "ring-2 ring-primary-main scale-110" : "hover:scale-105"
                  }`}
                  style={{ backgroundColor: s.hex }}
                />
              ))}
              <input
                type="color"
                value={primary}
                onChange={(e) => setPrimary(e.target.value.toUpperCase())}
                className="w-7 h-6 rounded border border-divider-soft cursor-pointer bg-transparent"
                title="Custom color"
              />
            </div>
          </div>

          {/* Secondary */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-text-primary">Secondary</span>
              {!same && <span className="font-mono text-xs text-text-secondary uppercase">{secondary}</span>}
            </div>
            <label className="flex items-center gap-2 text-xs text-text-primary cursor-pointer">
              <input
                type="checkbox"
                checked={same}
                onChange={(e) => setSame(e.target.checked)}
                className="rounded border-divider-soft text-primary-main focus:ring-primary-main"
              />
              Same as primary
            </label>
            {!same && (
              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                {SWATCHES.map((s) => (
                  <button
                    key={s.hex}
                    type="button"
                    title={s.label}
                    onClick={() => setSecondary(s.hex)}
                    className={`w-6 h-6 rounded-md border border-divider-soft transition-transform ${
                      secondary.toLowerCase() === s.hex.toLowerCase() ? "ring-2 ring-primary-main scale-110" : "hover:scale-105"
                    }`}
                    style={{ backgroundColor: s.hex }}
                  />
                ))}
                <input
                  type="color"
                  value={secondary}
                  onChange={(e) => setSecondary(e.target.value.toUpperCase())}
                  className="w-7 h-6 rounded border border-divider-soft cursor-pointer bg-transparent"
                  title="Custom color"
                />
              </div>
            )}
          </div>

          {/* Quote marks */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-text-primary">Quote marks</span>
              <span className="font-mono text-xs text-text-secondary uppercase">
                {quoteMarks ? quoteMarks : "Same as badge"}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                onClick={() => setQuoteMarks("")}
                className={`px-2 py-0.5 text-xs rounded border ${
                  !quoteMarks
                    ? "border-primary-main bg-primary-main/10 text-primary-main font-semibold"
                    : "border-divider-soft text-text-secondary hover:text-text-primary"
                }`}
              >
                Same as badge
              </button>
              {info.style === "highlight" && (
                <button
                  type="button"
                  onClick={() => setQuoteMarks("#FEFEFC")}
                  className={`px-2 py-0.5 text-xs rounded border ${
                    quoteMarks.toUpperCase() === "#FEFEFC"
                      ? "border-primary-main bg-primary-main/10 text-primary-main font-semibold"
                      : "border-divider-soft text-text-secondary hover:text-text-primary"
                  }`}
                >
                  White (#FEFEFC)
                </button>
              )}
              {SWATCHES.map((s) => (
                <button
                  key={s.hex}
                  type="button"
                  title={s.label}
                  onClick={() => setQuoteMarks(s.hex)}
                  className={`w-6 h-6 rounded-md border border-divider-soft transition-transform ${
                    quoteMarks.toLowerCase() === s.hex.toLowerCase() ? "ring-2 ring-primary-main scale-110" : "hover:scale-105"
                  }`}
                  style={{ backgroundColor: s.hex }}
                />
              ))}
              <input
                type="color"
                value={quoteMarks || primary}
                onChange={(e) => setQuoteMarks(e.target.value.toUpperCase())}
                className="w-7 h-6 rounded border border-divider-soft cursor-pointer bg-transparent"
                title="Custom color"
              />
            </div>
          </div>
        </div>

        {/* Streak for GP */}
        {info.style === "gp" && (
          <div className="pt-2 border-t border-divider-soft">
            <label className="flex items-center gap-2 text-xs text-text-primary cursor-pointer">
              <input
                type="checkbox"
                checked={streak}
                onChange={(e) => setStreak(e.target.checked)}
                className="rounded border-divider-soft text-primary-main focus:ring-primary-main"
              />
              Splatter streak behind quote tab
            </label>
          </div>
        )}

        {/* Save button and messages */}
        <div className="flex items-center gap-3 pt-2">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="btn btn-primary text-xs py-1.5 px-3"
          >
            {saving ? "Saving…" : "Save defaults"}
          </button>
          {saveMsg && (
            <span className="text-xs text-emerald-600 dark:text-emerald-400 font-medium">{saveMsg}</span>
          )}
          {saveError && (
            <span className="text-xs text-rose-600 dark:text-rose-400 font-medium">{saveError}</span>
          )}
        </div>
      </div>

      {/* 3 cards: News / Quote / Discussion */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {(["news", "quote", "discussion"] as const).map((kind) => {
          const ref = info.templates[kind];
          const tpl = ref ? allTemplates.find((t) => t.id === ref.id) : null;
          const tag = kind === "discussion" ? "new" : "kept";
          const title = SAMPLE_TITLES[kind];
          const label = SAMPLE_LABELS[kind];

          return (
            <div
              key={kind}
              className="p-4 rounded-xl border border-divider-soft bg-bg-paper flex flex-col gap-3"
            >
              <div
                className="relative w-full rounded-lg overflow-hidden border border-divider-soft bg-[#0B1220]"
                style={{ aspectRatio: tpl ? `${tpl.canvas_width} / ${tpl.canvas_height}` : "4 / 5" }}
              >
                {tpl?.template_json ? (
                  <TemplateThumbnail
                    json={tpl.template_json}
                    width={tpl.canvas_width || 1080}
                    height={tpl.canvas_height || 1350}
                    className="w-full h-full object-contain"
                    look={currentLook}
                    label={label}
                    title={title}
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-text-disabled">
                    <Icon icon="solar:gallery-add-bold-duotone" width={32} className="opacity-40" />
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-bold text-text-primary truncate">
                  {tpl ? templateDisplayName(tpl) : (ref?.name || `${STYLE_LABELS[info.style]} ${kind}`)}
                </span>
                <span
                  className={`text-[10px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wider ${
                    tag === "new"
                      ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20"
                      : "bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20"
                  }`}
                >
                  {tag}
                </span>
              </div>

              <p className="text-xs text-text-secondary leading-snug">
                {CARD_USAGE[kind]}
              </p>

              {tpl && (
                <div className="pt-1 mt-auto">
                  <Link
                    href={`/templates/${tpl.id}`}
                    className="text-xs font-semibold text-primary-main hover:underline inline-flex items-center gap-1"
                  >
                    <Icon icon="solar:pen-bold" width={12} />
                    Open in designer
                  </Link>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function TemplatesPage() {
  const { data: styleList = [], mutate: mutateStyles } = useSWR<StyleInfo[]>(
    "template-styles",
    listTemplateStyles
  );
  const { data: templates = [], mutate: mutateTemplates } = useSWR<Template[]>(
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
  const [category, setCategory] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");

  async function handleRename(t: Template) {
    const newName = editName.trim();
    if (!newName || newName === t.name) {
      setEditingId(null);
      return;
    }
    await updateTemplate(t.id, { name: newName });
    setEditingId(null);
    mutateTemplates();
  }

  async function handleCreate() {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const preset = SIZE_PRESETS[sizeIdx];
      const r = await createTemplate({
        name: name.trim(),
        fanpage_id: fanpageId ? parseInt(fanpageId, 10) : null,
        canvas_width: preset.w,
        canvas_height: preset.h,
        category: category || null,
      });
      setShowForm(false);
      setName("");
      mutateTemplates();
      window.location.href = `/templates/${(r.data as Template).id}`;
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(t: Template) {
    if (!confirm(`Delete template "${t.name}"?`)) return;
    await deleteTemplate(t.id);
    mutateTemplates();
  }

  const fanpageName = (id: number | null) =>
    id === null
      ? "Shared (all fanpages)"
      : fanpages.find((f) => f.id === id)?.name ?? `Fanpage #${id}`;

  const userTemplates = templates.filter((t) => t.style == null && !t.is_hidden);
  const oldTemplates = templates.filter((t) => t.is_hidden === true);

  const sortedStyles = [...styleList].sort(
    (a, b) => (STYLE_NUMS[a.style] || 99) - (STYLE_NUMS[b.style] || 99)
  );

  return (
    <div className="space-y-10">
      {/* 1. Header */}
      <div>
        <div className="text-xs text-text-secondary font-medium mb-1">
          Settings › Design templates
        </div>
        <h1
          className="text-display-md text-text-primary"
          style={{ fontFamily: "'SF Pro Display', system-ui, sans-serif" }}
        >
          Template library
        </h1>
        <p className="text-caption text-text-secondary mt-1 max-w-3xl">
          3 styles, 9 templates: 6 kept from today and 3 new Discussion cards. All centered. Each style has default colors, set here. Every Content Project uses them unless it turns on Override in its Design Templates settings.
        </p>
      </div>

      {/* 2. For each StyleInfo */}
      <div className="space-y-8">
        {sortedStyles.map((info) => (
          <StyleSection
            key={info.style}
            info={info}
            fanpages={fanpages}
            allTemplates={templates}
            onMutateStyles={mutateStyles}
          />
        ))}
      </div>

      {/* 3. Section "Your templates" */}
      <section className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-h5 font-bold text-text-primary">Your templates</h2>
            <p className="text-caption text-text-secondary mt-0.5">
              Custom templates created for your fanpages
            </p>
          </div>
          <button
            onClick={() => setShowForm(true)}
            className="btn btn-primary flex items-center gap-2 shrink-0"
          >
            <Icon icon="solar:add-circle-bold-duotone" width={16} />
            New Template
          </button>
        </div>

        {showForm && (
          <div className="card p-6 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs font-semibold text-text-primary mb-1">Name</label>
                <input
                  className="input-rect py-2"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="MotoGP breaking news"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-text-primary mb-1">Fanpage</label>
                <select
                  className="input-rect py-2"
                  value={fanpageId}
                  onChange={(e) => setFanpageId(e.target.value)}
                >
                  <option value="">Shared (all fanpages)</option>
                  {fanpages.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-text-primary mb-1">Canvas Size</label>
                <select
                  className="input-rect py-2"
                  value={sizeIdx}
                  onChange={(e) => setSizeIdx(parseInt(e.target.value, 10))}
                >
                  {SIZE_PRESETS.map((p, i) => (
                    <option key={p.label} value={i}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-semibold text-text-primary mb-1">Category</label>
                <select
                  className="input-rect py-2"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                >
                  <option value="">Uncategorized</option>
                  <option value="quote">Quote</option>
                  <option value="news">News</option>
                  <option value="discussion">Discussion</option>
                </select>
                <p className="text-[10px] text-text-secondary mt-1">
                  Which fanpage &quot;Default Design Template&quot; picker this shows up in.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowForm(false)}
                className="btn btn-secondary"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={saving || !name.trim()}
                className="btn btn-primary"
              >
                {saving ? "Creating…" : "Create & Open Editor"}
              </button>
            </div>
          </div>
        )}

        {userTemplates.length === 0 && !showForm ? (
          <div className="card p-10 text-center text-text-disabled">
            <Icon icon="solar:palette-bold-duotone" width={40} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm">No custom templates yet. Create one to design your custom layout.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {userTemplates.map((t) => (
              <div key={t.id} className="group">
                <div
                  className="relative rounded-xl overflow-hidden border border-divider-soft bg-bg-default"
                  style={{ aspectRatio: `${t.canvas_width} / ${t.canvas_height}` }}
                >
                  {t.has_content && t.template_json ? (
                    <TemplateThumbnail
                      json={t.template_json}
                      width={t.canvas_width}
                      height={t.canvas_height}
                      className="w-full h-full object-contain"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-text-disabled">
                      <Icon icon="solar:gallery-add-bold-duotone" width={40} className="opacity-40" />
                    </div>
                  )}

                  <Link
                    href={`/templates/${t.id}`}
                    className="absolute inset-0 flex items-center justify-center bg-black/45 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <span className="inline-flex items-center gap-1.5 text-white font-semibold text-sm">
                      <Icon icon="solar:pen-bold" width={15} /> Open in editor
                    </span>
                  </Link>

                  {t.is_default && (
                    <span className="absolute top-2 left-2 inline-flex items-center rounded-full bg-primary-main text-white px-2 py-0.5 text-[10px] font-semibold shadow">
                      default
                    </span>
                  )}

                  <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    {!t.is_default && (
                      <button
                        onClick={() =>
                          updateTemplate(t.id, { is_default: true }).then(() => mutateTemplates())
                        }
                        title="Make default"
                        className="p-1.5 rounded-lg bg-white/90 text-text-primary hover:text-primary-main shadow-sm"
                      >
                        <Icon icon="solar:star-bold-duotone" width={14} />
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(t)}
                      title="Delete"
                      className="p-1.5 rounded-lg bg-white/90 text-text-primary hover:text-red-600 shadow-sm"
                    >
                      <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
                    </button>
                  </div>
                </div>

                <div className="mt-2 px-0.5">
                  {editingId === t.id ? (
                    <div className="flex items-center gap-1.5">
                      <input
                        autoFocus
                        className="input-rect py-1 text-sm"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleRename(t);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                      />
                      <button
                        onClick={() => handleRename(t)}
                        title="Save"
                        className="p-1 text-primary-main shrink-0"
                      >
                        <Icon icon="solar:check-circle-bold" width={16} />
                      </button>
                      <button
                        onClick={() => setEditingId(null)}
                        title="Cancel"
                        className="p-1 text-text-secondary shrink-0"
                      >
                        <Icon icon="solar:close-circle-bold" width={16} />
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-semibold text-text-primary truncate">
                        {t.name}
                      </span>
                      <button
                        onClick={() => {
                          setEditingId(t.id);
                          setEditName(t.name);
                        }}
                        title="Rename"
                        className="p-0.5 rounded text-text-secondary hover:text-primary-main shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        <Icon icon="solar:pen-bold-duotone" width={12} />
                      </button>
                    </div>
                  )}
                  <p className="text-[11px] text-text-secondary mt-0.5">
                    {t.canvas_width} × {t.canvas_height} px · {fanpageName(t.fanpage_id)}
                  </p>
                  <select
                    value={t.category ?? ""}
                    onChange={(e) =>
                      updateTemplate(t.id, { category: e.target.value || null }).then(() =>
                        mutateTemplates()
                      )
                    }
                    className={`mt-1 text-[10px] font-semibold rounded-full px-2 py-0.5 border ${
                      t.category === "quote"
                        ? "bg-purple-50 text-purple-700 border-purple-200"
                        : t.category === "news"
                        ? "bg-blue-50 text-blue-700 border-blue-200"
                        : t.category === "discussion"
                        ? "bg-amber-50 text-amber-700 border-amber-200"
                        : "bg-bg-default text-text-secondary border-divider-soft"
                    }`}
                  >
                    <option value="">Uncategorized</option>
                    <option value="quote">Quote</option>
                    <option value="news">News</option>
                    <option value="discussion">Discussion</option>
                  </select>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 4. Collapsible section "Old templates that go away ({count})" */}
      <details className="card p-6 space-y-4">
        <summary className="cursor-pointer text-base font-bold text-text-primary flex items-center justify-between select-none">
          <span>Old templates that go away ({oldTemplates.length})</span>
          <span className="text-xs text-text-secondary font-normal">
            Projects using them move to the style in brackets
          </span>
        </summary>
        {oldTemplates.length === 0 ? (
          <p className="text-xs text-text-secondary">No deprecated templates.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 pt-3">
            {oldTemplates.map((t) => (
              <div
                key={t.id}
                className="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-divider-soft bg-bg-paper text-sm"
              >
                <div className="flex items-baseline gap-1.5 truncate">
                  <span className="font-medium text-text-primary truncate">{t.name}</span>
                  {t.replacement_style && (
                    <span className="text-xs text-text-secondary shrink-0">
                      [{STYLE_LABELS[t.replacement_style] || t.replacement_style}]
                    </span>
                  )}
                </div>
                <Link
                  href={`/templates/${t.id}`}
                  className="text-xs text-primary-main hover:underline shrink-0"
                >
                  Open
                </Link>
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-text-secondary pt-2">
          Hidden from pickers. Projects that used them were moved to the style in brackets. They are deleted in a later release.
        </p>
      </details>
    </div>
  );
}
