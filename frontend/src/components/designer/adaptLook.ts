import type { BrandLook } from "@/lib/designTemplatesApi";

function hexToRgb(h: string): [number, number, number] {
  h = String(h).replace(/^#/, "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function rgbToHex(r: number, g: number, b: number): string {
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0").toUpperCase();
  return `#${c(r)}${c(g)}${c(b)}`;
}

function relativeLuminance(rgb: [number, number, number]): number {
  const chan = (c: number) => {
    c = c / 255.0;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * chan(rgb[0]) + 0.7152 * chan(rgb[1]) + 0.0722 * chan(rgb[2]);
}

function rgbToHls(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0, s = 0;
  if (max === min) {
    h = s = 0;
  } else {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r: h = (g - b) / d + (g < b ? 6 : 0); break;
      case g: h = (b - r) / d + 2; break;
      case b: h = (r - g) / d + 4; break;
    }
    h /= 6;
  }
  return [h, l, s];
}

function hlsToRgb(h: number, l: number, s: number): [number, number, number] {
  let r: number, g: number, b: number;
  if (s === 0) {
    r = g = b = l;
  } else {
    const hue2rgb = (p: number, q: number, t: number) => {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, h + 1 / 3);
    g = hue2rgb(p, q, h);
    b = hue2rgb(p, q, h - 1 / 3);
  }
  return [r * 255, g * 255, b * 255];
}

function adjustLightness(rgb: [number, number, number], deltaL: number): [number, number, number] {
  const [h, l, s] = rgbToHls(rgb[0], rgb[1], rgb[2]);
  const newL = Math.max(0, Math.min(1, l + deltaL));
  return hlsToRgb(h, newL, s);
}

export function tintStreakDataUri(srcDataUri: string, accentHex: string): Promise<string> {
  return new Promise((resolve) => {
    if (!srcDataUri || typeof document === "undefined") {
      resolve(srcDataUri);
      return;
    }
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width = img.width;
        c.height = img.height;
        const ctx = c.getContext("2d");
        if (!ctx) {
          resolve(srcDataUri);
          return;
        }
        ctx.drawImage(img, 0, 0);
        const imgData = ctx.getImageData(0, 0, c.width, c.height);
        const data = imgData.data;
        const [tr, tg, tb] = hexToRgb(accentHex);
        for (let i = 0; i < data.length; i += 4) {
          const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
          if (a === 0) continue;
          // Grey/luma relative brightness (same as PIL gray = im.convert("L") / 255.0)
          const gray = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0;
          data[i] = Math.max(0, Math.min(255, Math.round(gray * tr)));
          data[i + 1] = Math.max(0, Math.min(255, Math.round(gray * tg)));
          data[i + 2] = Math.max(0, Math.min(255, Math.round(gray * tb)));
        }
        ctx.putImageData(imgData, 0, 0);
        resolve(c.toDataURL("image/png"));
      } catch {
        resolve(srcDataUri);
      }
    };
    img.onerror = () => resolve(srcDataUri);
    img.src = srcDataUri;
  });
}

export function adaptLook(templateJson: any, look: BrandLook): any {
  if (!templateJson || !look) return templateJson;
  const tpl = JSON.parse(JSON.stringify(templateJson));

  const isDarkInk = (hex: string) => relativeLuminance(hexToRgb(hex)) <= 0.5;
  const gradStops = (hex: string) => [
    { offset: 0, color: rgbToHex(...adjustLightness(hexToRgb(hex), 0.06)) },
    { offset: 1, color: rgbToHex(...adjustLightness(hexToRgb(hex), -0.06)) },
  ];
  const strokeOf = (hex: string) => {
    const s = adjustLightness(hexToRgb(hex), 0.25);
    return `rgba(${Math.round(s[0])},${Math.round(s[1])},${Math.round(s[2])},0.55)`;
  };
  const secRgb = hexToRgb(look.secondary);
  const glow = `rgba(${secRgb[0]},${secRgb[1]},${secRgb[2]},0.40)`;
  const tabInk = look.quoteMarks === look.badge
    ? look.badgeText
    : (relativeLuminance(hexToRgb(look.quoteMarks)) > 0.5 ? "#0B0B0B" : "#FFFFFF");
  const hasSubtitleBadge = (tpl.objects || []).some((o: any) => o && o.placeholderRole === "subtitleBadge");

  for (const obj of (tpl.objects || [])) {
    if (!obj) continue;
    const role = obj.placeholderRole;
    if (role === "title") {
      const blur = (obj.titleHighlightGlow && typeof obj.titleHighlightGlow.blur === "number")
        ? obj.titleHighlightGlow.blur
        : 10;
      obj.titleAccentColor = look.secondary;
      obj.titleHighlightGlow = { color: glow, blur };
    } else if (role === "caption") {
      if (obj.titleAccentColor) {
        obj.titleAccentColor = look.secondary;
      }
      if (obj.captionHighlightGlow) {
        const blur = typeof obj.captionHighlightGlow.blur === "number"
          ? obj.captionHighlightGlow.blur
          : 8;
        obj.captionHighlightGlow = { color: glow, blur };
      }
    } else if (role === "subtitle") {
      if (obj.titleAccentColor) {
        obj.titleAccentColor = look.secondary;
      }
      if (hasSubtitleBadge) {
        obj.fill = look.badgeText;
        if (isDarkInk(look.badgeText)) {
          obj.shadow = { color: "rgba(0,0,0,0)", blur: 0, offsetX: 0, offsetY: 0 };
        }
      }
    } else if (role === "labelBadge") {
      if (obj.fill && typeof obj.fill === "object" && obj.fill.colorStops) {
        obj.fill.colorStops = gradStops(look.badge);
        obj.stroke = strokeOf(look.badge);
      } else {
        obj.fill = look.badge;
      }
      obj.discussionFill = look.badge;
      obj.hotFill = look.badge;
    } else if (role === "label") {
      obj.fill = look.badgeText;
      obj.discussionColor = look.badgeText;
      obj.hotColor = look.badgeText;
      if (isDarkInk(look.badgeText)) {
        obj.shadow = { color: "rgba(0,0,0,0)", blur: 0, offsetX: 0, offsetY: 0 };
      }
    } else if (role === "subtitleBadge") {
      obj.fill = look.badge;
    } else if (role === "quoteIcon") {
      if (Array.isArray(obj.objects)) {
        for (const child of obj.objects) {
          if (!child) continue;
          const ctype = child.type;
          if (ctype === "rect") {
            if (child.fill && typeof child.fill === "object" && child.fill.colorStops) {
              child.fill.colorStops = gradStops(look.quoteMarks);
              child.stroke = strokeOf(look.quoteMarks);
            } else {
              child.fill = look.quoteMarks;
            }
          } else if (ctype === "path") {
            child.fill = tabInk;
            if (isDarkInk(tabInk)) {
              child.shadow = { color: "rgba(0,0,0,0)", blur: 0, offsetX: 0, offsetY: 0 };
            }
          } else if (ctype === "image") {
            if (look.streak === false) {
              child.visible = false;
            }
          }
        }
      } else {
        obj.fill = look.quoteMarks;
      }
    }
  }
  return tpl;
}
