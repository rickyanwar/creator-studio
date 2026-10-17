import type { BrandLook, StyleDefaults, TemplateStyle } from "./designTemplatesApi";

export const SWATCHES = [
  { hex: "#E8362B", label: "Racing red" },
  { hex: "#F97316", label: "Orange" },
  { hex: "#FFC21A", label: "Gold" },
  { hex: "#C7F754", label: "Lime" },
  { hex: "#16A34A", label: "Green" },
  { hex: "#2563EB", label: "Blue" },
  { hex: "#7C3AED", label: "Purple" },
  { hex: "#F8FAFC", label: "White" },
] as const;

export const PRESETS = [
  { a: "#E8362B", b: "#E8362B", label: "Racing red" },
  { a: "#E8362B", b: "#FFC21A", label: "Red + gold" },
  { a: "#C7F754", b: "#C7F754", label: "Lime" },
  { a: "#2563EB", b: "#FFC21A", label: "Blue + gold" },
  { a: "#16A34A", b: "#A3E635", label: "Green + lime" },
  { a: "#7C3AED", b: "#F472B6", label: "Purple + pink" },
] as const;

export const STYLE_LABELS: Record<TemplateStyle, string> = {
  gp: "GP Racing",
  highlight: "Highlight",
  classic: "Classic",
};

export const STYLE_NOTES: Record<TemplateStyle, { note: string; quoteNote: string }> = {
  gp: {
    note: "GP News — Breaking · GP Quote · GP Discussion",
    quoteNote: "GP Racing: the small tab and the splatter streak behind it.",
  },
  highlight: {
    note: "News Highlight — Green · Center (Quote overlay) · Quote Card (word-highlight) · Highlight Discussion",
    quoteNote: "Highlight: the big quote marks above the quote (white in the original).",
  },
  classic: {
    note: "News Highlight — Red · Center · Quote Card (name badge) · Classic Discussion",
    quoteNote: "Classic: the big quote marks above the quote.",
  },
};

export const CARD_USAGE: Record<"news" | "quote" | "discussion", string> = {
  news: "Mode 2 news, IG recreate (news), Pinterest without a face",
  quote: "IG recreate (quote), Pinterest with a face",
  discussion: "Mode 4 debate cards",
};

const LOOK_HEX = /^#[0-9A-Fa-f]{6}$/;

export function isHex(v: unknown): v is string {
  return typeof v === "string" && /^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$/.test(v);
}

export function toHex(v: unknown, fallback = "#E8362B"): string {
  if (typeof v !== "string") return fallback.toUpperCase();
  let h = v.trim();
  if (!h.startsWith("#")) h = "#" + h;
  if (/^#[0-9A-Fa-f]{3}$/.test(h)) {
    h = "#" + h[1] + h[1] + h[2] + h[2] + h[3] + h[3];
  }
  return LOOK_HEX.test(h) ? h.toUpperCase() : fallback.toUpperCase();
}

export function hexToRgb(hex: string): [number, number, number] {
  let h = String(hex || "").replace(/^#/, "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const n = parseInt(h, 16);
  if (h.length !== 6 || isNaN(n)) return [232, 54, 43];
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(a: string, b: string): number {
  const l1 = luminance(a);
  const l2 = luminance(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

export function autoInk(hex: string): string {
  return luminance(hex) > 0.5 ? "#0B0B0B" : "#FFFFFF";
}

export function resolveLook(
  src: Partial<{
    primary: string | null;
    secondary: string | null;
    badge: string | null;
    badgeText: string | null;
    quoteMarks: string | null;
    streak: boolean | null;
  }>,
  fallbackPrimary = "#E8362B"
): BrandLook {
  const pick = (v: unknown, fb: string) => (isHex(v) ? toHex(v) : fb);
  const primary = pick(src.primary, toHex(fallbackPrimary, "#E8362B"));
  const secondary = pick(src.secondary, primary);
  const badge = pick(src.badge, primary);
  const badgeText = pick(src.badgeText, autoInk(badge));
  const quoteMarks = pick(src.quoteMarks, badge);
  const streak = src.streak !== false;
  return { primary, secondary, badge, badgeText, quoteMarks, streak };
}

export function lookForFanpage(
  fp: {
    brand_override?: boolean | null;
    accent_color?: string | null;
    accent_secondary_color?: string | null;
    badge_color?: string | null;
    badge_text_color?: string | null;
    quote_mark_color?: string | null;
    quote_streak?: boolean | null;
  },
  styleDefaults?: StyleDefaults
): BrandLook {
  if (fp.brand_override === true && isHex(fp.accent_color)) {
    return resolveLook(
      {
        primary: fp.accent_color,
        secondary: fp.accent_secondary_color,
        badge: fp.badge_color,
        badgeText: fp.badge_text_color,
        quoteMarks: fp.quote_mark_color,
        streak: fp.quote_streak,
      },
      styleDefaults?.primary ?? "#E8362B"
    );
  }
  return resolveLook(
    {
      primary: styleDefaults?.primary,
      secondary: styleDefaults?.secondary,
      badge: styleDefaults?.badge,
      badgeText: styleDefaults?.badgeText,
      quoteMarks: styleDefaults?.quoteMarks,
      streak: styleDefaults?.streak,
    },
    styleDefaults?.primary ?? "#E8362B"
  );
}

export function templateDisplayName(t: {
  name: string;
  style?: string | null;
  category?: string | null;
}): string {
  if (t.style === "gp") return t.name;
  if ((t.style === "highlight" || t.style === "classic") && t.category) {
    const cat = t.category.charAt(0).toUpperCase() + t.category.slice(1).toLowerCase();
    const prefix = STYLE_LABELS[t.style as TemplateStyle] || t.style;
    return `${prefix} ${cat}`;
  }
  return t.name;
}
