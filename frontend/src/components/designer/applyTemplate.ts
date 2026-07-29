/**
 * Applies dynamic content to a loaded Fabric template EXACTLY like the headless
 * renderer (renderer/inject.js) so the thumbnail/editor preview matches the
 * published design: **word-highlight** markers, auto-fit (with the fabric
 * width-auto-expand undo), bottom-anchor, dynamic text scrim, subtitle, and an
 * optional cover-fit photo. Keep this in sync with renderer/inject.js.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

export function parseMarks(raw: string): { text: string; ranges: [number, number][] } {
  const ranges: [number, number][] = [];
  let out = "";
  let i = 0;
  while (i < raw.length) {
    if (raw[i] === "*" && raw[i + 1] === "*") {
      const end = raw.indexOf("**", i + 2);
      if (end < 0) { out += raw.slice(i); break; }
      const seg = raw.slice(i + 2, end);
      const start = out.length;
      out += seg;
      ranges.push([start, out.length]);
      i = end + 2;
    } else {
      out += raw[i];
      i++;
    }
  }
  return { text: out, ranges };
}

const applyCase = (t: string, mode: string | null): string =>
  mode === "uppercase" ? t.toUpperCase()
  : mode === "lowercase" ? t.toLowerCase()
  : mode === "capitalize" ? t.replace(/(^|\s)(\S)/g, (m, sep, ch) => sep + ch.toUpperCase())
  : t;

type ApplyArgs = {
  fabric: any;
  canvas: any;
  width: number;
  height: number;
  title?: string;
  subtitle?: string;
  caption?: string;
  imageSrc?: string | null;
  focusPoint?: [number, number];
  watermark?: string;
};

export async function applyTemplateContent(args: ApplyArgs): Promise<void> {
  const { fabric, canvas, width, height, title, subtitle, caption, imageSrc, focusPoint, watermark } = args;
  const objects = canvas.getObjects();

  // ── Title ──
  const titleObj = objects.find((o: any) => o.placeholderRole === "title");
  if (titleObj && title) {
    const mode = titleObj.titleTextTransform || (titleObj.titleUppercase ? "uppercase" : null);
    const parsed = parseMarks(String(title));
    const text = applyCase(parsed.text, mode);
    const maxHeight = titleObj.height;
    const fixedW = titleObj.width;
    titleObj.set({ text, styles: {} });
    titleObj.set("width", fixedW);
    titleObj.initDimensions();
    while ((titleObj.height > maxHeight || titleObj.width > fixedW + 1) && titleObj.fontSize > 12) {
      titleObj.set("fontSize", titleObj.fontSize - 2);
      titleObj.set("width", fixedW);
      titleObj.initDimensions();
    }
    titleObj.set("width", fixedW);
    const accent = titleObj.titleAccentColor;
    if (accent && parsed.ranges.length) {
      for (const [s, e] of parsed.ranges) titleObj.setSelectionStyles({ fill: accent }, s, e);
    } else if (accent) {
      let start = -1;
      const lineCount = titleObj.textLines.length;
      if (lineCount > 1) {
        const splitLine = Math.ceil(lineCount / 2);
        for (let i = 0; i < text.length; i++) {
          if (titleObj.get2DCursorLocation(i, false).lineIndex >= splitLine) { start = i; break; }
        }
      } else {
        const sp = text.indexOf(" ", Math.floor(text.length / 2));
        if (sp >= 0) start = sp + 1;
      }
      if (start > 0 && start < text.length) titleObj.setSelectionStyles({ fill: accent }, start, text.length);
    }
    if (typeof titleObj.titleAnchorBottom === "number") {
      titleObj.set("top", titleObj.titleAnchorBottom - titleObj.height);
    }
  }

  // ── Subtitle ──
  const subObj = objects.find((o: any) => o.placeholderRole === "subtitle");
  if (subObj && subtitle) {
    const mode = subObj.titleTextTransform || null;
    const parsed = parseMarks(String(subtitle));
    const text = applyCase(parsed.text, mode);
    const maxHeight = subObj.height;
    subObj.set({ text, styles: {} });
    while (subObj.height > maxHeight && subObj.fontSize > 8) {
      subObj.set("fontSize", subObj.fontSize - 1);
      subObj.initDimensions();
    }
    const accent = subObj.titleAccentColor;
    if (accent) for (const [s, e] of parsed.ranges) subObj.setSelectionStyles({ fill: accent }, s, e);

    // ── Name badge: hug the subtitle text's actual width and re-centre
    // vertically on it (subObj's height can shrink after the fit loop) ──
    const badge = objects.find((o: any) => o.placeholderRole === "subtitleBadge");
    if (badge) {
      const textW = subObj.calcTextWidth();
      const pad = typeof badge.badgePadX === "number" ? badge.badgePadX : 80;
      const newW = Math.max(badge.height, textW + pad);
      const cx = badge.left + badge.width / 2;
      const textCy = subObj.top + subObj.height / 2;
      badge.set({ width: newW, left: cx - newW / 2, top: textCy - badge.height / 2 });
    }
  }

  // ── Caption (small descriptive line under the name/badge) ──
  const captionObj = objects.find((o: any) => o.placeholderRole === "caption");
  if (captionObj && caption) {
    const mode = captionObj.titleTextTransform || null;
    const parsed = parseMarks(String(caption));
    const text = applyCase(parsed.text, mode);
    const maxHeight = captionObj.height;
    captionObj.set({ text, styles: {} });
    while (captionObj.height > maxHeight && captionObj.fontSize > 8) {
      captionObj.set("fontSize", captionObj.fontSize - 1);
      captionObj.initDimensions();
    }
    const accent = captionObj.titleAccentColor;
    if (accent) for (const [s, e] of parsed.ranges) captionObj.setSelectionStyles({ fill: accent }, s, e);
  }

  // ── Text scrim (hug the text) ──
  const scrim = objects.find((o: any) => o.placeholderRole === "scrim");
  if (scrim && titleObj) {
    const pad = typeof scrim.scrimPad === "number" ? scrim.scrimPad : 70;
    const topY = Math.max(0, titleObj.top - pad);
    const h = height - topY;
    scrim.set({ top: topY, height: h });
    if (scrim.fill && scrim.fill.coords) {
      scrim.fill.coords.y1 = 0;
      scrim.fill.coords.y2 = h;
      scrim.set("dirty", true);
    }
  }

  // ── Quote icon (hug the text, like the scrim) — a short quote sits lower
  // (less height needed above the bottom anchor), so a static icon position
  // would leave a growing gap; anchor the icon a fixed distance above
  // titleObj's actual (post-fit) top instead.
  const quoteIcon = objects.find((o: any) => o.placeholderRole === "quoteIcon");
  if (quoteIcon && titleObj) {
    const gap = typeof quoteIcon.iconGap === "number" ? quoteIcon.iconGap : 20;
    quoteIcon.set("top", titleObj.top - gap - quoteIcon.height);
  }

  // ── Photo (optional; cover-fit + focus + clip) ──
  const imageSlot = objects.find((o: any) => o.placeholderRole === "image");
  if (imageSlot && imageSrc) {
    await new Promise<void>((resolve) => {
      fabric.Image.fromURL(
        imageSrc,
        (img: any) => {
          if (!img || !img.width) { resolve(); return; }
          const slotW = imageSlot.getScaledWidth();
          const slotH = imageSlot.getScaledHeight();
          const cx = imageSlot.left + slotW / 2;
          const cy = imageSlot.top + slotH / 2;
          const idx = canvas.getObjects().indexOf(imageSlot);
          const scale = Math.max(slotW / img.width, slotH / img.height);
          const iw = img.width * scale;
          const ih = img.height * scale;
          const fp = focusPoint || [0.5, 0.42];
          let left = cx - fp[0] * iw;
          let top = cy - fp[1] * ih;
          left = Math.min(imageSlot.left, Math.max(imageSlot.left + slotW - iw, left));
          top = Math.min(imageSlot.top, Math.max(imageSlot.top + slotH - ih, top));
          img.set({ left, top, originX: "left", originY: "top", scaleX: scale, scaleY: scale, selectable: false });
          img.clipPath = new fabric.Rect({ left: imageSlot.left, top: imageSlot.top, width: slotW, height: slotH, absolutePositioned: true });
          canvas.remove(imageSlot);
          canvas.insertAt(img, idx < 0 ? canvas.getObjects().length : idx, false);
          resolve();
        },
        { crossOrigin: "anonymous" }
      );
    });
  }

  // ── Watermark (top-left text) ──
  // Textbox (not Text) so long watermark strings wrap instead of running off
  // the canvas edge; shrink the font until it wraps to at most 2 lines. Keep
  // in sync with renderer/inject.js's addWatermark.
  if (watermark) {
    const wm = new fabric.Textbox(String(watermark).toUpperCase(), {
      left: Math.round(width * 0.052),
      top: Math.round(width * 0.045),
      width: Math.round(width * 0.6),
      fontFamily: "Poppins",
      fontWeight: "bold",
      fontSize: Math.round(width * 0.032),
      lineHeight: 1.15,
      fill: "rgba(255,255,255,0.6)",
      charSpacing: 60,
      selectable: false,
    });
    wm.initDimensions();
    while (wm.textLines.length > 2 && wm.fontSize > 10) {
      wm.set("fontSize", wm.fontSize - 1);
      wm.initDimensions();
    }
    canvas.add(wm);
  }

  canvas.renderAll();
}
