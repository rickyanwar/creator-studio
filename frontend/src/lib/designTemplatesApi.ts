import { api } from "./api";

export type TemplateStyle = "gp" | "highlight" | "classic";

export interface StyleDefaults {
  primary?: string;
  secondary?: string;
  badge?: string;
  badgeText?: string;
  quoteMarks?: string;
  streak?: boolean;
}

export interface StyleTemplateRef {
  id: number;
  name: string;
}

export interface StyleInfo {
  style: TemplateStyle;
  label: string;
  defaults: StyleDefaults;
  templates: {
    news: StyleTemplateRef | null;
    quote: StyleTemplateRef | null;
    discussion: StyleTemplateRef | null;
  };
}

export interface BrandLook {
  primary: string;
  secondary: string;
  badge: string;
  badgeText: string;
  quoteMarks: string;
  streak: boolean;
}

export const listTemplateStyles = (): Promise<StyleInfo[]> =>
  api.get("/templates/styles").then((r) => r.data);

export const updateStyleDefaults = (
  style: TemplateStyle,
  body: {
    primary: string;
    secondary?: string | null;
    badge?: string | null;
    badgeText?: string | null;
    quoteMarks?: string | null;
    streak?: boolean | null;
  }
): Promise<StyleInfo> =>
  api.put(`/templates/styles/${style}/defaults`, body).then((r) => r.data);
