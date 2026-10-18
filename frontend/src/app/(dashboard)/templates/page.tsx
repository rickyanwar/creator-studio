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

const CATEGORY_USES: { name: string; uses: string[] }[] = [
  {
    name: "News",
    uses: ["News cards", "Instagram recreate", "Viral Radar", "Pinterest (no face)", "FB recreate · news"],
  },
  {
    name: "Quote",
    uses: ["News quote cards (~1 in 10 articles)", "Instagram recreate", "Viral Radar", "Pinterest (face found)", "FB recreate · quote"],
  },
  {
    name: "Discussion",
    uses: ["Discussion cards", "FB recreate · discussion"],
  },
];

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
      ? matchingFans.map((f) => f.name).join(", ")
      : "No brand assigned";

  const note = STYLE_NOTES[info.style]?.note || "";

  return (
    <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col lg:flex-row gap-5">
      {/* ── Left Column: Controls & Defaults ── */}
      <div className="lg:w-[320px] shrink-0 flex flex-col gap-3.5">
        <div>
          <h2 className="text-[17px] font-bold text-text">
            {info.label || STYLE_LABELS[info.style]}
          </h2>
          <p className="text-[13px] text-text2 mt-0.5">{note}</p>
        </div>

        <div className="space-y-3 pt-2 border-t border-line">
          <div className="flex items-center justify-between">
            <span className="text-[13px] font-semibold text-text">Default colors</span>
            <span className="text-xs text-text2 truncate max-w-[150px]" title={usedByText}>
              Used by {usedByText}
            </span>
          </div>

          {/* Palette preset chips */}
          <div className="flex flex-wrap items-center gap-1.5">
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
                  className={`flex items-center gap-1.5 px-2 py-1 rounded-lg border text-xs transition-colors ${
                    isSelected
                      ? "border-pri bg-pri-soft text-pri-text font-semibold ring-1 ring-pri"
                      : "border-line bg-surface text-text hover:bg-surface2"
                  }`}
                >
                  <span className="flex w-4 h-3 rounded overflow-hidden border border-line shrink-0">
                    <span className="flex-1" style={{ background: p.a }} />
                    <span className="flex-1" style={{ background: p.b }} />
                  </span>
                  <span>{p.label}</span>
                </button>
              );
            })}
          </div>

          {/* Primary swatch row */}
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold text-text">Primary</span>
              <span className="font-mono text-text2 uppercase">{primary}</span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {SWATCHES.map((s) => (
                <button
                  key={s.hex}
                  type="button"
                  title={s.label}
                  onClick={() => setPrimary(s.hex)}
                  className={`w-6 h-6 rounded-md border border-line transition-transform ${
                    primary.toLowerCase() === s.hex.toLowerCase()
                      ? "ring-2 ring-pri scale-110"
                      : "hover:scale-105"
                  }`}
                  style={{ backgroundColor: s.hex }}
                />
              ))}
              <input
                type="color"
                value={primary}
                onChange={(e) => setPrimary(e.target.value.toUpperCase())}
                className="w-7 h-6 rounded border border-line cursor-pointer bg-transparent"
                title="Custom color"
              />
            </div>
          </div>

          {/* Secondary swatch row */}
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold text-text">Secondary</span>
              {!same && <span className="font-mono text-text2 uppercase">{secondary}</span>}
            </div>
            <label className="flex items-center gap-2 text-xs text-text cursor-pointer">
              <input
                type="checkbox"
                checked={same}
                onChange={(e) => setSame(e.target.checked)}
                className="w-3.5 h-3.5 rounded accent-pri border-line"
              />
              <span>Same as primary</span>
            </label>
            {!same && (
              <div className="flex flex-wrap items-center gap-1.5 pt-1">
                {SWATCHES.map((s) => (
                  <button
                    key={s.hex}
                    type="button"
                    title={s.label}
                    onClick={() => setSecondary(s.hex)}
                    className={`w-6 h-6 rounded-md border border-line transition-transform ${
                      secondary.toLowerCase() === s.hex.toLowerCase()
                        ? "ring-2 ring-pri scale-110"
                        : "hover:scale-105"
                    }`}
                    style={{ backgroundColor: s.hex }}
                  />
                ))}
                <input
                  type="color"
                  value={secondary}
                  onChange={(e) => setSecondary(e.target.value.toUpperCase())}
                  className="w-7 h-6 rounded border border-line cursor-pointer bg-transparent"
                  title="Custom color"
                />
              </div>
            )}
          </div>

          {/* Quote marks */}
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="font-semibold text-text">Quote marks</span>
              <span className="font-mono text-text2 uppercase">
                {quoteMarks ? quoteMarks : "Same as badge"}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                onClick={() => setQuoteMarks("")}
                className={`px-2 py-0.5 text-xs rounded border ${
                  !quoteMarks
                    ? "border-pri bg-pri-soft text-pri-text font-semibold"
                    : "border-line text-text2 hover:text-text"
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
                      ? "border-pri bg-pri-soft text-pri-text font-semibold"
                      : "border-line text-text2 hover:text-text"
                  }`}
                >
                  White
                </button>
              )}
              {SWATCHES.map((s) => (
                <button
                  key={s.hex}
                  type="button"
                  title={s.label}
                  onClick={() => setQuoteMarks(s.hex)}
                  className={`w-6 h-6 rounded-md border border-line transition-transform ${
                    quoteMarks.toLowerCase() === s.hex.toLowerCase()
                      ? "ring-2 ring-pri scale-110"
                      : "hover:scale-105"
                  }`}
                  style={{ backgroundColor: s.hex }}
                />
              ))}
              <input
                type="color"
                value={quoteMarks || primary}
                onChange={(e) => setQuoteMarks(e.target.value.toUpperCase())}
                className="w-7 h-6 rounded border border-line cursor-pointer bg-transparent"
                title="Custom color"
              />
            </div>
          </div>

          {/* Streak toggle for GP */}
          {info.style === "gp" && (
            <label className="flex items-center gap-2 text-xs text-text cursor-pointer pt-1">
              <input
                type="checkbox"
                checked={streak}
                onChange={(e) => setStreak(e.target.checked)}
                className="w-3.5 h-3.5 rounded accent-pri border-line"
              />
              <span>Splatter streak behind quote tab</span>
            </label>
          )}

          {/* Save defaults button */}
          <div className="pt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="h-8 px-3 rounded-[8px] border border-line bg-surface text-text text-xs font-semibold hover:bg-surface2 transition-colors disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save defaults"}
            </button>
            {saveMsg && <span className="text-xs text-pri-text font-medium">{saveMsg}</span>}
            {saveError && <span className="text-xs text-bad font-medium">{saveError}</span>}
          </div>
        </div>
      </div>

      {/* ── Right Column: 3 Cards (News, Quote, Discussion) ── */}
      <div className="flex-1 min-w-0 grid grid-cols-1 sm:grid-cols-3 gap-3">
        {(["news", "quote", "discussion"] as const).map((kind) => {
          const ref = info.templates[kind];
          const tpl = ref ? allTemplates.find((t) => t.id === ref.id) : null;
          const title = SAMPLE_TITLES[kind];
          const label = SAMPLE_LABELS[kind];
          const kindLabel = kind.charAt(0).toUpperCase() + kind.slice(1);

          return (
            <div
              key={kind}
              className="flex flex-col gap-2 p-3 rounded-xl border border-line bg-surface2/50"
            >
              <div
                className="relative w-full rounded-[10px] overflow-hidden border border-line bg-[#0B1220]"
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
                  <div className="w-full h-full flex items-center justify-center text-text2">
                    <Icon icon="solar:gallery-add-bold-duotone" width={32} className="opacity-40" />
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between gap-1 mt-auto">
                <span className="text-[13px] font-semibold text-text truncate">
                  {tpl ? templateDisplayName(tpl) : kindLabel}
                </span>
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded uppercase tracking-wider bg-surface border border-line text-text2">
                  {kind}
                </span>
              </div>

              {CARD_USAGE[kind] && (
                <p className="text-xs text-text2 leading-snug">
                  {CARD_USAGE[kind]}
                </p>
              )}

              {tpl && (
                <Link
                  href={`/templates/${tpl.id}`}
                  className="h-8 px-2.5 rounded-[8px] border border-line bg-surface text-text text-xs font-medium inline-flex items-center justify-center hover:bg-surface2 transition-colors mt-1"
                >
                  Edit template
                </Link>
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

  const STYLE_NUMS: Record<TemplateStyle, number> = { gp: 1, highlight: 2, classic: 3 };
  const sortedStyles = [...styleList].sort(
    (a, b) => (STYLE_NUMS[a.style] || 99) - (STYLE_NUMS[b.style] || 99)
  );

  return (
    <div className="space-y-6 max-w-[1240px] pb-24">
      {/* ── 1. Page Header ── */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-[26px] font-bold text-text tracking-tight">Templates</h1>
          <p className="text-sm text-text2">
            Three styles, each with News, Quote and Discussion. Default colors here; brands may override them.
          </p>
          <p className="text-[13px] text-text2">
            Edit template opens the template editor. The Designer is only for finishing a post.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="h-10 px-3.5 rounded-[10px] border border-line bg-surface text-text text-sm font-semibold hover:bg-surface2 transition-colors shrink-0"
        >
          New custom template
        </button>
      </div>

      {/* ── 2. Where Each Category Is Used ── */}
      <section
        aria-labelledby="cat-use"
        className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3"
      >
        <h2 id="cat-use" className="text-[15px] font-semibold text-text">
          Where each category is used
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {CATEGORY_USES.map((cat) => (
            <div
              key={cat.name}
              className="p-3 rounded-xl bg-surface2 flex flex-col gap-2"
            >
              <span className="font-semibold text-sm text-text">{cat.name}</span>
              <div className="flex flex-wrap gap-1.5">
                {cat.uses.map((u) => (
                  <span
                    key={u}
                    className="px-2 py-0.5 rounded-md bg-surface border border-line text-xs text-text2"
                  >
                    {u}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
        <p className="text-[13px] text-text2">
          There is no Pinterest template: a pin becomes a Quote card when a face is found, otherwise a News card. Instagram recreate picks News or Quote per post; FB recreate picks News, Quote or Discussion. Posts classified as &quot;other&quot; are skipped.
        </p>
      </section>

      {/* ── 3. Style Sections (GP Racing, Highlight, Classic) ── */}
      <div className="space-y-4">
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

      {/* ── 4. Custom Templates Section ── */}
      <section className="bg-surface border border-line rounded-[14px] p-5 flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-[15px] font-semibold text-text">Custom templates</h2>
          <button
            onClick={() => setShowForm(true)}
            className="text-xs font-semibold text-pri-text hover:underline"
          >
            + Create template
          </button>
        </div>

        {showForm && (
          <div className="p-4 rounded-xl border border-line bg-surface2 space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs font-semibold text-text mb-1">Name</label>
                <input
                  className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="MotoGP breaking news"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-text mb-1">Fanpage</label>
                <select
                  className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
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
                <label className="block text-xs font-semibold text-text mb-1">Canvas Size</label>
                <select
                  className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
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
                <label className="block text-xs font-semibold text-text mb-1">Category</label>
                <select
                  className="h-9 px-3 rounded-[8px] border border-line bg-surface text-text text-sm w-full"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                >
                  <option value="">Uncategorized</option>
                  <option value="quote">Quote</option>
                  <option value="news">News</option>
                  <option value="discussion">Discussion</option>
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="h-8 px-3 rounded-[8px] border border-line bg-surface text-text text-xs hover:bg-surface2"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleCreate}
                disabled={saving || !name.trim()}
                className="h-8 px-3 rounded-[8px] bg-pri text-pri-fg text-xs font-semibold hover:opacity-90 disabled:opacity-50"
              >
                {saving ? "Creating…" : "Create & Open Editor"}
              </button>
            </div>
          </div>
        )}

        {userTemplates.length === 0 && !showForm ? (
          <p className="text-xs text-text2 py-2">
            No custom templates yet. Create one to design your custom layout.
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {userTemplates.map((t) => (
              <div
                key={t.id}
                className="group p-3 rounded-xl border border-line bg-surface2/50 flex flex-col gap-2"
              >
                <div
                  className="relative rounded-[10px] overflow-hidden border border-line bg-surface"
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
                    <div className="w-full h-full flex items-center justify-center text-text2">
                      <Icon icon="solar:gallery-add-bold-duotone" width={32} className="opacity-40" />
                    </div>
                  )}

                  <Link
                    href={`/templates/${t.id}`}
                    className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <span className="inline-flex items-center gap-1.5 text-white font-semibold text-xs">
                      <Icon icon="solar:pen-bold" width={14} /> Open
                    </span>
                  </Link>

                  {t.is_default && (
                    <span className="absolute top-2 left-2 inline-flex items-center rounded-full bg-pri text-pri-fg px-2 py-0.5 text-[10px] font-semibold shadow">
                      default
                    </span>
                  )}

                  <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity z-10">
                    {!t.is_default && (
                      <button
                        onClick={() =>
                          updateTemplate(t.id, { is_default: true }).then(() => mutateTemplates())
                        }
                        title="Make default"
                        className="p-1.5 rounded-lg bg-surface/90 text-text hover:text-pri-text shadow-sm border border-line"
                      >
                        <Icon icon="solar:star-bold-duotone" width={14} />
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(t)}
                      title="Delete"
                      className="p-1.5 rounded-lg bg-surface/90 text-text hover:text-bad shadow-sm border border-line"
                    >
                      <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} />
                    </button>
                  </div>
                </div>

                <div className="flex items-center justify-between gap-1">
                  {editingId === t.id ? (
                    <div className="flex items-center gap-1 w-full">
                      <input
                        autoFocus
                        className="h-7 px-2 rounded border border-line bg-surface text-text text-xs flex-1 min-w-0"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleRename(t);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                      />
                      <button
                        onClick={() => handleRename(t)}
                        className="p-1 text-pri-text shrink-0"
                      >
                        <Icon icon="solar:check-circle-bold" width={14} />
                      </button>
                    </div>
                  ) : (
                    <span className="text-[13px] font-semibold text-text truncate">{t.name}</span>
                  )}
                  <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                    <button
                      onClick={() => {
                        setEditingId(t.id);
                        setEditName(t.name);
                      }}
                      title="Rename"
                      className="p-1 rounded text-text2 hover:text-text"
                    >
                      <Icon icon="solar:pen-bold" width={12} />
                    </button>
                    <button
                      onClick={() => handleDelete(t)}
                      title="Delete"
                      className="p-1 rounded text-text2 hover:text-bad"
                    >
                      <Icon icon="solar:trash-bin-trash-bold" width={12} />
                    </button>
                  </div>
                </div>

                <p className="text-[11px] text-text2">
                  {t.canvas_width}×{t.canvas_height} · {fanpageName(t.fanpage_id)}
                </p>

                <select
                  value={t.category ?? ""}
                  onChange={(e) =>
                    updateTemplate(t.id, { category: e.target.value || null }).then(() =>
                      mutateTemplates()
                    )
                  }
                  className={`mt-0.5 text-[10px] font-semibold rounded-full px-2 py-0.5 border ${
                    t.category === "quote"
                      ? "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/30"
                      : t.category === "news"
                      ? "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/30"
                      : t.category === "discussion"
                      ? "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/30"
                      : "bg-surface text-text2 border-line"
                  }`}
                >
                  <option value="">Uncategorized</option>
                  <option value="quote">Quote</option>
                  <option value="news">News</option>
                  <option value="discussion">Discussion</option>
                </select>
              </div>
            ))}
          </div>
        )}

        {/* ── Collapsible Old Templates ── */}
        <details className="border-t border-line pt-3">
          <summary className="text-sm font-semibold text-text cursor-pointer select-none">
            Old templates ({oldTemplates.length}) · hidden
          </summary>
          <p className="text-[13px] text-text2 mt-1">
            Hidden from pickers. Posts that still point to one keep rendering with it.
          </p>
          {oldTemplates.length > 0 && (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2.5 pt-3">
              {oldTemplates.map((t) => (
                <div
                  key={t.id}
                  className="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-line bg-surface text-xs"
                >
                  <span className="font-medium text-text truncate">{t.name}</span>
                  <Link
                    href={`/templates/${t.id}`}
                    className="text-pri-text font-semibold hover:underline shrink-0"
                  >
                    Open
                  </Link>
                </div>
              ))}
            </div>
          )}
        </details>
      </section>
    </div>
  );
}
