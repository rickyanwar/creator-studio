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
window.renderTemplate = function renderTemplate(args) {
  const { templateJson, width, height, title, subtitle, caption, label, watermark, watermarkImage, imageSrc, imageSrcs, focusPoints, imageZooms } = args;

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
    const canvas = new fabric.StaticCanvas("c", { width, height });

    canvas.loadFromJSON(templateJson, () => {
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
          const maxHeight = titleObj.height;
          const fixedW = titleObj.width;
          titleObj.set({ text, styles: {} });
          titleObj.set("width", fixedW);
          titleObj.initDimensions();
          while ((titleObj.height > maxHeight || titleObj.width > fixedW + 1) && titleObj.fontSize > 12) {
            titleObj.set("fontSize", titleObj.fontSize - 2);
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
              titleObj.set("fontSize", titleObj.fontSize - 2);
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
          labelObj.set("fill", isHot
            ? (labelObj.hotColor || "#ffffff")
            : (labelObj.discussionColor || "#111111"));

          const padX = typeof labelObj.labelPadX === "number" ? labelObj.labelPadX : 24;
          const padY = typeof labelObj.labelPadY === "number" ? labelObj.labelPadY : 12;
          const gap = typeof labelObj.labelGap === "number" ? labelObj.labelGap : 26;

          // Anchor above the headline when there is one; otherwise leave the
          // label where the template placed it.
          if (titleObj && title) {
            const bottom = titleObj.top - gap;
            labelObj.set("top", bottom - labelObj.height);
          }
          // Centered layouts (textAlign "center") centre the pill + text on the
          // canvas; otherwise the pill hugs the label's fixed left edge.
          // NOTE: labelObj.calcTextWidth() (Fabric.js Textbox) was observed to
          // under-measure "HOT TAKE:" by roughly half (returned ~235px for
          // text that renders ~450px wide at this font/size) — resizing the
          // box to that bogus narrower width then re-triggered Fabric's own
          // word-wrap, splitting "HOT TAKE:" onto two lines that overlapped
          // the headline below (found 2026-08-16 testing the Yellow/Green
          // discussion variants; "DISCUSSION:" never showed it because a
          // single unbreakable word can't wrap regardless of box width).
          // Measure with a plain Canvas 2D context instead — exact, and
          // independent of whatever Fabric.js quirk causes the mismeasure.
          const measureCtx = document.createElement("canvas").getContext("2d");
          measureCtx.font = `${labelObj.fontWeight || 400} ${labelObj.fontSize}px ${labelObj.fontFamily || "sans-serif"}`;
          const textW = Math.ceil(measureCtx.measureText(labelObj.text).width) + 2; // +2px rounding safety
          const centered = labelObj.textAlign === "center";
          if (centered) labelObj.set({ width: textW, left: Math.round(width / 2 - textW / 2) });
          if (labelObj.textLines.length > 1) {
            // Still wrapped despite the accurate measurement (e.g. an
            // unusually long custom label) — widen to the box's original
            // capacity and re-measure/re-center rather than let it wrap.
            labelObj.set({ width: 700 });
            labelObj.initDimensions();
          }
          if (labelBadge) {
            const badgeW = textW + padX * 2;
            const badgeH = labelObj.height + padY * 2;
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
            labelBadge.set({
              fill: isHot
                ? (labelBadge.hotFill || "#8e1b1b")
                : (labelBadge.discussionFill || "#f2c300"),
              width: badgeW,
              height: badgeH,
              rx: radius,
              ry: radius,
              left: centered ? Math.round(width / 2 - badgeW / 2) : labelObj.left - padX,
              top: labelObj.top - padY,
            });
          }
        } else if (labelObj) {
          labelObj.set({ visible: false });
          if (labelBadge) labelBadge.set({ visible: false });
        }

        // ── Subtitle placeholder (smaller caption; same **red** word markers) ──
        const subObj = objects.find((o) => o.placeholderRole === "subtitle");
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
          if (accent) {
            for (const [s, e] of parsed.ranges) subObj.setSelectionStyles({ fill: accent }, s, e);
          }

          // ── Name badge: hug the subtitle text's actual width (e.g. a
          // pill behind "TOTO WOLFF") instead of a fixed box that's either
          // too tight for a long name or leaves an odd gap for a short one.
          // Also re-centre it vertically on the text — subObj's height can
          // shrink (fewer/smaller glyphs) after the fit loop above, and a
          // fixed badge top would then sit visibly off-centre.
          const badge = objects.find((o) => o.placeholderRole === "subtitleBadge");
          if (badge) {
            const textW = subObj.calcTextWidth();
            const pad = typeof badge.badgePadX === "number" ? badge.badgePadX : 80;
            const newW = Math.max(badge.height, textW + pad);
            const cx = badge.left + badge.width / 2;
            const textCy = subObj.top + subObj.height / 2;
            badge.set({ width: newW, left: cx - newW / 2, top: textCy - badge.height / 2 });
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
          const maxHeight = captionObj.height;
          captionObj.set({ text, styles: {} });
          while (captionObj.height > maxHeight && captionObj.fontSize > 8) {
            captionObj.set("fontSize", captionObj.fontSize - 1);
            captionObj.initDimensions();
          }
          const accent = captionObj.titleAccentColor;
          if (accent) {
            for (const [s, e] of parsed.ranges) captionObj.setSelectionStyles({ fill: accent }, s, e);
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

        // ── Text scrim: keep the dark overlay hugging the TEXT only ──
        // A rect with placeholderRole "scrim" is resized so its top sits just
        // above the (bottom-anchored) headline and it runs to the canvas bottom,
        // so the gradient never darkens far above the text regardless of how many
        // lines the headline wraps to. The linear-gradient stretches with it.
        const scrim = objects.find((o) => o.placeholderRole === "scrim");
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

        // ── Quote icon: hug the text like the scrim does ──
        // A short quote sits lower (less height needed above the bottom
        // anchor), so a static icon position would leave a growing gap;
        // anchor it a fixed distance above titleObj's actual post-fit top.
        const quoteIcon = objects.find((o) => o.placeholderRole === "quoteIcon");
        if (quoteIcon && titleObj) {
          const gap = typeof quoteIcon.iconGap === "number" ? quoteIcon.iconGap : 20;
          quoteIcon.set("top", titleObj.top - gap - quoteIcon.height);
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
              const zoom = (Array.isArray(imageZooms) && imageZooms[si] > 0) ? imageZooms[si] : 1;
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
  });
};
