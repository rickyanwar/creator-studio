"use client";

/**
 * Fabric.js canvas editor used by both:
 *  - Template Designer (/templates/[id]) — build a template with placeholder
 *    layers, save as Fabric JSON.
 *  - Job Designer (/designer/[jobId]) — review mode: template preloaded with
 *    the AI title + selected image; admin edits freely and exports a PNG.
 *
 * Placeholder contract (kept in sync with renderer/inject.js):
 *  - an object with placeholderRole="title" receives the headline text
 *  - an object with placeholderRole="image" marks the photo slot; the photo
 *    is cover-fitted and clipped to its bounds
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { fabric } from "fabric";
import { Icon } from "@iconify/react";
import { parseMarks, applyTemplateContent } from "./applyTemplate";

// Only fonts vendored in renderer/fonts (installed as system fonts there) so the
// editor preview matches the headless render exactly. Keep in sync with the
// Google Fonts @import in globals.css and the TTFs in renderer/fonts/.
const FONTS = ["Poppins", "Montserrat", "Roboto", "Oswald", "Playfair Display", "Bebas Neue", "Anton", "Pacifico", "Tusker Grotesk", "Agrandir", "Agrandir Grand Light"];
const DEFAULT_FONT = "Poppins";

type TitleTransform = "uppercase" | "lowercase" | "capitalize";
type TitleBox = fabric.Textbox & {
  titleAccentColor?: string;
  titleUppercase?: boolean; // legacy — superseded by titleTextTransform
  titleTextTransform?: TitleTransform;
};

function transformTitle(text: string, box: TitleBox): string {
  const mode = box.titleTextTransform ?? (box.titleUppercase ? "uppercase" : undefined);
  if (mode === "uppercase") return text.toUpperCase();
  if (mode === "lowercase") return text.toLowerCase();
  if (mode === "capitalize") return text.replace(/(^|\s)(\S)/g, (m, sep, ch) => sep + ch.toUpperCase());
  return text;
}

// Two-tone headline — same contract as renderer/inject.js: bottom half of the
// wrapped lines gets titleAccentColor; a single line splits at the midpoint word.
function applyTwoTone(box: TitleBox) {
  const text = box.text ?? "";
  box.styles = {};
  const accent = box.titleAccentColor;
  if (!accent || !text) return;
  let start = -1;
  const lineCount = box.textLines.length;
  if (lineCount > 1) {
    const splitLine = Math.ceil(lineCount / 2);
    for (let i = 0; i < text.length; i++) {
      if (box.get2DCursorLocation(i, false).lineIndex >= splitLine) {
        start = i;
        break;
      }
    }
  } else {
    const sp = text.indexOf(" ", Math.floor(text.length / 2));
    if (sp >= 0) start = sp + 1;
  }
  if (start > 0 && start < text.length) {
    box.setSelectionStyles({ fill: accent }, start, text.length);
  }
}
const ROLE_PROP = "placeholderRole";

export type EditorApi = {
  toTemplateJson: () => { json: Record<string, unknown>; placeholderConfig: Record<string, unknown> };
  exportPng: () => Promise<Blob>;
  injectTitle: (title: string) => void;
  injectSubtitle: (subtitle: string) => void;
  injectCaption: (caption: string) => void;
  injectImage: (src: string) => Promise<void>;
  showSample: () => void;
  hideSample: () => void;
};

type Props = {
  width: number;
  height: number;
  initialJson?: Record<string, unknown> | null;
  onReady?: (api: EditorApi) => void;
  sampleOnLoad?: boolean; // template designer: show sample content (WYSIWYG)
};

type SlotBounds = { left: number; top: number; width: number; height: number };
type FabricObjectWithRole = fabric.Object & { [ROLE_PROP]?: string; text?: string; slotBounds?: SlotBounds };

export default function TemplateEditor({ width, height, initialJson, onReady, sampleOnLoad }: Props) {
  const canvasElRef = useRef<HTMLCanvasElement>(null);
  const canvasRef = useRef<fabric.Canvas | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // loadFromJSON is async — injections requested before it completes are
  // queued here and applied in its callback (fixes the review-page race
  // where the AI headline never replaced the placeholder text)
  const jsonLoadedRef = useRef(false);
  const pendingTitleRef = useRef<string | null>(null);
  const pendingSubtitleRef = useRef<string | null>(null);
  const pendingCaptionRef = useRef<string | null>(null);
  const pendingImageRef = useRef<string | null>(null);
  // WYSIWYG sample preview (template designer): inject sample content so the
  // canvas looks like the real result; snapshot lets us reset before Save.
  const sampleShownRef = useRef(false);
  const snapshotRef = useRef<Record<string, { text?: string; fontSize?: number; top?: number; width?: number; height?: number; y2?: number }> | null>(null);
  const [selected, setSelected] = useState<FabricObjectWithRole | null>(null);
  const [bgColor, setBgColor] = useState("#111827");
  const [activeTool, setActiveTool] = useState<"design" | "text" | "shapes" | "image" | "draw">("design");
  const [brushColor, setBrushColor] = useState("#ffffff");
  const [brushWidth, setBrushWidth] = useState(6);
  const [, forceRender] = useState(0);
  const bump = () => forceRender((n) => n + 1);

  const zoom = Math.min(640 / width, 640 / height, 1);

  // ── image cover-fit into a slot's bounds (same math as renderer/inject.js) ──
  const coverFitImage = useCallback(
    (canvas: fabric.Canvas, img: fabric.Image, bounds: { left: number; top: number; width: number; height: number }, index: number) => {
      const scale = Math.max(bounds.width / (img.width || 1), bounds.height / (img.height || 1));
      img.set({
        left: bounds.left + bounds.width / 2,
        top: bounds.top + bounds.height / 2,
        originX: "center",
        originY: "center",
        scaleX: scale,
        scaleY: scale,
      });
      img.clipPath = new fabric.Rect({
        left: bounds.left,
        top: bounds.top,
        width: bounds.width,
        height: bounds.height,
        absolutePositioned: true,
      });
      (img as FabricObjectWithRole)[ROLE_PROP] = "image";
      // remember the original slot area so replacing this image later
      // cover-fits into the slot, not into this image's own (larger) bounds
      (img as FabricObjectWithRole).slotBounds = bounds;
      canvas.insertAt(img, index, false);
      canvas.renderAll();
    },
    []
  );

  useEffect(() => {
    if (!canvasElRef.current) return;
    const canvas = new fabric.Canvas(canvasElRef.current, {
      width: width * zoom,
      height: height * zoom,
      backgroundColor: bgColor,
      preserveObjectStacking: true,
    });
    canvas.setZoom(zoom);
    canvasRef.current = canvas;
    // fontsReady resolves asynchronously — if the effect (React strict-mode
    // double-invoke, or a width/height change) tears down and disposes this
    // canvas before then, calling loadFromJSON on it throws (null context).
    let disposed = false;

    // Webfonts load lazily — preload every editor font (regular + bold) before
    // touching the canvas so glyph metrics are correct from the start. This
    // must complete BEFORE loadFromJSON/showSample below: the auto-fit sizing
    // they do is a one-time measurement — if it runs against a fallback font
    // (because the real webfont hasn't downloaded yet), the wrong fontSize
    // gets baked in permanently and never matches TemplateThumbnail, which
    // waits on document.fonts.ready for the same reason.
    const fontsReady: Promise<void> = Promise.all(
      FONTS.flatMap((f) => [
        document.fonts.load(`400 16px "${f}"`),
        document.fonts.load(`700 16px "${f}"`),
      ])
    )
      .then(() => {
        FONTS.forEach((f) => fabric.util.clearFabricFontCache(f));
      })
      .catch(() => {});

    canvas.on("selection:created", (e) => setSelected((e.selected?.[0] as FabricObjectWithRole) ?? null));
    canvas.on("selection:updated", (e) => setSelected((e.selected?.[0] as FabricObjectWithRole) ?? null));
    canvas.on("selection:cleared", () => setSelected(null));
    // keep the two-tone split live while the headline placeholder is edited
    canvas.on("text:changed", (e) => {
      const t = e.target as FabricObjectWithRole | undefined;
      if (t && t[ROLE_PROP] === "title" && t instanceof fabric.Textbox) {
        applyTwoTone(t as TitleBox);
        canvas.renderAll();
      }
    });

    // ── WYSIWYG sample preview (template designer) ──
    // Declared before loadFromJSON below: its callback can fire synchronously
    // (templates with no async-loaded objects), which would otherwise call
    // showSample() -> reference SAMPLE_TITLE while it's still in the TDZ.
    const SAMPLE_TITLE = "BAGNAIA **SNUBBED YAMAHA'S MILLIONS** TO JOIN APRILIA";
    const SAMPLE_SUBTITLE = "A BOLD CAREER MOVE THAT SHOCKED THE PADDOCK.";
    const SAMPLE_CAPTION = "ON THE RIVALRY THAT DEFINED THE SEASON, AND WHAT COMES NEXT.";

    function snapshotPlaceholders() {
      const objs = canvas.getObjects() as FabricObjectWithRole[];
      const snap: NonNullable<typeof snapshotRef.current> = {};
      for (const role of ["title", "subtitle", "caption"]) {
        const o = objs.find((x) => x[ROLE_PROP] === role) as (fabric.Textbox & FabricObjectWithRole) | undefined;
        if (o) snap[role] = { text: o.text, fontSize: o.fontSize, top: o.top, width: o.width };
      }
      const scrim = objs.find((x) => x[ROLE_PROP] === "scrim") as
        (fabric.Rect & { fill?: { coords?: { y2: number } } }) | undefined;
      if (scrim) snap.scrim = { top: scrim.top, height: scrim.height, y2: scrim.fill?.coords?.y2 };
      snapshotRef.current = snap;
    }

    function showSample() {
      if (sampleShownRef.current) return;
      try {
        const objs = canvas.getObjects() as FabricObjectWithRole[];
        const hasTitle = objs.some((o) => o[ROLE_PROP] === "title");
        if (!hasTitle) return;
        const hasSubtitle = objs.some((o) => o[ROLE_PROP] === "subtitle");
        const hasCaption = objs.some((o) => o[ROLE_PROP] === "caption");
        applyTemplateContent({
          fabric, canvas, width, height,
          title: SAMPLE_TITLE,
          subtitle: hasSubtitle ? SAMPLE_SUBTITLE : undefined,
          caption: hasCaption ? SAMPLE_CAPTION : undefined,
        });
        sampleShownRef.current = true;
      } catch (err) {
        console.warn("showSample failed:", err);
        if (typeof window !== "undefined") {
          window.setTimeout(() => alert("showSample DEBUG:\n" + (err instanceof Error ? err.message + "\n\n" + (err.stack || "") : String(err))), 100);
        }
      }
    }

    function hideSample() {
      const snap = snapshotRef.current;
      if (!snap || !sampleShownRef.current) return;
      try {
      const objs = canvas.getObjects() as FabricObjectWithRole[];
      for (const role of ["title", "subtitle", "caption"]) {
        const o = objs.find((x) => x[ROLE_PROP] === role) as (fabric.Textbox & FabricObjectWithRole) | undefined;
        const s = snap[role];
        if (o && s) {
          o.set({ text: s.text, fontSize: s.fontSize, top: s.top, width: s.width, styles: {} });
          o.initDimensions();
        }
      }
      const scrim = objs.find((x) => x[ROLE_PROP] === "scrim") as
        (fabric.Rect & { fill?: { coords?: { y1: number; y2: number } } }) | undefined;
      if (scrim && snap.scrim) {
        scrim.set({ top: snap.scrim.top, height: snap.scrim.height });
        if (scrim.fill && scrim.fill.coords && typeof snap.scrim.y2 === "number") scrim.fill.coords.y2 = snap.scrim.y2;
        scrim.set("dirty", true);
      }
      canvas.renderAll();
      sampleShownRef.current = false;
      } catch (err) {
        console.warn("hideSample failed:", err);
      }
    }

    jsonLoadedRef.current = !initialJson;
    if (initialJson) {
      fontsReady.then(() => {
        if (disposed) return;
        canvas.loadFromJSON(initialJson, () => {
          if (disposed) return;
          // fabric 5.3 quirk: text objects deserialized without a "styles" key
          // get styles=undefined (stylesFromArray passes it through), and then
          // toObject()/toJSON() throws — normalize so Save always works.
          (canvas.getObjects() as Array<FabricObjectWithRole & { styles?: unknown }>).forEach((o) => {
            if ("text" in o && o.styles === undefined) o.styles = {};
          });
          // show the two-tone accent on the placeholder while designing
          const titleBox = (canvas.getObjects() as FabricObjectWithRole[]).find((o) => o[ROLE_PROP] === "title");
          if (titleBox instanceof fabric.Textbox) applyTwoTone(titleBox as TitleBox);
          const bg = canvas.backgroundColor;
          if (typeof bg === "string" && bg) setBgColor(bg);
          canvas.renderAll();
          // apply injections that arrived while the JSON was still loading
          jsonLoadedRef.current = true;
          snapshotPlaceholders();
          if (pendingTitleRef.current) {
            doInjectTitle(pendingTitleRef.current);
            pendingTitleRef.current = null;
          }
          if (pendingSubtitleRef.current) {
            doInjectSubtitle(pendingSubtitleRef.current);
            pendingSubtitleRef.current = null;
          }
          if (pendingCaptionRef.current) {
            doInjectCaption(pendingCaptionRef.current);
            pendingCaptionRef.current = null;
          }
          if (sampleOnLoad) showSample();
          if (pendingImageRef.current) {
            doInjectImage(pendingImageRef.current).catch(() => {});
            pendingImageRef.current = null;
          }
        });
      });
    }

    // Delegate to the same function the "Live preview" thumbnail uses (applyTemplate.ts)
    // so the two can never drift apart again — same highlight/auto-fit/scrim/anchor math.
    function doInjectTitle(rawTitle: string) {
      applyTemplateContent({ fabric, canvas, width, height, title: rawTitle });
    }

    function doInjectSubtitle(rawSub: string) {
      applyTemplateContent({ fabric, canvas, width, height, subtitle: rawSub });
    }

    function doInjectCaption(rawCaption: string) {
      applyTemplateContent({ fabric, canvas, width, height, caption: rawCaption });
    }

    function doInjectImage(src: string) {
      return new Promise<void>((resolve, reject) => {
        const objs = canvas.getObjects() as FabricObjectWithRole[];
        const slot = objs.find((o) => o[ROLE_PROP] === "image");
        if (!slot) {
          reject(new Error("template has no image slot"));
          return;
        }
        // an already-injected image carries the original slot area; a raw
        // slot rect is measured directly (accounting for a center origin)
        const w = slot.getScaledWidth();
        const h = slot.getScaledHeight();
        const bounds = slot.slotBounds ?? {
          left: (slot.left ?? 0) - (slot.originX === "center" ? w / 2 : 0),
          top: (slot.top ?? 0) - (slot.originY === "center" ? h / 2 : 0),
          width: w,
          height: h,
        };
        const index = objs.indexOf(slot);
        fabric.Image.fromURL(
          src,
          (img) => {
            if (!img || !img.width) {
              reject(new Error("image failed to load"));
              return;
            }
            canvas.remove(slot);
            coverFitImage(canvas, img, bounds, index);
            resolve();
          },
          { crossOrigin: "anonymous" }
        );
      });
    }

    const api: EditorApi = {
      toTemplateJson: () => {
        // Must list every custom prop the render pipeline reads (applyTemplate.ts /
        // renderer/inject.js) — anything left off silently vanishes from the saved
        // JSON: titleAnchorBottom/scrimPad missing here previously broke bottom-
        // anchoring and scrim positioning on every save/"Refresh preview".
        const json = canvas.toJSON([
          ROLE_PROP,
          "titleAccentColor",
          "titleUppercase",
          "titleTextTransform",
          "titleAnchorBottom",
          "scrimPad",
          "slotBounds",
          "iconGap",
          "badgePadX",
        ]) as unknown as Record<string, unknown>;
        const objs = canvas.getObjects() as FabricObjectWithRole[];
        const titleIdx = objs.findIndex((o) => o[ROLE_PROP] === "title");
        const imageIdx = objs.findIndex((o) => o[ROLE_PROP] === "image");
        return {
          json,
          placeholderConfig: {
            title_layer_index: titleIdx,
            image_slot_index: imageIdx,
            has_title: titleIdx >= 0,
            has_image_slot: imageIdx >= 0,
          },
        };
      },
      exportPng: () =>
        new Promise<Blob>((resolve, reject) => {
          canvas.discardActiveObject();
          canvas.renderAll();
          const dataUrl = canvas.toDataURL({ format: "png", multiplier: 1 / zoom });
          fetch(dataUrl)
            .then((r) => r.blob())
            .then(resolve)
            .catch(reject);
        }),
      injectTitle: (title: string) => {
        if (!jsonLoadedRef.current) {
          pendingTitleRef.current = title;
          return;
        }
        doInjectTitle(title);
      },
      injectSubtitle: (subtitle: string) => {
        if (!jsonLoadedRef.current) {
          pendingSubtitleRef.current = subtitle;
          return;
        }
        doInjectSubtitle(subtitle);
      },
      injectCaption: (caption: string) => {
        if (!jsonLoadedRef.current) {
          pendingCaptionRef.current = caption;
          return;
        }
        doInjectCaption(caption);
      },
      showSample: () => showSample(),
      hideSample: () => hideSample(),
      injectImage: (src: string) => {
        if (!jsonLoadedRef.current) {
          pendingImageRef.current = src;
          return Promise.resolve();
        }
        return doInjectImage(src);
      },
    };
    onReady?.(api);

    return () => {
      disposed = true;
      canvas.dispose();
      canvasRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [width, height]);

  // ── toolbar actions ──
  function withCanvas(fn: (c: fabric.Canvas) => void) {
    if (canvasRef.current) {
      fn(canvasRef.current);
      canvasRef.current.renderAll();
      bump();
    }
  }

  function clearRole(canvas: fabric.Canvas, role: string) {
    (canvas.getObjects() as FabricObjectWithRole[]).forEach((o) => {
      if (o[ROLE_PROP] === role) delete o[ROLE_PROP];
    });
  }

  const addHeadline = () =>
    withCanvas((c) => {
      clearRole(c, "title");
      const box = new fabric.Textbox("Your headline goes here", {
        left: width * 0.06,
        top: height * 0.72,
        width: width * 0.88,
        fontSize: Math.round(width / 16),
        fontWeight: "bold",
        fontFamily: DEFAULT_FONT,
        fill: "#ffffff",
      });
      (box as FabricObjectWithRole)[ROLE_PROP] = "title";
      c.add(box);
      c.setActiveObject(box);
    });

  const addImageSlot = (role: string = "image") =>
    withCanvas((c) => {
      clearRole(c, role);
      const secondary = role !== "image";
      const rect = new fabric.Rect({
        left: secondary ? Math.round(width * 0.5) : 0,
        top: 0,
        width: secondary ? Math.round(width * 0.5) : width,
        height: Math.round(height * 0.66),
        fill: secondary ? "#4b5563" : "#374151",
        stroke: "#9ca3af",
        strokeDashArray: [12, 8],
        strokeWidth: 2,
      });
      (rect as FabricObjectWithRole)[ROLE_PROP] = role;
      c.add(rect);
      c.sendToBack(rect);
      c.setActiveObject(rect);
    });

  const addText = () =>
    withCanvas((c) => {
      const box = new fabric.Textbox("Text", {
        left: width * 0.1,
        top: height * 0.1,
        width: width * 0.4,
        fontSize: Math.round(width / 30),
        fontFamily: DEFAULT_FONT,
        fill: "#ffffff",
      });
      c.add(box);
      c.setActiveObject(box);
    });

  const addRect = () =>
    withCanvas((c) => {
      const rect = new fabric.Rect({
        left: width * 0.3,
        top: height * 0.4,
        width: Math.round(width * 0.4),
        height: Math.round(height * 0.2),
        fill: "#dc2626",
      });
      c.add(rect);
      c.setActiveObject(rect);
    });

  const addCircle = () =>
    withCanvas((c) => {
      const circle = new fabric.Circle({
        left: width * 0.35,
        top: height * 0.35,
        radius: Math.round(Math.min(width, height) * 0.15),
        fill: "#2563eb",
      });
      c.add(circle);
      c.setActiveObject(circle);
    });

  const addTriangle = () =>
    withCanvas((c) => {
      const size = Math.round(Math.min(width, height) * 0.3);
      const tri = new fabric.Triangle({ left: width * 0.35, top: height * 0.35, width: size, height: size, fill: "#f59e0b" });
      c.add(tri);
      c.setActiveObject(tri);
    });

  const addLine = () =>
    withCanvas((c) => {
      const line = new fabric.Line([width * 0.2, height * 0.5, width * 0.8, height * 0.5], {
        stroke: "#ffffff",
        strokeWidth: 6,
      });
      c.add(line);
      c.setActiveObject(line);
    });

  // Freehand draw mode follows the "Draw" tool tab
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const drawing = activeTool === "draw";
    c.isDrawingMode = drawing;
    if (drawing) {
      if (!c.freeDrawingBrush) c.freeDrawingBrush = new fabric.PencilBrush(c);
      c.freeDrawingBrush.color = brushColor;
      c.freeDrawingBrush.width = brushWidth;
    }
  }, [activeTool, brushColor, brushWidth]);

  function handleLogoUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      withCanvas((c) => {
        fabric.Image.fromURL(String(reader.result), (img) => {
          const scale = Math.min((width * 0.25) / (img.width || 1), (height * 0.25) / (img.height || 1), 1);
          img.set({ left: width * 0.04, top: height * 0.04, scaleX: scale, scaleY: scale });
          c.add(img);
          c.setActiveObject(img);
          c.renderAll();
        });
      });
    };
    reader.readAsDataURL(file);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  const deleteSelected = () =>
    withCanvas((c) => {
      const obj = c.getActiveObject();
      if (obj) c.remove(obj);
      setSelected(null);
    });

  const moveLayer = (dir: "front" | "forward" | "backward" | "back") =>
    withCanvas((c) => {
      const obj = c.getActiveObject();
      if (!obj) return;
      if (dir === "front") c.bringToFront(obj);
      else if (dir === "forward") c.bringForward(obj);
      else if (dir === "backward") c.sendBackwards(obj);
      else c.sendToBack(obj);
    });

  const duplicateSelected = () =>
    withCanvas((c) => {
      const obj = c.getActiveObject();
      if (!obj) return;
      obj.clone((clone: fabric.Object) => {
        clone.set({ left: (obj.left ?? 0) + 24, top: (obj.top ?? 0) + 24 });
        // a copy must not keep the title/image placeholder role (only one of each)
        delete (clone as FabricObjectWithRole)[ROLE_PROP];
        c.add(clone);
        c.setActiveObject(clone);
      });
    });

  // Toggle a boolean/text style prop on the selected text (bold/italic/underline/strike)
  const toggleTextStyle = (prop: "fontWeight" | "fontStyle" | "underline" | "linethrough") =>
    withCanvas((c) => {
      const obj = c.getActiveObject();
      if (!(obj instanceof fabric.Textbox || obj instanceof fabric.Text)) return;
      const t = obj as fabric.Textbox;
      if (prop === "fontWeight") t.set({ fontWeight: t.fontWeight === "bold" ? "normal" : "bold" });
      else if (prop === "fontStyle") t.set({ fontStyle: t.fontStyle === "italic" ? "normal" : "italic" });
      else if (prop === "underline") t.set({ underline: !t.underline });
      else t.set({ linethrough: !t.linethrough });
    });

  function setBackground(color: string) {
    setBgColor(color);
    withCanvas((c) => c.setBackgroundColor(color, () => c.renderAll()));
  }

  function updateSelected(props: Record<string, unknown>) {
    withCanvas((c) => {
      const obj = c.getActiveObject();
      if (obj) obj.set(props as Partial<fabric.Object>);
    });
  }

  function setTitleAccent(color: string | undefined) {
    withCanvas((c) => {
      const obj = c.getActiveObject();
      if (obj instanceof fabric.Textbox) {
        (obj as TitleBox).titleAccentColor = color;
        applyTwoTone(obj as TitleBox);
      }
    });
  }

  const isText = selected instanceof fabric.Textbox || selected instanceof fabric.Text;
  const selectedRole = selected?.[ROLE_PROP];

  const btn = "px-2.5 py-1.5 rounded-lg border border-hairline text-xs font-medium text-ink-80 hover:border-primary-main hover:text-primary-main transition-colors flex items-center gap-1.5";
  const panelBtn = "w-full px-2.5 py-2 rounded-lg border border-hairline text-xs font-medium text-ink-80 hover:border-primary-main hover:text-primary-main transition-colors flex items-center gap-2";
  const TOOLS = [
    { id: "design", label: "Design", icon: "solar:widget-2-bold-duotone" },
    { id: "text", label: "Text", icon: "solar:text-bold-duotone" },
    { id: "shapes", label: "Shapes", icon: "solar:widget-bold-duotone" },
    { id: "image", label: "Image", icon: "solar:gallery-bold-duotone" },
    { id: "draw", label: "Draw", icon: "solar:pen-new-square-bold-duotone" },
  ] as const;

  return (
    <div className="flex gap-3 items-start">
      {/* Left icon rail (Canva-style tools) */}
      <div className="shrink-0 flex flex-col gap-1 rounded-xl border border-hairline p-1.5 bg-bg-paper">
        {TOOLS.map((t) => (
          <button
            key={t.id}
            onClick={() => setActiveTool(t.id)}
            className={`flex flex-col items-center gap-0.5 w-16 py-2 rounded-lg text-[10px] font-medium transition-colors ${
              activeTool === t.id ? "bg-primary-main/10 text-primary-main" : "text-ink-48 hover:bg-parchment"
            }`}
          >
            <Icon icon={t.icon} width={20} />
            {t.label}
          </button>
        ))}
      </div>

      {/* Tool panel — content follows the active tool */}
      <div className="shrink-0 w-52 rounded-xl border border-hairline p-3 space-y-2 bg-bg-paper">
        {activeTool === "design" && (
          <>
            <p className="text-[11px] font-bold text-ink-80 mb-1">Placeholders</p>
            <button className={panelBtn} onClick={() => addImageSlot("image")} title="Main photo slot">
              <Icon icon="solar:gallery-bold-duotone" width={14} /> Image Slot
            </button>
            <button className={panelBtn} onClick={() => addImageSlot("image_2")} title="Secondary/related photo slot">
              <Icon icon="solar:gallery-bold-duotone" width={14} /> Image Slot 2
            </button>
            <button className={panelBtn} onClick={addHeadline} title="Where the AI headline goes">
              <Icon icon="solar:text-bold-duotone" width={14} /> Headline
            </button>
            <div className="pt-2 flex items-center justify-between">
              <label className="text-[11px] font-semibold text-ink-80">Background</label>
              <input type="color" value={bgColor} onChange={(e) => setBackground(e.target.value)} className="w-8 h-8 rounded cursor-pointer border border-hairline" />
            </div>
          </>
        )}
        {activeTool === "text" && (
          <>
            <p className="text-[11px] font-bold text-ink-80 mb-1">Add text</p>
            <button className={panelBtn} onClick={addHeadline}>
              <Icon icon="solar:text-bold-duotone" width={14} /> Headline (AI title)
            </button>
            <button className={panelBtn} onClick={addText}>
              <Icon icon="solar:text-field-bold-duotone" width={14} /> Text box
            </button>
          </>
        )}
        {activeTool === "shapes" && (
          <>
            <p className="text-[11px] font-bold text-ink-80 mb-1">Shapes</p>
            <div className="grid grid-cols-2 gap-2">
              <button className={panelBtn} onClick={addRect}><Icon icon="solar:stop-bold" width={14} /> Box</button>
              <button className={panelBtn} onClick={addCircle}><Icon icon="solar:record-circle-bold" width={14} /> Circle</button>
              <button className={panelBtn} onClick={addTriangle}><Icon icon="solar:play-bold" width={14} className="rotate-[-90deg]" /> Triangle</button>
              <button className={panelBtn} onClick={addLine}><Icon icon="solar:minus-bold" width={14} /> Line</button>
            </div>
          </>
        )}
        {activeTool === "image" && (
          <>
            <p className="text-[11px] font-bold text-ink-80 mb-1">Image</p>
            <button className={panelBtn} onClick={() => fileInputRef.current?.click()}>
              <Icon icon="solar:upload-bold-duotone" width={14} /> Upload image / logo
            </button>
            <button className={panelBtn} onClick={() => addImageSlot("image")}>
              <Icon icon="solar:gallery-bold-duotone" width={14} /> Image slot (main)
            </button>
            <button className={panelBtn} onClick={() => addImageSlot("image_2")}>
              <Icon icon="solar:gallery-bold-duotone" width={14} /> Image slot 2 (related)
            </button>
          </>
        )}
        {activeTool === "draw" && (
          <>
            <p className="text-[11px] font-bold text-ink-80 mb-1">Freehand draw</p>
            <p className="text-[10px] text-ink-48">Draw directly on the canvas. Switch to another tool to stop.</p>
            <div className="flex items-center justify-between pt-1">
              <label className="text-[11px] font-semibold text-ink-80">Brush color</label>
              <input type="color" value={brushColor} onChange={(e) => setBrushColor(e.target.value)} className="w-8 h-8 rounded cursor-pointer border border-hairline" />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-ink-80 mb-0.5">Brush size: {brushWidth}px</label>
              <input type="range" min={1} max={40} value={brushWidth} onChange={(e) => setBrushWidth(parseInt(e.target.value))} className="w-full" />
            </div>
          </>
        )}
      </div>

      {/* Canvas */}
      <div className="shrink-0 border border-hairline rounded-lg overflow-hidden shadow-sm" style={{ width: width * zoom, height: height * zoom }}>
        <canvas ref={canvasElRef} />
      </div>
      <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleLogoUpload} />

      {/* Properties (contextual) */}
      <div className="flex-1 min-w-[200px] space-y-4">
        {selected && (
          <div className="rounded-lg border border-hairline p-3 space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-xs font-bold text-ink">
                {selectedRole === "title" ? "📰 Headline placeholder" : selectedRole === "image" ? "🖼 Image slot" : selectedRole === "image_2" ? "🖼 Image slot 2" : selected.type}
              </p>
              <div className="flex gap-0.5">
                <button className="p-1 rounded hover:bg-parchment" title="Bring to front" onClick={() => moveLayer("front")}>
                  <Icon icon="solar:double-alt-arrow-up-bold-duotone" width={15} className="text-ink-48" />
                </button>
                <button className="p-1 rounded hover:bg-parchment" title="Bring forward" onClick={() => moveLayer("forward")}>
                  <Icon icon="solar:alt-arrow-up-bold" width={14} className="text-ink-48" />
                </button>
                <button className="p-1 rounded hover:bg-parchment" title="Send backward" onClick={() => moveLayer("backward")}>
                  <Icon icon="solar:alt-arrow-down-bold" width={14} className="text-ink-48" />
                </button>
                <button className="p-1 rounded hover:bg-parchment" title="Send to back" onClick={() => moveLayer("back")}>
                  <Icon icon="solar:double-alt-arrow-down-bold-duotone" width={15} className="text-ink-48" />
                </button>
                <button className="p-1 rounded hover:bg-parchment" title="Duplicate" onClick={duplicateSelected}>
                  <Icon icon="solar:copy-bold-duotone" width={14} className="text-ink-48" />
                </button>
                <button className="p-1 rounded hover:bg-red-50" title="Delete" onClick={deleteSelected}>
                  <Icon icon="solar:trash-bin-trash-bold-duotone" width={14} className="text-red-500" />
                </button>
              </div>
            </div>

            {isText && (
              <>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">Font size</label>
                    <input
                      type="number"
                      className="input-rect py-1 text-xs"
                      value={Math.round(((selected as fabric.Textbox).fontSize as number) ?? 32)}
                      onChange={(e) => updateSelected({ fontSize: parseInt(e.target.value) || 32 })}
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">Color</label>
                    <input
                      type="color"
                      className="w-full h-7 rounded cursor-pointer border border-hairline"
                      value={String((selected as fabric.Textbox).fill ?? "#ffffff")}
                      onChange={(e) => updateSelected({ fill: e.target.value })}
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">Font</label>
                  <select
                    className="input-rect py-1 text-xs"
                    value={String((selected as fabric.Textbox).fontFamily ?? DEFAULT_FONT)}
                    onChange={(e) => {
                      const family = e.target.value;
                      const weight = String((selected as fabric.Textbox).fontWeight ?? "normal");
                      updateSelected({ fontFamily: family });
                      document.fonts
                        .load(`${weight === "bold" || /^\d+$/.test(weight) ? weight : "normal"} 16px "${family}"`)
                        .then(() => {
                          fabric.util.clearFabricFontCache(family);
                          withCanvas((c) => {
                            const obj = c.getActiveObject();
                            if (obj instanceof fabric.Textbox) obj.initDimensions();
                          });
                        })
                        .catch(() => {});
                    }}
                  >
                    {FONTS.map((f) => (
                      <option key={f} value={f}>{f}</option>
                    ))}
                  </select>
                </div>
                {selectedRole === "title" && (
                  <>
                    <div>
                      <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">
                        2-tone accent (colors the bottom lines)
                      </label>
                      <div className="flex items-center gap-1.5">
                        <input
                          type="color"
                          className="w-full h-7 rounded cursor-pointer border border-hairline"
                          value={(selected as TitleBox).titleAccentColor ?? "#3CD52F"}
                          onChange={(e) => setTitleAccent(e.target.value)}
                        />
                        <button className={btn} title="Disable two-tone" onClick={() => setTitleAccent(undefined)}>
                          Off
                        </button>
                      </div>
                    </div>
                    <div>
                      <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">
                        Headline case (applied to the injected news title)
                      </label>
                      <select
                        className="input-rect py-1 text-xs"
                        value={(selected as TitleBox).titleTextTransform ?? ((selected as TitleBox).titleUppercase ? "uppercase" : "none")}
                        onChange={(e) =>
                          withCanvas((c) => {
                            const obj = c.getActiveObject();
                            if (!(obj instanceof fabric.Textbox)) return;
                            const box = obj as TitleBox;
                            const v = e.target.value as TitleTransform | "none";
                            box.titleTextTransform = v === "none" ? undefined : v;
                            box.titleUppercase = undefined;
                            if (box.text) {
                              box.set({ text: transformTitle(box.text, box) });
                              applyTwoTone(box);
                            }
                          })
                        }
                      >
                        <option value="none">As generated (no change)</option>
                        <option value="uppercase">UPPERCASE ALL</option>
                        <option value="lowercase">lowercase all</option>
                        <option value="capitalize">Capitalize Each Word</option>
                      </select>
                    </div>
                  </>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">Letter spacing</label>
                    <input
                      type="number"
                      step={10}
                      className="input-rect py-1 text-xs"
                      value={Math.round(((selected as fabric.Textbox).charSpacing as number) ?? 0)}
                      onChange={(e) => updateSelected({ charSpacing: parseInt(e.target.value) || 0 })}
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">Line height</label>
                    <input
                      type="number"
                      step={0.05}
                      min={0.5}
                      max={3}
                      className="input-rect py-1 text-xs"
                      value={Number(((selected as fabric.Textbox).lineHeight as number) ?? 1.16).toFixed(2)}
                      onChange={(e) => updateSelected({ lineHeight: parseFloat(e.target.value) || 1.16 })}
                    />
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <button
                    title="Bold"
                    className={`${btn} font-bold ${((selected as fabric.Textbox).fontWeight === "bold") ? "border-primary-main text-primary-main" : ""}`}
                    onClick={() => toggleTextStyle("fontWeight")}
                  >
                    B
                  </button>
                  <button
                    title="Italic"
                    className={`${btn} italic ${((selected as fabric.Textbox).fontStyle === "italic") ? "border-primary-main text-primary-main" : ""}`}
                    onClick={() => toggleTextStyle("fontStyle")}
                  >
                    I
                  </button>
                  <button
                    title="Underline"
                    className={`${btn} underline ${((selected as fabric.Textbox).underline ? "border-primary-main text-primary-main" : "")}`}
                    onClick={() => toggleTextStyle("underline")}
                  >
                    U
                  </button>
                  <button
                    title="Strikethrough"
                    className={`${btn} line-through ${((selected as fabric.Textbox).linethrough ? "border-primary-main text-primary-main" : "")}`}
                    onClick={() => toggleTextStyle("linethrough")}
                  >
                    S
                  </button>
                  {(["left", "center", "right"] as const).map((a) => (
                    <button
                      key={a}
                      title={`Align ${a}`}
                      className={`${btn} ${((selected as fabric.Textbox).textAlign === a) ? "border-primary-main text-primary-main" : ""}`}
                      onClick={() => updateSelected({ textAlign: a })}
                    >
                      <Icon icon={`solar:align-${a === "center" ? "horizontal-center" : a}-bold`} width={12} />
                    </button>
                  ))}
                </div>
              </>
            )}
            {!isText && selectedRole !== "image" && selectedRole !== "image_2" && (
              <>
                {(selected.type === "rect" || selected.type === "circle" || selected.type === "triangle") && (
                  <div>
                    <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">Fill</label>
                    <input
                      type="color"
                      className="w-full h-7 rounded cursor-pointer border border-hairline"
                      value={String(selected.fill ?? "#dc2626")}
                      onChange={(e) => updateSelected({ fill: e.target.value })}
                    />
                  </div>
                )}
                {selected.type === "line" && (
                  <>
                    <div>
                      <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">Line color</label>
                      <input
                        type="color"
                        className="w-full h-7 rounded cursor-pointer border border-hairline"
                        value={String(selected.stroke ?? "#ffffff")}
                        onChange={(e) => updateSelected({ stroke: e.target.value })}
                      />
                    </div>
                    <div>
                      <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">Thickness: {Math.round((selected.strokeWidth as number) ?? 6)}px</label>
                      <input type="range" min={1} max={40} value={Math.round((selected.strokeWidth as number) ?? 6)} onChange={(e) => updateSelected({ strokeWidth: parseInt(e.target.value) })} className="w-full" />
                    </div>
                  </>
                )}
                <div>
                  <label className="block text-[10px] font-semibold text-ink-48 mb-0.5">Opacity: {Math.round(((selected.opacity as number) ?? 1) * 100)}%</label>
                  <input type="range" min={0} max={100} value={Math.round(((selected.opacity as number) ?? 1) * 100)} onChange={(e) => updateSelected({ opacity: parseInt(e.target.value) / 100 })} className="w-full" />
                </div>
              </>
            )}
          </div>
        )}

        <p className="text-[11px] text-ink-48 leading-relaxed">
          A template needs one <strong>Image Slot</strong> (news photo) and one <strong>Headline</strong> (AI title).
          Everything else — logo, boxes, extra text — is drawn exactly as designed.
        </p>
      </div>
    </div>
  );
}
