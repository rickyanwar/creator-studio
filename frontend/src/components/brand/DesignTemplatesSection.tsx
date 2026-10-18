"use client";

import { useState, useEffect, useId } from "react";
import useSWR from "swr";
import Link from "next/link";
import type { Fanpage, Template } from "@/lib/types";
import { updateFanpage, listTemplates } from "@/lib/api";
import {
  listTemplateStyles,
  type StyleInfo,
  type TemplateStyle,
} from "@/lib/designTemplatesApi";
import {
  SWATCHES,
  PRESETS,
  STYLE_LABELS,
  STYLE_NOTES,
  CARD_USAGE,
  isHex,
  luminance,
  contrastRatio,
  autoInk,
  resolveLook,
  templateDisplayName,
} from "@/lib/brandLook";
import TemplateThumbnail from "@/components/designer/TemplateThumbnail";

interface Props {
  fanpage: Fanpage;
  onSaved?: () => void;
}

export default function DesignTemplatesSection({ fanpage, onSaved }: Props) {
  const sameColorId = useId();
  const streakOnId = useId();
  const primaryCustomId = useId();
  const secondaryCustomId = useId();
  const badgeCustomId = useId();
  const inkCustomId = useId();
  const quoteCustomId = useId();

  const { data: styles = [] } = useSWR<StyleInfo[]>("template-styles", listTemplateStyles);
  const { data: templates = [] } = useSWR<Template[]>(
    ["templates", fanpage.id],
    () => listTemplates(fanpage.id).then((r) => r.data as Template[])
  );

  const initStyle = (): TemplateStyle => {
    if (fanpage.template_style === "gp" || fanpage.template_style === "highlight" || fanpage.template_style === "classic") {
      return fanpage.template_style;
    }
    if (fanpage.default_news_template_id && styles.length > 0) {
      const match = styles.find((s) => s.templates?.news?.id === fanpage.default_news_template_id);
      if (match) return match.style;
    }
    return "gp";
  };

  const [style, setStyle] = useState<TemplateStyle>(initStyle);
  const [styleTouched, setStyleTouched] = useState(false);
  const [override, setOverride] = useState<boolean>(fanpage.brand_override === true);

  useEffect(() => {
    if (styleTouched || fanpage.template_style || styles.length === 0) return;
    const match = styles.find((s) => s.templates?.news?.id === fanpage.default_news_template_id);
    if (match) setStyle(match.style);
  }, [styles, styleTouched, fanpage.template_style, fanpage.default_news_template_id]);

  const currentStyleInfo = styles.find((s) => s.style === style) || styles[0];
  const styleDefs = currentStyleInfo?.defaults || {};
  const defP = styleDefs.primary || "#E8362B";
  const defS = styleDefs.secondary || defP;

  const [primary, setPrimary] = useState<string>(fanpage.accent_color || defP);
  const [secondary, setSecondary] = useState<string>(
    fanpage.accent_secondary_color || fanpage.accent_color || defS
  );
  const [same, setSame] = useState<boolean>(
    !fanpage.accent_secondary_color || fanpage.accent_secondary_color === fanpage.accent_color
  );

  const initBadgeSrc = (): "primary" | "secondary" | "custom" => {
    if (!fanpage.badge_color) return "primary";
    if (fanpage.badge_color === (fanpage.accent_secondary_color || fanpage.accent_color)) return "secondary";
    return "custom";
  };
  const [badgeSrc, setBadgeSrc] = useState<"primary" | "secondary" | "custom">(initBadgeSrc);
  const [badgeCustom, setBadgeCustom] = useState<string>(fanpage.badge_color || defP);

  const initInk = (): "auto" | "white" | "black" | "custom" => {
    if (!fanpage.badge_text_color) return "auto";
    const up = fanpage.badge_text_color.toUpperCase();
    if (up === "#FFFFFF") return "white";
    if (up === "#0B0B0B" || up === "#000000" || up === "#111111") return "black";
    return "custom";
  };
  const [ink, setInk] = useState<"auto" | "white" | "black" | "custom">(initInk);
  const [inkCustom, setInkCustom] = useState<string>(fanpage.badge_text_color || "#FFFFFF");

  const initQuoteSrc = (): "badge" | "primary" | "secondary" | "white" | "custom" => {
    if (!fanpage.quote_mark_color) return "badge";
    const up = fanpage.quote_mark_color.toUpperCase();
    if (up === "#FEFEFC" || up === "#FFFFFF") return "white";
    if (fanpage.quote_mark_color === fanpage.accent_color) return "primary";
    if (fanpage.quote_mark_color === (fanpage.accent_secondary_color || fanpage.accent_color)) return "secondary";
    return "custom";
  };
  const [quoteSrc, setQuoteSrc] = useState<"badge" | "primary" | "secondary" | "white" | "custom">(initQuoteSrc);
  const [quoteCustom, setQuoteCustom] = useState<string>(fanpage.quote_mark_color || defP);
  const [streak, setStreak] = useState<boolean>(fanpage.quote_streak !== false);

  const [advNewsId, setAdvNewsId] = useState<number | null>(fanpage.default_news_template_id ?? null);
  const [advQuoteId, setAdvQuoteId] = useState<number | null>(fanpage.default_quote_template_id ?? null);
  const [advDiscId, setAdvDiscId] = useState<number | null>(fanpage.default_discussion_template_id ?? null);

  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const secondaryEff = same ? primary : secondary;
  const badgeFill = badgeSrc === "secondary" ? secondaryEff : badgeSrc === "custom" ? badgeCustom : primary;
  const badgeInk = ink === "white" ? "#FFFFFF" : ink === "black" ? "#0B0B0B" : ink === "custom" ? inkCustom : autoInk(badgeFill);
  const quoteColor = quoteSrc === "badge" ? badgeFill : quoteSrc === "primary" ? primary : quoteSrc === "secondary" ? secondaryEff : quoteSrc === "white" ? "#FEFEFC" : quoteCustom;

  const resolvedLook = override
    ? resolveLook({ primary, secondary: secondaryEff, badge: badgeFill, badgeText: badgeInk, quoteMarks: quoteColor, streak: style === "gp" ? streak : true })
    : resolveLook(styleDefs, defP);

  const showTooDark = override && !same && luminance(secondaryEff) < 0.04;
  const showLowContrast = override && contrastRatio(badgeFill, badgeInk) < 3;

  const styleNewsId = currentStyleInfo?.templates?.news?.id ?? null;
  const styleQuoteId = currentStyleInfo?.templates?.quote?.id ?? null;
  const styleDiscId = currentStyleInfo?.templates?.discussion?.id ?? null;

  const templateById = (id: number | null) => (id ? templates.find((t) => t.id === id) ?? null : null);
  // An explicit per-category choice only counts when it is NOT one of the style templates (style === null/undefined:
  // a user-made template, or an old hidden one that is still saved). Style templates always follow the selected style.
  const effectiveId = (advId: number | null, styleId: number | null) => {
    const t = templateById(advId);
    return t && !t.style ? t.id : styleId;
  };

  const getCategoryOptions = (cat: "news" | "quote" | "discussion", savedId: number | null) => {
    return templates.filter((t) => {
      if (savedId && t.id === savedId) return true;
      if (t.is_hidden) return false;
      const matchStyle = !t.style || t.style === style;
      const matchCat = !t.category || t.category === cat;
      return matchStyle && matchCat;
    });
  };

  const handleToggleOverride = (enable: boolean) => {
    if (enable) {
      setOverride(true);
      if (fanpage.brand_override !== true || !isHex(fanpage.accent_color ?? "")) {
        // No saved override yet: start from the selected style's defaults.
        setPrimary(defP);
        setSecondary(defS);
        setSame(defP.toLowerCase() === defS.toLowerCase());
        setBadgeSrc("primary");
        setInk("auto");
        setQuoteSrc(styleDefs.quoteMarks ? "white" : "badge");
        setStreak(true);
      }
    } else {
      setOverride(false);
    }
  };

  const handleReset = () => {
    setOverride(false);
    setPrimary(defP);
    setSecondary(defS);
    setSame(defP.toLowerCase() === defS.toLowerCase());
    setBadgeSrc("primary");
    setInk("auto");
    setQuoteSrc(styleDefs.quoteMarks ? "white" : "badge");
    setStreak(true);
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveMsg(null);
    setSaveError(null);
    try {
      const secVal = same ? null : secondary;
      const bColor = badgeSrc === "primary" ? null : badgeSrc === "secondary" ? secVal : badgeCustom;
      const bInk = ink === "auto" ? null : ink === "white" ? "#FFFFFF" : ink === "black" ? "#0B0B0B" : inkCustom;
      const qColor = quoteSrc === "badge" ? null : quoteSrc === "primary" ? primary : quoteSrc === "secondary" ? secVal : quoteSrc === "white" ? "#FEFEFC" : quoteCustom;

      const payload = {
        template_style: style,
        brand_override: override,
        accent_color: override ? primary : null,
        accent_secondary_color: override ? secVal : null,
        badge_color: override ? bColor : null,
        badge_text_color: override ? bInk : null,
        quote_mark_color: override ? qColor : null,
        quote_streak: override ? (style === "gp" ? streak : null) : null,
        default_news_template_id: effectiveId(advNewsId, styleNewsId) ?? null,
        default_quote_template_id: effectiveId(advQuoteId, styleQuoteId) ?? null,
        default_discussion_template_id: effectiveId(advDiscId, styleDiscId) ?? null,
      };
      await updateFanpage(fanpage.id, payload);
      setSaveMsg("Saved. New cards use this style and these colors.");
      setTimeout(() => setSaveMsg(null), 4000);
      onSaved?.();
    } catch {
      setSaveError("Save failed. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  const newsTpl = templateById(effectiveId(advNewsId, styleNewsId));
  const quoteTpl = templateById(effectiveId(advQuoteId, styleQuoteId));
  const discTpl = templateById(effectiveId(advDiscId, styleDiscId));

  return (
    <section className="card space-y-6">
      <div>
        <h2 className="text-xl font-bold text-text-primary">Design Templates</h2>
        <p className="text-sm text-text-secondary mt-1 max-w-3xl">
          Pick one style and this project&apos;s colors. The style gives a News, a Quote and a Discussion template; every mode that renders that kind of content uses them.
        </p>
      </div>

      <div className="space-y-3">
        <h3 className="text-xs font-bold uppercase tracking-wider text-text-secondary">Template style</h3>
        <div role="group" aria-label="Template style" className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {(["gp", "highlight", "classic"] as TemplateStyle[]).map((st) => {
            const on = style === st;
            return (
              <button
                key={st}
                type="button"
                onClick={() => {
                  setStyle(st);
                  setStyleTouched(true);
                }}
                aria-pressed={on}
                className={`flex flex-col gap-1 p-3.5 rounded-xl border text-left transition-colors ${
                  on ? "border-primary-main bg-primary-main/10 shadow-sm" : "border-hairline bg-bg-paper hover:bg-bg-paper-hover"
                }`}
              >
                <span className="text-sm font-bold text-text-primary">{STYLE_LABELS[st]}</span>
                <span className="text-xs text-text-secondary leading-snug">{STYLE_NOTES[st].note}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex flex-col lg:flex-row gap-8 items-start">
        <div className="w-full lg:w-[420px] flex-shrink-0 space-y-5">
          <h3 className="text-xs font-bold uppercase tracking-wider text-text-secondary">Colors</h3>
          <div role="group" aria-label="Where the colors come from" className="flex border border-hairline rounded-lg overflow-hidden w-fit">
            <button
              type="button"
              onClick={() => handleToggleOverride(false)}
              className={`px-3 py-2 text-xs font-semibold ${!override ? "bg-primary-main text-white" : "bg-bg-paper text-text-secondary hover:text-text-primary"}`}
            >
              Use template defaults
            </button>
            <button
              type="button"
              onClick={() => handleToggleOverride(true)}
              className={`px-3 py-2 text-xs font-semibold ${override ? "bg-primary-main text-white" : "bg-bg-paper text-text-secondary hover:text-text-primary"}`}
            >
              Override for this project
            </button>
          </div>

          {!override ? (
            <div className="p-4 rounded-xl border border-hairline bg-bg-paper-hover space-y-2.5">
              <div className="flex items-center gap-2.5">
                <span className="flex w-11 h-6 rounded-md overflow-hidden border border-hairline">
                  <span className="flex-1" style={{ background: defP }} />
                  <span className="flex-1" style={{ background: defS }} />
                </span>
                <span className="text-sm font-semibold text-text-primary">{STYLE_LABELS[style]} defaults</span>
                <span className="font-mono text-xs text-text-secondary">{defP === defS ? defP : `${defP} + ${defS}`}</span>
              </div>
              <p className="text-xs text-text-secondary leading-relaxed">
                Set once for every project in Settings › Design templates. This project follows them, so a change there shows here too.
              </p>
              <Link href="/templates" className="text-xs font-semibold text-primary-main hover:underline inline-block">
                Edit template defaults
              </Link>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="space-y-2">
                <span className="text-xs font-semibold text-text-primary">Quick presets</span>
                <div className="grid grid-cols-3 gap-2">
                  {PRESETS.map((p) => {
                    const on = p.a.toLowerCase() === primary.toLowerCase() && p.b.toLowerCase() === secondaryEff.toLowerCase();
                    return (
                      <button
                        key={p.label}
                        type="button"
                        onClick={() => {
                          setPrimary(p.a);
                          setSecondary(p.b);
                          setSame(p.a.toLowerCase() === p.b.toLowerCase());
                        }}
                        className={`flex flex-col gap-1.5 p-2 rounded-lg border text-left text-xs transition-colors ${
                          on ? "border-primary-main bg-primary-main/10 font-bold" : "border-hairline bg-bg-paper hover:bg-bg-paper-hover"
                        }`}
                      >
                        <span className="flex h-3.5 rounded overflow-hidden border border-hairline">
                          <span className="flex-1" style={{ background: p.a }} />
                          <span className="flex-1" style={{ background: p.b }} />
                        </span>
                        <span className="truncate">{p.label}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex justify-between items-baseline">
                  <span className="text-xs font-semibold text-text-primary">Primary</span>
                  <span className="font-mono text-xs text-text-secondary uppercase">{primary}</span>
                </div>
                <p className="text-xs text-text-secondary">Brand color. Badge and quote marks use it unless set below.</p>
                <div className="flex flex-wrap items-center gap-2">
                  {SWATCHES.map((s) => {
                    const on = s.hex.toLowerCase() === primary.toLowerCase();
                    return (
                      <button
                        key={s.hex}
                        type="button"
                        aria-label={s.label}
                        onClick={() => setPrimary(s.hex)}
                        className={`w-7 h-7 rounded-lg border border-hairline transition-transform ${on ? "ring-2 ring-primary-main ring-offset-2 scale-110" : "hover:scale-105"}`}
                        style={{ backgroundColor: s.hex }}
                      />
                    );
                  })}
                  <label htmlFor={primaryCustomId} className="flex items-center gap-1.5 text-xs text-text-secondary cursor-pointer ml-1">
                    <input id={primaryCustomId} type="color" value={primary} onChange={(e) => setPrimary(e.target.value.toUpperCase())} className="w-8 h-7 rounded border border-hairline cursor-pointer bg-transparent" />
                    Custom
                  </label>
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex justify-between items-baseline">
                  <span className="text-xs font-semibold text-text-primary">Secondary</span>
                  <span className="font-mono text-xs text-text-secondary uppercase">{secondaryEff}</span>
                </div>
                <p className="text-xs text-text-secondary">Highlighted words in the headline and the speaker line.</p>
                <label htmlFor={sameColorId} className="flex items-center gap-2 text-xs font-medium text-text-primary cursor-pointer py-1">
                  <input id={sameColorId} type="checkbox" checked={same} onChange={(e) => setSame(e.target.checked)} className="rounded border-hairline text-primary-main focus:ring-primary-main" />
                  Same as primary (single color)
                </label>
                {!same && (
                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    {SWATCHES.map((s) => {
                      const on = s.hex.toLowerCase() === secondary.toLowerCase();
                      return (
                        <button
                          key={s.hex}
                          type="button"
                          aria-label={s.label}
                          onClick={() => setSecondary(s.hex)}
                          className={`w-7 h-7 rounded-lg border border-hairline transition-transform ${on ? "ring-2 ring-primary-main ring-offset-2 scale-110" : "hover:scale-105"}`}
                          style={{ backgroundColor: s.hex }}
                        />
                      );
                    })}
                    <label htmlFor={secondaryCustomId} className="flex items-center gap-1.5 text-xs text-text-secondary cursor-pointer ml-1">
                      <input id={secondaryCustomId} type="color" value={secondary} onChange={(e) => setSecondary(e.target.value.toUpperCase())} className="w-8 h-7 rounded border border-hairline cursor-pointer bg-transparent" />
                      Custom
                    </label>
                  </div>
                )}
                {showTooDark && (
                  <div role="status" className="p-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-700 text-xs">
                    Secondary is very dark; highlighted words may disappear on the dark fade.
                  </div>
                )}
              </div>

              <details className="border border-hairline rounded-xl p-3.5 space-y-4">
                <summary className="font-semibold text-xs text-text-primary cursor-pointer select-none">Badge &amp; quote marks (optional)</summary>
                <div className="space-y-4 pt-2">
                  <div className="space-y-1.5">
                    <div className="flex justify-between items-baseline">
                      <span className="text-xs font-medium text-text-primary">Badge color</span>
                      <span className="font-mono text-xs text-text-secondary uppercase">{badgeFill}</span>
                    </div>
                    <div className="flex flex-wrap gap-2 items-center">
                      {(["primary", "secondary", "custom"] as const).map((opt) => (
                        <button
                          key={opt}
                          type="button"
                          onClick={() => setBadgeSrc(opt)}
                          className={`px-2.5 py-1 text-xs rounded-lg border font-medium capitalize ${badgeSrc === opt ? "border-primary-main bg-primary-main/10 text-primary-main" : "border-hairline bg-bg-paper text-text-secondary"}`}
                        >
                          {opt}
                        </button>
                      ))}
                      {badgeSrc === "custom" && (
                        <label htmlFor={badgeCustomId} className="flex items-center gap-1 text-xs text-text-secondary cursor-pointer">
                          <input id={badgeCustomId} type="color" value={badgeCustom} onChange={(e) => setBadgeCustom(e.target.value.toUpperCase())} className="w-7 h-6 rounded border border-hairline cursor-pointer bg-transparent" />
                          Pick
                        </label>
                      )}
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <div className="flex justify-between items-baseline">
                      <span className="text-xs font-medium text-text-primary">Badge text</span>
                      <span className="font-mono text-xs text-text-secondary uppercase">{badgeInk}</span>
                    </div>
                    <div className="flex flex-wrap gap-2 items-center">
                      {(["auto", "white", "black", "custom"] as const).map((opt) => (
                        <button
                          key={opt}
                          type="button"
                          onClick={() => setInk(opt)}
                          className={`px-2.5 py-1 text-xs rounded-lg border font-medium capitalize ${ink === opt ? "border-primary-main bg-primary-main/10 text-primary-main" : "border-hairline bg-bg-paper text-text-secondary"}`}
                        >
                          {opt}
                        </button>
                      ))}
                      {ink === "custom" && (
                        <label htmlFor={inkCustomId} className="flex items-center gap-1 text-xs text-text-secondary cursor-pointer">
                          <input id={inkCustomId} type="color" value={inkCustom} onChange={(e) => setInkCustom(e.target.value.toUpperCase())} className="w-7 h-6 rounded border border-hairline cursor-pointer bg-transparent" />
                          Pick
                        </label>
                      )}
                    </div>
                    {showLowContrast && (
                      <div role="status" className="p-2 rounded bg-amber-500/10 border border-amber-500/20 text-amber-700 text-xs">
                        Badge text may be hard to read on this badge color.
                      </div>
                    )}
                  </div>

                  <div className="space-y-1.5">
                    <div className="flex justify-between items-baseline">
                      <span className="text-xs font-medium text-text-primary">Quote marks</span>
                      <span className="font-mono text-xs text-text-secondary uppercase">{quoteColor}</span>
                    </div>
                    <p className="text-[11px] text-text-secondary">{STYLE_NOTES[style].quoteNote}</p>
                    <div className="flex flex-wrap gap-2 items-center">
                      {(["badge", "primary", "secondary", "white", "custom"] as const).map((opt) => (
                        <button
                          key={opt}
                          type="button"
                          onClick={() => setQuoteSrc(opt)}
                          className={`px-2.5 py-1 text-xs rounded-lg border font-medium ${quoteSrc === opt ? "border-primary-main bg-primary-main/10 text-primary-main" : "border-hairline bg-bg-paper text-text-secondary"}`}
                        >
                          {opt === "badge" ? "Same as badge" : opt === "white" ? "White #FEFEFC" : opt.charAt(0).toUpperCase() + opt.slice(1)}
                        </button>
                      ))}
                      {quoteSrc === "custom" && (
                        <label htmlFor={quoteCustomId} className="flex items-center gap-1 text-xs text-text-secondary cursor-pointer">
                          <input id={quoteCustomId} type="color" value={quoteCustom} onChange={(e) => setQuoteCustom(e.target.value.toUpperCase())} className="w-7 h-6 rounded border border-hairline cursor-pointer bg-transparent" />
                          Pick
                        </label>
                      )}
                    </div>
                    {style === "gp" && (
                      <label htmlFor={streakOnId} className="flex items-center gap-2 text-xs font-medium text-text-primary cursor-pointer pt-1">
                        <input id={streakOnId} type="checkbox" checked={streak} onChange={(e) => setStreak(e.target.checked)} className="rounded border-hairline text-primary-main focus:ring-primary-main" />
                        Show the streak behind the quote tab
                      </label>
                    )}
                  </div>
                </div>
              </details>
            </div>
          )}
        </div>

        <div className="flex-1 min-w-0 space-y-3 w-full">
          <div className="flex justify-between items-baseline">
            <h3 className="text-xs font-bold uppercase tracking-wider text-text-secondary">
              Preview · {STYLE_LABELS[style]}
            </h3>
            <span className="text-xs text-text-secondary">Sample text, no photo · updates live</span>
          </div>
          <div className="p-4 sm:p-6 rounded-2xl border border-hairline bg-bg-paper-hover flex flex-wrap gap-5 justify-center">
            <div className="flex flex-col gap-2 w-[220px]">
              <div className="relative w-[220px] aspect-[1080/1350] rounded-lg overflow-hidden bg-[#3A4152] shadow-sm">
                <TemplateThumbnail
                  json={newsTpl?.template_json || null}
                  width={newsTpl?.canvas_width || 1080}
                  height={newsTpl?.canvas_height || 1350}
                  look={resolvedLook}
                  label="BREAKING"
                  title="Rookie stuns the paddock with a **record lap** in qualifying"
                  className="w-full h-full object-cover"
                />
              </div>
              <span className="text-xs font-bold text-text-primary">News</span>
              <span className="text-[11px] text-text-secondary leading-tight">{CARD_USAGE.news}</span>
            </div>

            <div className="flex flex-col gap-2 w-[220px]">
              <div className="relative w-[220px] aspect-[1080/1350] rounded-lg overflow-hidden bg-[#3A4152] shadow-sm">
                <TemplateThumbnail
                  json={quoteTpl?.template_json || null}
                  width={quoteTpl?.canvas_width || 1080}
                  height={quoteTpl?.canvas_height || 1350}
                  look={resolvedLook}
                  label={null}
                  title="We gave everything today and the **bike felt perfect** from the first lap"
                  subtitle="Rider name, Team"
                  caption="Caption goes here, a short line of context"
                  className="w-full h-full object-cover"
                />
              </div>
              <span className="text-xs font-bold text-text-primary">Quote</span>
              <span className="text-[11px] text-text-secondary leading-tight">{CARD_USAGE.quote}</span>
            </div>

            <div className="flex flex-col gap-2 w-[220px]">
              <div className="relative w-[220px] aspect-[1080/1350] rounded-lg overflow-hidden bg-[#3A4152] shadow-sm">
                <TemplateThumbnail
                  json={discTpl?.template_json || null}
                  width={discTpl?.canvas_width || 1080}
                  height={discTpl?.canvas_height || 1350}
                  look={resolvedLook}
                  label="DISCUSSION"
                  title="Who takes the title if **rain hits** on Sunday?"
                  className="w-full h-full object-cover"
                />
              </div>
              <span className="text-xs font-bold text-text-primary">Discussion</span>
              <span className="text-[11px] text-text-secondary leading-tight">{CARD_USAGE.discussion}</span>
            </div>
          </div>
        </div>
      </div>

      <details className="border border-hairline rounded-xl p-4 space-y-3">
        <summary className="font-semibold text-sm text-text-primary cursor-pointer select-none">
          Use a different template for one category (advanced)
        </summary>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-2">
          <div>
            <label className="label">News template</label>
            <select
              className="input w-full mt-1 text-xs"
              value={effectiveId(advNewsId, styleNewsId) ?? ""}
              onChange={(e) => {
                const val = e.target.value ? parseInt(e.target.value) : null;
                setAdvNewsId(val === styleNewsId ? null : val);
              }}
            >
              <option value={styleNewsId || ""}>From style ({STYLE_LABELS[style]})</option>
              {getCategoryOptions("news", fanpage.default_news_template_id).map((t) => (
                <option key={t.id} value={t.id}>
                  {templateDisplayName(t)}{t.id === fanpage.default_news_template_id && t.is_hidden ? " (old)" : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Quote template</label>
            <select
              className="input w-full mt-1 text-xs"
              value={effectiveId(advQuoteId, styleQuoteId) ?? ""}
              onChange={(e) => {
                const val = e.target.value ? parseInt(e.target.value) : null;
                setAdvQuoteId(val === styleQuoteId ? null : val);
              }}
            >
              <option value={styleQuoteId || ""}>From style ({STYLE_LABELS[style]})</option>
              {getCategoryOptions("quote", fanpage.default_quote_template_id).map((t) => (
                <option key={t.id} value={t.id}>
                  {templateDisplayName(t)}{t.id === fanpage.default_quote_template_id && t.is_hidden ? " (old)" : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">Discussion template</label>
            <select
              className="input w-full mt-1 text-xs"
              value={effectiveId(advDiscId, styleDiscId) ?? ""}
              onChange={(e) => {
                const val = e.target.value ? parseInt(e.target.value) : null;
                setAdvDiscId(val === styleDiscId ? null : val);
              }}
            >
              <option value={styleDiscId || ""}>From style ({STYLE_LABELS[style]})</option>
              {getCategoryOptions("discussion", fanpage.default_discussion_template_id).map((t) => (
                <option key={t.id} value={t.id}>
                  {templateDisplayName(t)}{t.id === fanpage.default_discussion_template_id && t.is_hidden ? " (old)" : ""}
                </option>
              ))}
            </select>
          </div>
        </div>
        <p className="text-xs text-text-secondary">Colors above still apply when you mix templates.</p>
      </details>

      <div className="space-y-1 text-xs text-text-secondary">
        <p>Pinterest has no separate setting — it reuses the Quote/News templates above (face → Quote, no face → News).</p>
        <p>
          Create, edit or tag templates in{" "}
          <Link href="/templates" className="text-primary-main hover:underline">
            Template Designer
          </Link>.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3 pt-3 border-t border-hairline">
        <button type="button" onClick={handleSave} disabled={saving} className="btn-primary text-sm px-5 py-2">
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={handleReset} disabled={saving} className="btn-secondary text-sm px-4 py-2">
          Reset to template defaults
        </button>
        {saveMsg && <span role="status" className="text-xs text-green-600 font-medium">{saveMsg}</span>}
        {saveError && <span role="status" className="text-xs text-red-500 font-medium">{saveError}</span>}
      </div>
    </section>
  );
}
