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
  const { templateJson, width, height, title, subtitle, watermark, watermarkImage, imageSrc, imageSrcs, focusPoints } = args;

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
      const wm = new fabric.Text(String(watermark).toUpperCase(), {
        left: Math.round(width * 0.052),
        top: Math.round(width * 0.045),
        fontFamily: "Poppins",
        fontWeight: "bold",
        fontSize: Math.round(width * 0.032),
        fill: "rgba(255,255,255,0.6)",
        charSpacing: 60,
        selectable: false,
        shadow: new fabric.Shadow({ color: "rgba(0,0,0,0.55)", blur: 8, offsetX: 0, offsetY: 1 }),
      });
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
              const scale = Math.max(slotW / img.width, slotH / img.height);
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
