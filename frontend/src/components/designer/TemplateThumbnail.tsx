"use client";

import { useEffect, useState } from "react";
import type { BrandLook } from "@/lib/designTemplatesApi";
import { applyTemplateContent } from "./applyTemplate";
import { adaptLook, tintStreakDataUri } from "./adaptLook";
import { loadTemplateFonts, layoutLikeRenderer } from "./previewLayout";

// Sample content so the card shows the real styling (highlight colour, alignment,
// subtitle) instead of the empty "Headline" placeholder.
const SAMPLE_TITLE = "BAGNAIA **SNUBBED YAMAHA'S MILLIONS** TO JOIN APRILIA";
const SAMPLE_SUBTITLE = "A BOLD CAREER MOVE THAT SHOCKED THE PADDOCK.";
const SAMPLE_CAPTION = "ON THE RIVALRY THAT DEFINED THE SEASON, AND WHAT COMES NEXT.";

/**
 * Renders a design template's Fabric.js JSON into a static PNG preview.
 * Runs entirely client-side (templates hold only rects/text — no external
 * images — so the canvas never taints). Fonts are the same webfonts the editor
 * and renderer use, so the thumbnail matches the exported design.
 */
export default function TemplateThumbnail({
  json,
  width,
  height,
  className,
  look,
  label,
  title,
  subtitle,
  caption,
}: {
  json: unknown | null;
  width: number;
  height: number;
  className?: string;
  look?: BrandLook | null;
  label?: string | null;
  title?: string;
  subtitle?: string;
  caption?: string;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const lookKey = JSON.stringify(look);

  useEffect(() => {
    if (!json || !width || !height) {
      setSrc(null);
      return;
    }
    let cancelled = false;
    let canvasInstance: any = null;

    import("fabric")
      .then(async ({ fabric }) => {
        if (cancelled) return;
        const el = document.createElement("canvas");
        const canvas = new fabric.StaticCanvas(el, {
          width,
          height,
          enableRetinaScaling: false,
        });
        canvasInstance = canvas;

        const finish = () => {
          if (cancelled) {
            try { canvas.dispose(); } catch {}
            return;
          }
          canvas.renderAll();
          try {
            // Downscale the output — cap the long edge around 600px for a crisp card.
            const url = canvas.toDataURL({ format: "png", multiplier: Math.min(1, 600 / Math.max(width, height)) });
            if (!cancelled) setSrc(url);
          } catch {
            /* ignore */
          }
          try { canvas.dispose(); } catch {}
        };

        try {
          let copy: any = JSON.parse(JSON.stringify(json));

          // Every object with a placeholderRole starting with "image" gets neutral photo fill #3A4152
          if (Array.isArray(copy.objects)) {
            for (const obj of copy.objects as any[]) {
              if (obj && typeof obj.placeholderRole === "string" && obj.placeholderRole.startsWith("image")) {
                obj.fill = "#3A4152";
              }
            }
          }

          // If look and json.accentFollowsFanpage: adapt look and tint streak data URI
          if (look && copy.accentFollowsFanpage) {
            copy = adaptLook(copy, look);
            const quoteIcon = (copy.objects || []).find((o: any) => o && o.placeholderRole === "quoteIcon");
            if (quoteIcon && Array.isArray(quoteIcon.objects)) {
              for (const child of quoteIcon.objects as any[]) {
                if (child && child.type === "image" && child.visible !== false && child.src) {
                  child.src = await tintStreakDataUri(child.src, look.quoteMarks);
                }
              }
            }
          }

          // Label: if label is given, set the label object's text to it
          if (label !== undefined && label !== null) {
            const labelObj = (copy.objects || []).find((o: any) => o && o.placeholderRole === "label");
            if (labelObj) {
              labelObj.text = label;
            }
          }

          const ready = document.fonts?.ready ?? Promise.resolve();
          await ready;
          if (cancelled) {
            try { canvas.dispose(); } catch {}
            return;
          }

          await loadTemplateFonts(copy);
          if (cancelled) {
            try { canvas.dispose(); } catch {}
            return;
          }

          canvas.loadFromJSON(copy, () => {
            if (cancelled) {
              try { canvas.dispose(); } catch {}
              return;
            }
            applyTemplateContent({
              fabric,
              canvas,
              width,
              height,
              title: title !== undefined ? title : SAMPLE_TITLE,
              subtitle: subtitle !== undefined ? subtitle : SAMPLE_SUBTITLE,
              caption: caption !== undefined ? caption : SAMPLE_CAPTION,
            })
              .then(() => {
                if (cancelled) return;
                layoutLikeRenderer(canvas, width, height, label);
              })
              .catch(() => {})
              .finally(() => requestAnimationFrame(finish));
          });
        } catch {
          finish();
        }
      })
      .catch(() => {});

    return () => {
      cancelled = true;
      if (canvasInstance) {
        try { canvasInstance.dispose(); } catch {}
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [json, width, height, lookKey, label, title, subtitle, caption]);

  if (!src) return <div className={className} aria-hidden />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" className={className} draggable={false} />;
}
