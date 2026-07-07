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

const FONTS = ["DejaVu Sans", "Arial", "Poppins", "Montserrat", "Georgia", "Impact", "Courier New", "Verdana"];

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
  injectImage: (src: string) => Promise<void>;
};

type Props = {
  width: number;
  height: number;
  initialJson?: Record<string, unknown> | null;
  onReady?: (api: EditorApi) => void;
};

type SlotBounds = { left: number; top: number; width: number; height: number };
type FabricObjectWithRole = fabric.Object & { [ROLE_PROP]?: string; text?: string; slotBounds?: SlotBounds };

export default function TemplateEditor({ width, height, initialJson, onReady }: Props) {
  const canvasElRef = useRef<HTMLCanvasElement>(null);
  const canvasRef = useRef<fabric.Canvas | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // loadFromJSON is async — injections requested before it completes are
  // queued here and applied in its callback (fixes the review-page race
  // where the AI headline never replaced the placeholder text)
  const jsonLoadedRef = useRef(false);
  const pendingTitleRef = useRef<string | null>(null);
  const pendingImageRef = useRef<string | null>(null);
  const [selected, setSelected] = useState<FabricObjectWithRole | null>(null);
  const [bgColor, setBgColor] = useState("#111827");
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

    // Webfonts load lazily — without this, text set to Poppins/Montserrat
    // renders with the fallback font until the next full canvas redraw.
    document.fonts
      .load('900 16px Poppins')
      .then(() => document.fonts.load('900 16px Montserrat'))
      .then(() => canvas.renderAll())
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

    jsonLoadedRef.current = !initialJson;
    if (initialJson) {
      canvas.loadFromJSON(initialJson, () => {
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
        if (pendingTitleRef.current) {
          doInjectTitle(pendingTitleRef.current);
          pendingTitleRef.current = null;
        }
        if (pendingImageRef.current) {
          doInjectImage(pendingImageRef.current).catch(() => {});
          pendingImageRef.current = null;
        }
      });
    }

    function doInjectTitle(title: string) {
      const obj = (canvas.getObjects() as FabricObjectWithRole[]).find((o) => o[ROLE_PROP] === "title");
      if (obj && "set" in obj) {
        const box = obj as TitleBox;
        title = transformTitle(title, box);
        // Auto-fit like renderer/inject.js: shrink font until the headline
        // fits the placeholder's designed height.
        const maxHeight = box.height ?? 0;
        box.set({ text: title, styles: {} });
        while ((box.height ?? 0) > maxHeight && (box.fontSize ?? 0) > 12) {
          box.set("fontSize", (box.fontSize ?? 24) - 2);
          box.initDimensions();
        }
        applyTwoTone(box as TitleBox);
        canvas.renderAll();
      }
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
        const json = canvas.toJSON([ROLE_PROP, "titleAccentColor", "titleUppercase", "titleTextTransform"]) as unknown as Record<string, unknown>;
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
        fontFamily: "DejaVu Sans",
        fill: "#ffffff",
      });
      (box as FabricObjectWithRole)[ROLE_PROP] = "title";
      c.add(box);
      c.setActiveObject(box);
    });

  const addImageSlot = () =>
    withCanvas((c) => {
      clearRole(c, "image");
      const rect = new fabric.Rect({
        left: 0,
        top: 0,
        width: width,
        height: Math.round(height * 0.66),
        fill: "#374151",
        stroke: "#9ca3af",
        strokeDashArray: [12, 8],
        strokeWidth: 2,
      });
      (rect as FabricObjectWithRole)[ROLE_PROP] = "image";
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
        fontFamily: "DejaVu Sans",
        fill: "#ffffff",
      });
      c.add(box);
      c.setActiveObject(box);
    });

  const addRect = () =>
    withCanvas((c) => {
      const rect = new fabric.Rect({
        left: 0,
        top: height * 0.66,
        width: width,
        height: height * 0.34,
        fill: "#dc2626",
      });
      c.add(rect);
      c.setActiveObject(rect);
    });

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

  const moveLayer = (dir: 1 | -1) =>
    withCanvas((c) => {
      const obj = c.getActiveObject();
      if (!obj) return;
      if (dir === 1) c.bringForward(obj);
      else c.sendBackwards(obj);
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

  return (
    <div className="flex gap-4 items-start">
      {/* Canvas */}
      <div className="shrink-0 border border-hairline rounded-lg overflow-hidden shadow-sm" style={{ width: width * zoom, height: height * zoom }}>
        <canvas ref={canvasElRef} />
      </div>

      {/* Controls */}
      <div className="flex-1 min-w-[220px] space-y-4">
        <div className="flex flex-wrap gap-1.5">
          <button className={btn} onClick={addImageSlot} title="Where the news photo goes">
            <Icon icon="solar:gallery-bold-duotone" width={13} /> Image Slot
          </button>
          <button className={btn} onClick={addHeadline} title="Where the AI headline goes">
            <Icon icon="solar:text-bold-duotone" width={13} /> Headline
          </button>
          <button className={btn} onClick={addText}>
            <Icon icon="solar:text-field-bold-duotone" width={13} /> Text
          </button>
          <button className={btn} onClick={addRect}>
            <Icon icon="solar:widget-bold-duotone" width={13} /> Box
          </button>
          <button className={btn} onClick={() => fileInputRef.current?.click()}>
            <Icon icon="solar:upload-bold-duotone" width={13} /> Logo
          </button>
          <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleLogoUpload} />
        </div>

        <div className="flex items-center gap-2">
          <label className="text-xs font-semibold text-ink-80">Background</label>
          <input type="color" value={bgColor} onChange={(e) => setBackground(e.target.value)} className="w-8 h-8 rounded cursor-pointer border border-hairline" />
        </div>

        {selected && (
          <div className="rounded-lg border border-hairline p-3 space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-xs font-bold text-ink">
                {selectedRole === "title" ? "📰 Headline placeholder" : selectedRole === "image" ? "🖼 Image slot" : selected.type}
              </p>
              <div className="flex gap-1">
                <button className="p-1 rounded hover:bg-parchment" title="Bring forward" onClick={() => moveLayer(1)}>
                  <Icon icon="solar:layers-bold-duotone" width={14} className="text-ink-48" />
                </button>
                <button className="p-1 rounded hover:bg-parchment rotate-180" title="Send backward" onClick={() => moveLayer(-1)}>
                  <Icon icon="solar:layers-bold-duotone" width={14} className="text-ink-48" />
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
                    value={String((selected as fabric.Textbox).fontFamily ?? "DejaVu Sans")}
                    onChange={(e) => {
                      const family = e.target.value;
                      const weight = String((selected as fabric.Textbox).fontWeight ?? "normal");
                      updateSelected({ fontFamily: family });
                      // Webfonts load lazily; fabric also caches char widths per
                      // family — without this the canvas keeps the fallback metrics.
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
                        <button
                          className={btn}
                          title="Disable two-tone"
                          onClick={() => setTitleAccent(undefined)}
                        >
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
                            box.titleUppercase = undefined; // legacy flag superseded
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
                <div className="flex gap-1.5">
                  <button
                    className={`${btn} ${((selected as fabric.Textbox).fontWeight === "bold") ? "border-primary-main text-primary-main" : ""}`}
                    onClick={() => updateSelected({ fontWeight: (selected as fabric.Textbox).fontWeight === "bold" ? "normal" : "bold" })}
                  >
                    B
                  </button>
                  {(["left", "center", "right"] as const).map((a) => (
                    <button
                      key={a}
                      className={`${btn} ${((selected as fabric.Textbox).textAlign === a) ? "border-primary-main text-primary-main" : ""}`}
                      onClick={() => updateSelected({ textAlign: a })}
                    >
                      <Icon icon={`solar:align-${a === "center" ? "horizontal-center" : a}-bold`} width={12} />
                    </button>
                  ))}
                </div>
              </>
            )}
            {!isText && selected.type === "rect" && selectedRole !== "image" && (
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
