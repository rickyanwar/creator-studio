/**
 * Runs inside the Puppeteer page (browser context, global `fabric`).
 *
 * Loads a Fabric.js template JSON, injects the article headline into the
 * layer marked placeholderRole="title" and the photo into the slot marked
 * placeholderRole="image" (cover-fit + clipped to the slot bounds), then
 * returns the canvas PNG as a data URL.
 *
 * NOTE: the frontend designer (frontend/src/components/designer/) implements
 * the same placeholder contract for its live preload — keep the two in sync.
 */

function hexToRgb(h) {
  h = String(h).replace(/^#/, "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function rgbToHex(r, g, b) {
  const c = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0").toUpperCase();
  return `#${c(r)}${c(g)}${c(b)}`;
}

function relativeLuminance(rgb) {
  const chan = (c) => {
    c = c / 255.0;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * chan(rgb[0]) + 0.7152 * chan(rgb[1]) + 0.0722 * chan(rgb[2]);
}

function rgbToHls(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  let h, l = (max + min) / 2, s;
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

function hlsToRgb(h, l, s) {
  let r, g, b;
  if (s === 0) {
    r = g = b = l;
  } else {
    const hue2rgb = (p, q, t) => {
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

function adjustLightness(rgb, deltaL) {
  const [h, l, s] = rgbToHls(rgb[0], rgb[1], rgb[2]);
  const newL = Math.max(0, Math.min(1, l + deltaL));
  return hlsToRgb(h, newL, s);
}

function recolorStreakDataUri(srcDataUri, accentHex) {
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
      } catch (e) {
        resolve(srcDataUri);
      }
    };
    img.onerror = () => resolve(srcDataUri);
    img.src = srcDataUri;
  });
}

function adaptTemplateForAccent(templateJson, accentHex) {
  const tpl = JSON.parse(JSON.stringify(templateJson));
  const rgb = hexToRgb(accentHex);
  const lum = relativeLuminance(rgb);
  const isLight = lum > 0.5;
  const darkTextColor = "#0B0B0B";

  const topColor = rgbToHex(...adjustLightness(rgb, 0.06));
  const botColor = rgbToHex(...adjustLightness(rgb, -0.06));
  const strokeRgb = adjustLightness(rgb, 0.20);
  const strokeColor = `rgba(${Math.round(strokeRgb[0])},${Math.round(strokeRgb[1])},${Math.round(strokeRgb[2])},0.35)`;
  const accentGlowColor = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.40)`;

  const objects = tpl.objects || [];
  for (const obj of objects) {
    const role = obj.placeholderRole;
    if (role === "title") {
      obj.titleAccentColor = accentHex;
      obj.titleHighlightGlow = { color: accentGlowColor, blur: 10 };
    } else if (role === "caption") {
      obj.titleAccentColor = accentHex;
      obj.captionHighlightGlow = { color: accentGlowColor, blur: 10 };
    } else if (role === "labelBadge") {
      obj.stroke = strokeColor;
      if (obj.fill && typeof obj.fill === "object" && obj.fill.colorStops) {
        obj.fill.colorStops = [
          { offset: 0, color: topColor },
          { offset: 1, color: botColor },
        ];
      }
    } else if (role === "label") {
      if (isLight) {
        obj.fill = darkTextColor;
        obj.shadow = { color: "rgba(0,0,0,0)", blur: 0, offsetX: 0, offsetY: 0 };
      }
    } else if (role === "quoteIcon") {
      for (const child of (obj.objects || [])) {
        const ctype = child.type;
        if (ctype === "rect") {
          child.stroke = strokeColor;
          if (child.fill && typeof child.fill === "object" && child.fill.colorStops) {
            child.fill.colorStops = [
              { offset: 0, color: topColor },
              { offset: 1, color: botColor },
            ];
          }
        } else if (ctype === "path") {
          if (isLight) {
            child.fill = darkTextColor;
            child.shadow = { color: "rgba(0,0,0,0)", blur: 0, offsetX: 0, offsetY: 0 };
          }
        }
      }
    }
  }
  return tpl;
}

window.adaptTemplateForAccent = adaptTemplateForAccent;
window.recolorStreakDataUri = recolorStreakDataUri;

window.renderTemplate = function renderTemplate(args) {
  const { templateJson, width, height, title, subtitle, caption, label, watermark, watermarkImage, imageSrc, imageSrcs, focusPoints, imageZooms, accent } = args;

  // Watermark drawn on EVERY design (branding), top-left, on top of everything.
  // An IMAGE (logo) takes priority when provided; otherwise a semi-transparent
  // text handle. Async because the logo has to load first.
  async function addWatermark(canvas) {
    if (watermarkImage) {
      const img = await new Promise((res) =>
        fabric.Image.fromURL(watermarkImage, (im) => res(im && im.width ? im : null), { crossOrigin: "anonymous" })
      );
      if (img) {
        const targetW = Math.round(width * 0.20);           // ~20% of the canvas width
        const sc = targetW / img.width;
        img.set({
          left: Math.round(width * 0.05),
          top: Math.round(width * 0.045),
          scaleX: sc, scaleY: sc,
          opacity: 0.92,
          selectable: false,
          shadow: new fabric.Shadow({ color: "rgba(0,0,0,0.4)", blur: 10, offsetX: 0, offsetY: 1 }),
        });
        canvas.add(img);
        canvas.renderAll();
        return;
      }
    }
    if (watermark) {
      // Textbox (not Text) so long watermark strings wrap instead of running
      // off the canvas edge. Auto-fit: shrink the font until the text wraps
      // to at most 2 lines within the box, so a short handle stays big and a
      // longer tagline (e.g. a full sentence) shrinks and wraps to 2 lines.
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
        shadow: new fabric.Shadow({ color: "rgba(0,0,0,0.55)", blur: 8, offsetX: 0, offsetY: 1 }),
      });
      wm.initDimensions();
      while (wm.textLines.length > 2 && wm.fontSize > 10) {
        wm.set("fontSize", wm.fontSize - 1);
        wm.initDimensions();
      }
      canvas.add(wm);
      canvas.renderAll();
    }
  }
  const finalize = async (canvas) => {
    await addWatermark(canvas);
    return canvas.toDataURL({ format: "png", multiplier: outScale });
  };
  // Output multiplier — renders the canvas at `scale`× its design size for a
  // sharper, higher-resolution PNG (e.g. 2 → 2160×2700 for a 1080×1350 design).
  const outScale = Number(args.scale) > 0 ? Number(args.scale) : 1;

  // Parse **highlight** markers → clean text + the char ranges to accent-colour.
  // The copywriter wraps the words to emphasise in ** ** (chosen from the news).
  function parseMarks(raw) {
    const ranges = [];
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
  const applyCase = (t, mode) =>
    mode === "uppercase" ? t.toUpperCase()
    : mode === "lowercase" ? t.toLowerCase()
    : mode === "capitalize" ? t.replace(/(^|\s)(\S)/g, (m, sep, ch) => sep + ch.toUpperCase())
    : t;

  return new Promise((resolve, reject) => {
    // If accent is set and template opts in, adapt template and streak
    const prepTemplate = async () => {
      let tpl = templateJson;
      if (accent && templateJson && templateJson.accentFollowsFanpage) {
        tpl = adaptTemplateForAccent(templateJson, accent);
        // Recolor streak image in quoteIcon if present
        for (const obj of (tpl.objects || [])) {
          if (obj.placeholderRole === "quoteIcon") {
            for (const child of (obj.objects || [])) {
              if (child.type === "image" && child.src) {
                child.src = await recolorStreakDataUri(child.src, accent);
              }
            }
          }
        }
      }
      return tpl;
    };

    // Ensure Montserrat weights 700, 800, and 900 are fully loaded before layout calculations
    const ensureFonts = (typeof document !== "undefined" && document.fonts)
      ? Promise.all([
          document.fonts.load("700 44px Montserrat"),
          document.fonts.load("800 64px Montserrat"),
          document.fonts.load("800 76px Montserrat"),
          document.fonts.load("800 78px Montserrat"),
          document.fonts.load("900 64px Montserrat"),
          document.fonts.load("900 76px Montserrat"),
        ])
      : Promise.resolve();

    Promise.all([ensureFonts, prepTemplate()]).then(([_, finalTemplateJson]) => {
      const canvas = new fabric.StaticCanvas("c", { width, height });

      canvas.loadFromJSON(finalTemplateJson, () => {
        try {
          const objects = canvas.getObjects();

        // ── Title placeholder ──
        // Auto-fit: the placeholder's designed height is the budget; if the
        // injected headline wraps taller than that, step the font size down.
        const titleObj = objects.find((o) => o.placeholderRole === "title");
        if (titleObj && title) {
          // titleTextTransform: "uppercase" | "lowercase" | "capitalize";
          // legacy templates may carry titleUppercase=true instead
          const mode = titleObj.titleTextTransform || (titleObj.titleUppercase ? "uppercase" : null);
          // Parse **...** markers → clean text + per-word red ranges (case
          // transform preserves indices since it doesn't change length).
          const parsed = parseMarks(String(title));
          const text = applyCase(parsed.text, mode);
          // Auto-fit: the template fontSize is the MAX; shrink until the block
          // fits the area (maxHeight) AND no line is wider than the box (a long
          // word must never spill past the frame). So short headlines stay big
          // and long ones scale down — always filling the width, never overflowing.
          // Auto-fit: the template fontSize is the MAX. Shrink until the block
          // fits the area (maxHeight) AND fabric no longer has to auto-expand the
          // box width — which it does when a single word is wider than the box
          // (that expansion is what caused text to spill past the frame). So
          // short headlines stay big, long ones scale down, none overflow.
          const maxHeight = typeof titleObj.titleGrowMaxHeight === "number" ? titleObj.titleGrowMaxHeight : titleObj.height;
          const minReadableFontSize = typeof titleObj.titleMinReadableFontSize === "number" ? titleObj.titleMinReadableFontSize : null;
          const fixedW = titleObj.width;
          titleObj.set({ text, styles: {} });
          titleObj.set("width", fixedW);
          titleObj.initDimensions();
          // First pass: prefer stopping at titleMinReadableFontSize if configured
          if (minReadableFontSize && titleObj.fontSize > minReadableFontSize) {
            while ((titleObj.height > maxHeight || titleObj.width > fixedW + 1) && titleObj.fontSize > minReadableFontSize) {
              const step = titleObj.titleFontSizeStep || 2;
              titleObj.set("fontSize", Math.max(minReadableFontSize, titleObj.fontSize - step));
              titleObj.set("width", fixedW);
              titleObj.initDimensions();
            }
          }
          // Second pass / standard pass: if still overflowing, keep shrinking down to 12 (never truncate)
          while ((titleObj.height > maxHeight || titleObj.width > fixedW + 1) && titleObj.fontSize > 12) {
            const step = titleObj.titleFontSizeStep || 2;
            titleObj.set("fontSize", titleObj.fontSize - step);
            titleObj.set("width", fixedW);   // undo fabric's word-overflow auto-expand
            titleObj.initDimensions();
          }
          // Opt-in line-count preference (e.g. Mode 4 discussion cards, user
          // request 2026-08-16): the height-fit loop above only shrinks until
          // the block fits its box, which for a tall box can happily settle
          // on 4+ comfortably-sized lines. When titlePreferMaxLines is set:
          //   1) try to reach `target` (2) lines, but not below
          //      titlePreferredMinFontSize — a readability floor, so a
          //      2-line result isn't forced smaller than necessary just to
          //      hit the number.
          //   2) if `target` isn't reachable within that floor, fall back to
          //      the largest font that gave <= titleFallbackMaxLines (3)
          //      lines during the descent above.
          //   3) if the text STILL doesn't fit `fallback` lines (or overflows
          //      the box) even at the readable floor, keep shrinking past
          //      it — all the way to the absolute floor (12) if truly
          //      needed. The text is NEVER truncated (explicit, repeated
          //      user requirement, 2026-08-16 round 3: full text always,
          //      shrink the font instead, never let it touch the badge above
          //      or spill outside the canvas) — a smaller-than-ideal font on
          //      an unrealistically long headline is preferable to losing
          //      words. This mirrors the ORIGINAL height/width-fit loop
          //      above (same floor, same maxHeight/fixedW bounds), so the
          //      result is guaranteed to stay inside the box — which is what
          //      keeps it off the badge (positioned relative to this box's
          //      final top, computed further down) and off the canvas edge.
          //      A real AI question (capped at ~90 chars by the copywriter)
          //      fits within `fallback` lines well above the readable floor
          //      and never reaches this last tier.
          if (titleObj.titlePreferMaxLines) {
            const target = titleObj.titlePreferMaxLines;
            const fallback = titleObj.titleFallbackMaxLines || target + 1;
            const preferredMinFontSize = titleObj.titlePreferredMinFontSize || 55;
            let fallbackFontSize = null;
            while (titleObj.textLines.length > target && titleObj.fontSize > preferredMinFontSize) {
              if (titleObj.textLines.length <= fallback && fallbackFontSize === null) {
                fallbackFontSize = titleObj.fontSize;
              }
              const step = titleObj.titleFontSizeStep || 2;
              titleObj.set("fontSize", titleObj.fontSize - step);
              titleObj.set("width", fixedW);
              titleObj.initDimensions();
            }
            if (titleObj.textLines.length > target) {
              titleObj.set("fontSize", fallbackFontSize !== null ? fallbackFontSize : preferredMinFontSize);
              titleObj.set("width", fixedW);
              titleObj.initDimensions();
            }
            // Last-resort tier: never truncate. Keep the FULL text and keep
            // shrinking past the readable floor until it both (a) fits
            // <= fallback lines and (b) stays within the box's designed
            // area — same guarantee the original fit loop above provides.
            while (
              (titleObj.textLines.length > fallback || titleObj.height > maxHeight || titleObj.width > fixedW + 1) &&
              titleObj.fontSize > 12
            ) {
              titleObj.set("fontSize", titleObj.fontSize - 2);
              titleObj.set("width", fixedW);
              titleObj.initDimensions();
            }
          }
          titleObj.set("width", fixedW);
          const accent = titleObj.titleAccentColor;
          if (accent && parsed.ranges.length) {
            // Word-highlight: colour exactly the **marked** words (news-driven).
            for (const [s, e] of parsed.ranges) titleObj.setSelectionStyles({ fill: accent }, s, e);
          } else if (accent) {
            // Legacy two-tone fallback: split by wrapped lines — top half base
            // fill, bottom half accent (single line splits at the midpoint word).
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
            if (start > 0 && start < text.length) {
              titleObj.setSelectionStyles({ fill: accent }, start, text.length);
            }
          }
          // Bottom-anchor: keep the headline's BOTTOM edge fixed (just above the
          // subtitle) and let it grow UPWARD. Short headlines sit low near the
          // subtitle instead of floating with a gap; the font stays big (only the
          // fit loop above shrinks it if it would overflow the overlay area).
          if (typeof titleObj.titleAnchorBottom === "number") {
            titleObj.set("top", titleObj.titleAnchorBottom - titleObj.height);
          }

          // Section 17: two-layer render when titleHighlightGlow is set and there are highlight ranges
          if (titleObj.titleHighlightGlow && accent && parsed.ranges.length) {
            // 1) Base layer: highlighted ranges get transparent fill
            for (const [s, e] of parsed.ranges) {
              titleObj.setSelectionStyles({ fill: "rgba(0,0,0,0)" }, s, e);
            }
            // 2) Highlight layer: clone properties, default transparent fill, accent fill for highlight ranges
            const glow = titleObj.titleHighlightGlow;
            const hlShadow = new fabric.Shadow({
              color: glow.color || "rgba(232,54,43,0.75)",
              blur: typeof glow.blur === "number" ? glow.blur : 14,
              offsetX: 0,
              offsetY: 0,
            });
            const hlLayer = new fabric.Textbox(text, {
              fontFamily: titleObj.fontFamily,
              fontSize: titleObj.fontSize,
              fontWeight: titleObj.fontWeight,
              lineHeight: titleObj.lineHeight,
              charSpacing: titleObj.charSpacing,
              textAlign: titleObj.textAlign,
              width: titleObj.width,
              left: titleObj.left,
              top: titleObj.top,
              originX: titleObj.originX,
              originY: titleObj.originY,
              fill: "rgba(0,0,0,0)",
              shadow: hlShadow,
              selectable: false,
            });
            for (const [s, e] of parsed.ranges) {
              hlLayer.setSelectionStyles({ fill: accent }, s, e);
            }
            titleObj.initDimensions();
            hlLayer.initDimensions();
            hlLayer.followsTitle = true;
            // Insert directly above titleObj in canvas stacking order
            const titleIdx = canvas.getObjects().indexOf(titleObj);
            canvas.insertAt(hlLayer, titleIdx + 1, false);
          }

          // Section 19 & 20: RGB-split chroma fringe on WHITE text (titleChromaShift)
          if (titleObj.titleChromaShift && typeof titleObj.titleChromaShift.px === "number") {
            const shift = titleObj.titleChromaShift;
            const px = shift.px;
            const leftColor = shift.left || "rgba(255,40,40,0.55)";
            const rightColor = shift.right || "rgba(60,255,255,0.55)";
            const blur = typeof shift.blur === "number" ? shift.blur : 3;

            const makeChromaCopy = (offsetPx, fringeColor) => {
              const copyShadow = blur > 0
                ? new fabric.Shadow({
                    color: fringeColor,
                    blur,
                    offsetX: 0,
                    offsetY: 0,
                  })
                : null;
              const copy = new fabric.Textbox(text, {
                fontFamily: titleObj.fontFamily,
                fontSize: titleObj.fontSize,
                fontWeight: titleObj.fontWeight,
                lineHeight: titleObj.lineHeight,
                charSpacing: titleObj.charSpacing,
                textAlign: titleObj.textAlign,
                width: titleObj.width,
                left: titleObj.left + offsetPx,
                top: titleObj.top,
                originX: titleObj.originX,
                originY: titleObj.originY,
                fill: fringeColor,
                shadow: copyShadow,
                selectable: false,
              });
              if (accent && parsed.ranges.length) {
                for (const [s, e] of parsed.ranges) {
                  copy.setSelectionStyles({ fill: "rgba(0,0,0,0)" }, s, e);
                }
              }
              copy.initDimensions();
              return copy;
            };

            const leftCopy = makeChromaCopy(-px, leftColor);
            const rightCopy = makeChromaCopy(px, rightColor);
            leftCopy.followsTitle = true;
            rightCopy.followsTitle = true;

            // Insert both copies directly BELOW the base white layer
            const curIdx = canvas.getObjects().indexOf(titleObj);
            canvas.insertAt(leftCopy, curIdx, false);
            canvas.insertAt(rightCopy, curIdx + 1, false);
          }
        }

        // ── Label badge (Mode 4 discussion cards): "DISCUSSION" / "HOT TAKE" ──
        // A coloured pill above the big question, no trailing colon. Default
        // colours (overridable per template via labelBadge.discussionFill/
        // hotFill and label.discussionColor/hotColor) match both labels to
        // the SAME highlight colour as the rest of the template — Mode 4's
        // badge is meant to read as "this template's accent", not a
        // DISCUSSION-vs-HOT-TAKE colour code. The pill hugs the text width
        // and is anchored a fixed gap above the (already positioned) headline.
        const labelObj = objects.find((o) => o.placeholderRole === "label");
        const labelBadge = objects.find((o) => o.placeholderRole === "labelBadge");
        if (labelObj && label) {
          const isHot = /HOT/i.test(String(label));
          const mode = labelObj.titleTextTransform || "uppercase";
          // No trailing colon (user preference, 2026-08-16) — just "DISCUSSION" / "HOT TAKE".
          const raw = String(label).trim().replace(/:$/, "");
          labelObj.set({ text: applyCase(raw, mode), styles: {} });
          labelObj.initDimensions();
          if (!labelBadge || !labelBadge.labelKeepFill) {
            labelObj.set("fill", isHot
              ? (labelObj.hotColor || "#ffffff")
              : (labelObj.discussionColor || "#111111"));
          }

          const padX = typeof labelObj.labelPadX === "number" ? labelObj.labelPadX : 24;
          const padY = typeof labelObj.labelPadY === "number" ? labelObj.labelPadY : 12;
          const gap = typeof labelObj.labelGap === "number" ? labelObj.labelGap : 26;

          // Anchor above the headline when there is one; otherwise leave the
          // label where the template placed it.
          if (titleObj && title) {
            const bottom = titleObj.top - gap;
            labelObj.set("top", bottom - labelObj.height);
          }
          // Measure with Fabric after fonts are loaded: set wide box so it does not wrap
          labelObj.set({ width: 2000 });
          labelObj.initDimensions();
          const textW = Math.ceil(labelObj.calcTextWidth());
          const centered = labelObj.textAlign === "center";
          if (centered) {
            labelObj.set({ width: textW + 2, left: Math.round(width / 2 - (textW + 2) / 2) });
          } else {
            labelObj.set({ width: textW + 2 });
          }
          labelObj.initDimensions();
          if (labelObj.textLines.length > 1) {
            // Safety: widen and re-center if word wrap occurred
            const w2 = Math.ceil(labelObj.calcTextWidth()) + 20;
            labelObj.set({ width: w2, left: centered ? Math.round(width / 2 - w2 / 2) : labelObj.left });
            labelObj.initDimensions();
          }
          if (labelBadge) {
            const badgeW = typeof labelBadge.labelFixedWidth === "number"
              ? labelBadge.labelFixedWidth
              : (textW + 2) + padX * 2;
            const badgeH = typeof labelBadge.labelFixedHeight === "number"
              ? labelBadge.labelFixedHeight
              : labelObj.height + padY * 2;
            // Rounded corners. labelRadius on the badge (px) overrides; -1 or
            // "pill" → fully rounded (radius = half the height); default is a
            // tasteful rounded corner scaled to the badge height.
            let radius;
            if (labelBadge.labelRadius === "pill" || labelBadge.labelRadius === -1) {
              radius = badgeH / 2;
            } else if (typeof labelBadge.labelRadius === "number") {
              radius = labelBadge.labelRadius;
            } else {
              radius = Math.round(badgeH * 0.28);
            }
            const badgeProps = {
              width: badgeW,
              height: badgeH,
              rx: radius,
              ry: radius,
              left: centered ? Math.round(width / 2 - badgeW / 2) : labelObj.left - padX,
              top: labelObj.top - padY,
            };
            if (!labelBadge.labelKeepFill) {
              badgeProps.fill = isHot
                ? (labelBadge.hotFill || "#8e1b1b")
                : (labelBadge.discussionFill || "#f2c300");
            }
            labelBadge.set(badgeProps);
            if (centered) {
              // Vertically centre labelObj inside labelBadge
              labelObj.set("top", labelBadge.top + Math.round((badgeH - labelObj.height) / 2));
            }
          }
        } else if (labelObj) {
          labelObj.set({ visible: false });
          if (labelBadge) labelBadge.set({ visible: false });
        }

        // ── Subtitle placeholder (smaller caption; same **red** word markers) ──
        const subObj = objects.find((o) => o.placeholderRole === "subtitle");
        let subDeltaY = 0;
        if (subObj && subtitle) {
          const mode = subObj.titleTextTransform || null;
          const parsed = parseMarks(String(subtitle));
          const text = applyCase(parsed.text, mode);
          const origH = subObj.height;
          const origBottom = subObj.top + origH;
          const designedFontSize = subObj.fontSize;
          const minFloor = typeof subObj.subtitleMinFontSize === "number"
            ? subObj.subtitleMinFontSize
            : Math.max(22, Math.round(0.75 * designedFontSize));
          const maxLines = typeof subObj.subtitleMaxLines === "number"
            ? subObj.subtitleMaxLines
            : 2;

          subObj.set({ text, styles: {} });
          subObj.initDimensions();

          // If text doesn't fit on <= maxLines at designed size, step down to minFloor
          while (subObj.textLines.length > maxLines && subObj.fontSize > minFloor) {
            subObj.set("fontSize", subObj.fontSize - 1);
            subObj.initDimensions();
          }

          // If still overflowing maxLines at minFloor, keep shrinking down to 14
          while (subObj.textLines.length > maxLines && subObj.fontSize > 14) {
            subObj.set("fontSize", subObj.fontSize - 1);
            subObj.initDimensions();
          }

          // Box grows upward: keep bottom edge fixed
          subObj.set("top", origBottom - subObj.height);
          subDeltaY = Math.max(0, subObj.height - origH);

          const accent = subObj.titleAccentColor;
          if (accent) {
            for (const [s, e] of parsed.ranges) subObj.setSelectionStyles({ fill: accent }, s, e);
          }

          // ── Name badge: hug the subtitle text's actual width (e.g. a
          // pill behind "TOTO WOLFF") instead of a fixed box that's either
          // too tight for a long name or leaves an odd gap for a short one.
          // Re-compute height and top from the (possibly 2-line) subtitle and keep centred.
          const badge = objects.find((o) => o.placeholderRole === "subtitleBadge");
          if (badge) {
            const textW = subObj.calcTextWidth();
            const pad = typeof badge.badgePadX === "number" ? badge.badgePadX : 80;
            const newW = Math.max(badge.height, textW + pad);
            const cx = badge.left + badge.width / 2;
            const padY = typeof badge.badgePadY === "number" ? badge.badgePadY : 20;
            const newH = Math.max(badge.height, subObj.height + padY);
            badge.set({
              width: newW,
              height: newH,
              left: cx - newW / 2,
              top: subObj.top + (subObj.height - newH) / 2,
            });
          }
        } else if (subObj) {
          subObj.set({ visible: false });
          const badge = objects.find((o) => o.placeholderRole === "subtitleBadge");
          if (badge) badge.set({ visible: false });
        }

        // Move title and dependent objects up by subDeltaY if subtitle grew upward
        if (subDeltaY > 0 && titleObj) {
          if (typeof titleObj.titleAnchorBottom === "number") {
            titleObj.titleAnchorBottom -= subDeltaY;
          }
          titleObj.set("top", titleObj.top - subDeltaY);

          // Move highlight layer and chroma copies that follow the title
          for (const obj of canvas.getObjects()) {
            if (obj.followsTitle) obj.set("top", obj.top - subDeltaY);
          }

          // Re-anchor label and labelBadge if they sit above title
          if (labelObj && labelObj.visible !== false) {
            const gap = typeof labelObj.labelGap === "number" ? labelObj.labelGap : 26;
            const bottom = titleObj.top - gap;
            labelObj.set("top", bottom - labelObj.height);
            if (labelBadge && labelBadge.visible !== false) {
              const padY = typeof labelObj.labelPadY === "number" ? labelObj.labelPadY : 12;
              const badgeH = labelBadge.height;
              labelBadge.set("top", labelObj.top - padY);
              labelObj.set("top", labelBadge.top + Math.round((badgeH - labelObj.height) / 2));
            }
          }
        }

        // ── Caption placeholder (small descriptive line under the name/badge;
        // same **red** word markers) — e.g. "on Lewis Hamilton's success, and
        // Kim Kardashian" under a "TOTO WOLFF" name badge ──
        const captionObj = objects.find((o) => o.placeholderRole === "caption");
        if (captionObj && caption) {
          const mode = captionObj.titleTextTransform || null;
          const parsed = parseMarks(String(caption));
          const text = applyCase(parsed.text, mode);
          const origH = captionObj.height;
          const origBottom = captionObj.top + origH;
          const designedFontSize = captionObj.fontSize;
          const minFloor = typeof captionObj.captionMinFontSize === "number"
            ? captionObj.captionMinFontSize
            : Math.max(22, Math.round(0.75 * designedFontSize));
          const maxLines = typeof captionObj.captionMaxLines === "number"
            ? captionObj.captionMaxLines
            : 2;

          captionObj.set({ text, styles: {} });
          captionObj.initDimensions();

          while (captionObj.textLines.length > maxLines && captionObj.fontSize > minFloor) {
            captionObj.set("fontSize", captionObj.fontSize - 1);
            captionObj.initDimensions();
          }

          while (captionObj.textLines.length > maxLines && captionObj.fontSize > 14) {
            captionObj.set("fontSize", captionObj.fontSize - 1);
            captionObj.initDimensions();
          }

          // Grow upward: keep bottom edge fixed
          captionObj.set("top", origBottom - captionObj.height);

          const accent = captionObj.titleAccentColor;
          if (accent) {
            for (const [s, e] of parsed.ranges) captionObj.setSelectionStyles({ fill: accent }, s, e);
          }
          // Section 17: two-layer render for caption when captionHighlightGlow is set and there are highlights
          if (captionObj.captionHighlightGlow && accent && parsed.ranges.length) {
            for (const [s, e] of parsed.ranges) {
              captionObj.setSelectionStyles({ fill: "rgba(0,0,0,0)" }, s, e);
            }
            const glow = captionObj.captionHighlightGlow;
            const hlShadow = new fabric.Shadow({
              color: glow.color || "rgba(232,54,43,0.6)",
              blur: typeof glow.blur === "number" ? glow.blur : 8,
              offsetX: 0,
              offsetY: 0,
            });
            const hlLayer = new fabric.Textbox(text, {
              fontFamily: captionObj.fontFamily,
              fontSize: captionObj.fontSize,
              fontWeight: captionObj.fontWeight,
              lineHeight: captionObj.lineHeight,
              charSpacing: captionObj.charSpacing,
              textAlign: captionObj.textAlign,
              width: captionObj.width,
              left: captionObj.left,
              top: captionObj.top,
              originX: captionObj.originX,
              originY: captionObj.originY,
              fill: "rgba(0,0,0,0)",
              shadow: hlShadow,
              selectable: false,
            });
            for (const [s, e] of parsed.ranges) {
              hlLayer.setSelectionStyles({ fill: accent }, s, e);
            }
            captionObj.initDimensions();
            hlLayer.initDimensions();
            const capIdx = canvas.getObjects().indexOf(captionObj);
            canvas.insertAt(hlLayer, capIdx + 1, false);
          }

          // Section 19 & 20: RGB-split chroma fringe on caption (captionChromaShift)
          if (captionObj.captionChromaShift && typeof captionObj.captionChromaShift.px === "number") {
            const shift = captionObj.captionChromaShift;
            const px = shift.px;
            const leftColor = shift.left || "rgba(255,40,40,0.5)";
            const rightColor = shift.right || "rgba(60,255,255,0.5)";
            const blur = typeof shift.blur === "number" ? shift.blur : 3;

            const makeCaptionChromaCopy = (offsetPx, fringeColor) => {
              const copyShadow = blur > 0
                ? new fabric.Shadow({
                    color: fringeColor,
                    blur,
                    offsetX: 0,
                    offsetY: 0,
                  })
                : null;
              const copy = new fabric.Textbox(text, {
                fontFamily: captionObj.fontFamily,
                fontSize: captionObj.fontSize,
                fontWeight: captionObj.fontWeight,
                lineHeight: captionObj.lineHeight,
                charSpacing: captionObj.charSpacing,
                textAlign: captionObj.textAlign,
                width: captionObj.width,
                left: captionObj.left + offsetPx,
                top: captionObj.top,
                originX: captionObj.originX,
                originY: captionObj.originY,
                fill: fringeColor,
                shadow: copyShadow,
                selectable: false,
              });
              if (accent && parsed.ranges.length) {
                for (const [s, e] of parsed.ranges) {
                  copy.setSelectionStyles({ fill: "rgba(0,0,0,0)" }, s, e);
                }
              }
              copy.initDimensions();
              return copy;
            };

            const leftCopy = makeCaptionChromaCopy(-px, leftColor);
            const rightCopy = makeCaptionChromaCopy(px, rightColor);

            // Insert both copies directly BELOW the base caption layer
            const curCapIdx = canvas.getObjects().indexOf(captionObj);
            canvas.insertAt(leftCopy, curCapIdx, false);
            canvas.insertAt(rightCopy, curCapIdx + 1, false);
          }
        } else if (captionObj) {
          // No caption data for this job (the common case for quote cards —
          // design_caption is rarely populated) — hide the placeholder
          // instead of leaving the template's own seed/sample text (e.g.
          // "Caption goes here, a short line of context under the name")
          // baked into the final PNG. Same "no value = no element" rule
          // already applied to the watermark above.
          captionObj.set({ visible: false });
        }

        // ── Quote icon: hug the text like the scrim does ──
        // A short quote sits lower (less height needed above the bottom
        // anchor), so a static icon position would leave a growing gap;
        // anchor it a fixed distance above titleObj's actual post-fit top.
        const quoteIcon = objects.find((o) => o.placeholderRole === "quoteIcon");
        if (quoteIcon && titleObj) {
          const gap = typeof quoteIcon.iconGap === "number" ? quoteIcon.iconGap : 20;
          quoteIcon.set("top", titleObj.top - gap - quoteIcon.height);
          // ponytail: streak PNG and tab use fixed red gradient (#EC3A2E→#E1302A) as-is; tinting plugs in here later
        }

        // ── Text scrim: keep the dark overlay hugging the TEXT only ──
        // A rect with placeholderRole "scrim" is resized so its top sits just
        // above the topmost text element (pill/badge, quoteIcon, or headline) and it
        // runs to the canvas bottom, so the gradient never darkens far above the text
        // regardless of how many lines the headline wraps to. The linear-gradient
        // stretches with it.
        const scrim = objects.find((o) => o.placeholderRole === "scrim");
        if (scrim && titleObj) {
          const pad = typeof scrim.scrimPad === "number" ? scrim.scrimPad : 70;
          let highestY = titleObj.top;
          if (labelObj && labelObj.visible !== false) highestY = Math.min(highestY, labelObj.top);
          if (labelBadge && labelBadge.visible !== false) highestY = Math.min(highestY, labelBadge.top);
          if (quoteIcon && quoteIcon.visible !== false) highestY = Math.min(highestY, quoteIcon.top);
          const topY = Math.max(0, highestY - pad);
          const h = height - topY;
          scrim.set({ top: topY, height: h });
          if (scrim.fill && scrim.fill.coords) {
            scrim.fill.coords.y1 = 0;
            scrim.fill.coords.y2 = h;
            scrim.set("dirty", true);
          }
        }

        // ── Image slot placeholders (image, image_2, image_3, …) ──
        // Back-compat: single imageSrc → the "image" slot. imageSrcs (array)
        // maps by index: image→0, image_2→1, image_N→N-1.
        const srcs = Array.isArray(imageSrcs) && imageSrcs.length
          ? imageSrcs
          : (imageSrc ? [imageSrc] : []);
        const imageSlots = objects.filter(
          (o) => typeof o.placeholderRole === "string" && /^image(_\d+)?$/.test(o.placeholderRole)
        );
        if (!imageSlots.length || !srcs.length) {
          canvas.renderAll();
          resolve(finalize(canvas));
          return;
        }

        const pairs = [];
        for (const slot of imageSlots) {
          const role = slot.placeholderRole;
          const idx = role === "image" ? 0 : parseInt(role.split("_")[1], 10) - 1;
          if (srcs[idx]) pairs.push([slot, srcs[idx], idx]);
        }

        Promise.all(
          pairs.map(
            ([slot, src, si]) =>
              new Promise((res, rej) => {
                fabric.Image.fromURL(
                  src,
                  (img) => (img && img.width ? res([slot, img, si]) : rej(new Error("image failed to load"))),
                  { crossOrigin: "anonymous" }
                );
              })
          )
        )
          .then((loaded) => {
            for (const [slot, img, si] of loaded) {
              const slotW = slot.getScaledWidth();
              const slotH = slot.getScaledHeight();
              const cx = slot.left + slotW / 2;
              const cy = slot.top + slotH / 2;
              const idx = canvas.getObjects().indexOf(slot);
              // Extra corrective zoom on top of the base cover-fit scale (e.g.
              // a split-pair half whose subject's face renders smaller than
              // its partner's — see design_images.py's face-size matching).
              // Still clamped below like the base case, so it can only zoom
              // IN further (crop tighter), never reveal space outside the
              // slot's cover-fit bounds.
              const paramZoom = (Array.isArray(imageZooms) && imageZooms[si] > 0) ? imageZooms[si] : 1;
              const slotZoom = typeof slot.imageZoom === "number" && slot.imageZoom > 0 ? slot.imageZoom : 1;
              const zoom = paramZoom * slotZoom;
              const scale = Math.max(slotW / img.width, slotH / img.height) * zoom;
              const iw = img.width * scale;
              const ih = img.height * scale;
              // Focus point (fraction of image, e.g. the detected face). Position
              // the image so the focus lands at the slot centre, then clamp so the
              // slot stays fully covered — keeps the face in frame instead of a
              // blind geometric-centre crop. Default: slightly above centre.
              const fp = (Array.isArray(focusPoints) && focusPoints[si]) || [0.5, 0.42];
              let left = cx - fp[0] * iw;
              let top = cy - fp[1] * ih;
              left = Math.min(slot.left, Math.max(slot.left + slotW - iw, left));
              top = Math.min(slot.top, Math.max(slot.top + slotH - ih, top));
              img.set({
                left,
                top,
                originX: "left",
                originY: "top",
                scaleX: scale,
                scaleY: scale,
                selectable: false,
              });
              // Circle/ellipse slots clip to a circle (e.g. the round inset in
              // "GP Today" cards); everything else clips to the slot rect.
              const isCircle = slot.type === "circle" || slot.type === "ellipse";
              img.clipPath = isCircle
                ? new fabric.Circle({
                    left: cx,
                    top: cy,
                    radius: Math.min(slotW, slotH) / 2,
                    originX: "center",
                    originY: "center",
                    absolutePositioned: true,
                  })
                : new fabric.Rect({
                    left: slot.left,
                    top: slot.top,
                    width: slotW,
                    height: slotH,
                    absolutePositioned: true,
                  });
              canvas.remove(slot);
              canvas.insertAt(img, idx < 0 ? canvas.getObjects().length : idx, false);
            }
            canvas.renderAll();
            resolve(finalize(canvas));
          })
          .catch(reject);
        } catch (err) {
          reject(err);
        }
      });
    }).catch(reject);
  });
};
