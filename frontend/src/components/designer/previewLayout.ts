/**
 * Layout and font loading helpers for template previews, matching renderer/inject.js
 */

export function templateFonts(json: any): string[] {
  if (!json || !Array.isArray(json.objects)) return [];
  const fonts = new Set<string>();

  function walk(objs: any[]) {
    for (const obj of objs) {
      if (!obj) continue;
      if (typeof obj.fontFamily === "string" && obj.fontFamily.trim()) {
        const family = obj.fontFamily.replace(/^['"]|['"]$/g, "").trim();
        const weight = obj.fontWeight ?? 400;
        fonts.add(`${weight} 48px "${family}"`);
      }
      if (Array.isArray(obj.objects)) {
        walk(obj.objects);
      }
    }
  }

  walk(json.objects);
  return Array.from(fonts);
}

export async function loadTemplateFonts(json: any): Promise<void> {
  if (typeof document === "undefined" || !document.fonts || typeof document.fonts.load !== "function") {
    return;
  }
  const fonts = templateFonts(json);
  if (!fonts.length) return;
  try {
    await Promise.all(fonts.map((f) => document.fonts.load(f).catch(() => [])));
  } catch {
    /* never throw */
  }
}

export function layoutLikeRenderer(
  canvas: any,
  width: number,
  height: number,
  label: string | null | undefined
): void {
  if (!canvas || typeof canvas.getObjects !== "function") return;

  const objects: any[] = canvas.getObjects();
  const titleObj = objects.find((o) => o && o.placeholderRole === "title");
  const labelObj = objects.find((o) => o && o.placeholderRole === "label");
  const labelBadge = objects.find((o) => o && o.placeholderRole === "labelBadge");
  const quoteIcon = objects.find((o) => o && o.placeholderRole === "quoteIcon");
  const scrim = objects.find((o) => o && o.placeholderRole === "scrim");

  // a) label + labelBadge (only when a label object exists)
  if (labelObj) {
    const isLabelEmpty =
      label !== undefined
        ? !label || !label.trim()
        : !labelObj.text || !String(labelObj.text).trim();

    if (isLabelEmpty) {
      labelObj.set({ visible: false });
      labelObj.setCoords?.();
      if (labelBadge) {
        labelBadge.set({ visible: false });
        labelBadge.setCoords?.();
      }
    } else {
      const padX = typeof labelObj.labelPadX === "number" ? labelObj.labelPadX : 24;
      const padY = typeof labelObj.labelPadY === "number" ? labelObj.labelPadY : 12;
      const gap = typeof labelObj.labelGap === "number" ? labelObj.labelGap : 26;

      if (titleObj) {
        labelObj.set("top", titleObj.top - gap - labelObj.height);
      }

      labelObj.set({ width: 2000 });
      labelObj.initDimensions?.();
      const textW = Math.ceil(
        typeof labelObj.calcTextWidth === "function"
          ? labelObj.calcTextWidth()
          : labelObj.width || 0
      );

      const centered = labelObj.textAlign === "center";
      if (centered) {
        labelObj.set({ width: textW + 2, left: Math.round((width - (textW + 2)) / 2) });
      } else {
        labelObj.set({ width: textW + 2 });
      }
      labelObj.initDimensions?.();

      if (labelObj.textLines && labelObj.textLines.length > 1) {
        const w2 =
          Math.ceil(
            typeof labelObj.calcTextWidth === "function"
              ? labelObj.calcTextWidth()
              : labelObj.width || 0
          ) + 20;
        labelObj.set({
          width: w2,
          left: centered ? Math.round((width - w2) / 2) : labelObj.left,
        });
        labelObj.initDimensions?.();
      }

      if (labelBadge) {
        const naturalW = textW + 2 + 2 * padX;
        const badgeW =
          typeof labelBadge.labelFixedWidth === "number"
            ? Math.max(labelBadge.labelFixedWidth, naturalW)
            : naturalW;
        const badgeH =
          typeof labelBadge.labelFixedHeight === "number"
            ? labelBadge.labelFixedHeight
            : labelObj.height + 2 * padY;

        let radius: number;
        if (labelBadge.labelRadius === "pill" || labelBadge.labelRadius === -1) {
          radius = badgeH / 2;
        } else if (typeof labelBadge.labelRadius === "number") {
          radius = labelBadge.labelRadius;
        } else {
          radius = Math.round(badgeH * 0.28);
        }

        const badgeLeft = centered ? Math.round((width - badgeW) / 2) : labelObj.left - padX;
        const badgeTop = labelObj.top - padY;
        labelBadge.set({
          width: badgeW,
          height: badgeH,
          rx: radius,
          ry: radius,
          left: badgeLeft,
          top: badgeTop,
        });

        if (centered) {
          labelObj.set("top", labelBadge.top + Math.round((badgeH - labelObj.height) / 2));
        }

        labelBadge.setCoords?.();
      }
      labelObj.setCoords?.();
    }
  }

  // b) quoteIcon (when it and a title exist)
  if (quoteIcon && titleObj) {
    const gap = typeof quoteIcon.iconGap === "number" ? quoteIcon.iconGap : 20;
    const h = quoteIcon.height || 0;
    const top =
      quoteIcon.originY === "bottom"
        ? titleObj.top - gap
        : quoteIcon.originY === "center"
        ? titleObj.top - gap - h / 2
        : titleObj.top - gap - h;
    quoteIcon.set("top", top);
    quoteIcon.setCoords?.();
  }

  // c) scrim (when present)
  if (scrim) {
    const pad = typeof scrim.scrimPad === "number" ? scrim.scrimPad : 70;
    let highestY: number | null = null;
    const candidates = [titleObj, labelObj, labelBadge, quoteIcon];
    for (const cand of candidates) {
      if (cand && cand.visible !== false && typeof cand.top === "number") {
        if (highestY === null || cand.top < highestY) {
          highestY = cand.top;
        }
      }
    }
    if (highestY !== null) {
      const topY = Math.max(0, highestY - pad);
      const h = height - topY;
      scrim.set({ top: topY, height: h });
      if (scrim.fill && typeof scrim.fill === "object" && scrim.fill.coords) {
        scrim.fill.coords.y1 = 0;
        scrim.fill.coords.y2 = h;
        scrim.set("dirty", true);
      }
      scrim.setCoords?.();
    }
  }
}
