"use client";

import { useEffect, useState } from "react";
import { fabric } from "fabric";

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
}: {
  json: unknown | null;
  width: number;
  height: number;
  className?: string;
}) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!json || !width || !height) {
      setSrc(null);
      return;
    }
    let cancelled = false;
    const el = document.createElement("canvas");
    const canvas = new fabric.StaticCanvas(el, {
      width,
      height,
      enableRetinaScaling: false,
    });

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

    const ready = document.fonts?.ready ?? Promise.resolve();
    ready
      .then(() => {
        canvas.loadFromJSON(json, () => {
          // let font metrics settle before capturing
          requestAnimationFrame(finish);
        });
      })
      .catch(finish);

    return () => {
      cancelled = true;
      try { canvas.dispose(); } catch {}
    };
  }, [json, width, height]);

  if (!src) return <div className={className} aria-hidden />;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={src} alt="" className={className} draggable={false} />;
}
