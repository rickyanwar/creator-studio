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
  const { templateJson, width, height, title, imageSrc, imageSrcs } = args;

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
          let text = String(title);
          if (mode === "uppercase") text = text.toUpperCase();
          else if (mode === "lowercase") text = text.toLowerCase();
          else if (mode === "capitalize") text = text.replace(/(^|\s)(\S)/g, (m, sep, ch) => sep + ch.toUpperCase());
          const maxHeight = titleObj.height;
          titleObj.set({ text, styles: {} });
          // Floor of 12px: long headlines must always fit rather than overflow.
          while (titleObj.height > maxHeight && titleObj.fontSize > 12) {
            titleObj.set("fontSize", titleObj.fontSize - 2);
            titleObj.initDimensions();
          }
          // Two-tone headline: templates may set titleAccentColor on the
          // title layer. Split by wrapped lines — top half keeps the base
          // fill, bottom half gets the accent. Single line falls back to a
          // word-boundary split at the midpoint so it's always two-tone.
          const accent = titleObj.titleAccentColor;
          if (accent) {
            let start = -1;
            const lineCount = titleObj.textLines.length;
            if (lineCount > 1) {
              const splitLine = Math.ceil(lineCount / 2);
              for (let i = 0; i < text.length; i++) {
                if (titleObj.get2DCursorLocation(i, false).lineIndex >= splitLine) {
                  start = i;
                  break;
                }
              }
            } else {
              const sp = text.indexOf(" ", Math.floor(text.length / 2));
              if (sp >= 0) start = sp + 1;
            }
            if (start > 0 && start < text.length) {
              titleObj.setSelectionStyles({ fill: accent }, start, text.length);
            }
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
          resolve(canvas.toDataURL({ format: "png" }));
          return;
        }

        const pairs = [];
        for (const slot of imageSlots) {
          const role = slot.placeholderRole;
          const idx = role === "image" ? 0 : parseInt(role.split("_")[1], 10) - 1;
          if (srcs[idx]) pairs.push([slot, srcs[idx]]);
        }

        Promise.all(
          pairs.map(
            ([slot, src]) =>
              new Promise((res, rej) => {
                fabric.Image.fromURL(
                  src,
                  (img) => (img && img.width ? res([slot, img]) : rej(new Error("image failed to load"))),
                  { crossOrigin: "anonymous" }
                );
              })
          )
        )
          .then((loaded) => {
            for (const [slot, img] of loaded) {
              const slotW = slot.getScaledWidth();
              const slotH = slot.getScaledHeight();
              const idx = canvas.getObjects().indexOf(slot);
              const scale = Math.max(slotW / img.width, slotH / img.height);
              img.set({
                left: slot.left + slotW / 2,
                top: slot.top + slotH / 2,
                originX: "center",
                originY: "center",
                scaleX: scale,
                scaleY: scale,
                selectable: false,
              });
              img.clipPath = new fabric.Rect({
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
            resolve(canvas.toDataURL({ format: "png" }));
          })
          .catch(reject);
      } catch (err) {
        reject(err);
      }
    });
  });
};
