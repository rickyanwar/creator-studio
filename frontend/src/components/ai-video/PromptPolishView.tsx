"use client";

import { useState } from "react";

export interface PromptPolishClipItem {
  index?: number;
  mode: string;
  prompt: string;
  prompt_original?: string | null;
}

export interface PromptPolishViewProps {
  clips: PromptPolishClipItem[];
  changes?: string[];
}

export function PromptPolishView({ clips = [], changes = [] }: PromptPolishViewProps) {
  const [activeTabs, setActiveTabs] = useState<Record<number, "argon" | "original">>({});
  const [collapsed, setCollapsed] = useState<Record<number, boolean>>({});
  const [copiedKey, setCopiedKey] = useState<number | null>(null);

  const toggleCollapse = (idx: number) => {
    setCollapsed((prev) => ({ ...prev, [idx]: !prev[idx] }));
  };

  const setTab = (idx: number, tab: "argon" | "original") => {
    setActiveTabs((prev) => ({ ...prev, [idx]: tab }));
  };

  const copyText = async (idx: number, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(idx);
      setTimeout(() => {
        setCopiedKey((curr) => (curr === idx ? null : curr));
      }, 2000);
    } catch (err) {
      console.error("Failed to copy prompt:", err);
    }
  };

  if (!clips || clips.length === 0) {
    return <p className="text-[11px] text-text2 italic">No clips available.</p>;
  }

  return (
    <div className="space-y-2 text-xs">
      <div className="space-y-1.5">
        {clips.map((clip, idx) => {
          const isCollapsed = Boolean(collapsed[idx]);
          const tab = activeTabs[idx] ?? "argon";
          const showOriginalTab = Boolean(clip.prompt_original);
          const isShowingOriginal = tab === "original" && showOriginalTab;
          const textToShow = isShowingOriginal ? (clip.prompt_original ?? "") : clip.prompt;
          const clipNumber = clip.index ?? idx + 1;

          return (
            <div
              key={idx}
              className="border border-line rounded-lg overflow-hidden bg-surface"
            >
              <div
                onClick={() => toggleCollapse(idx)}
                className="flex items-center justify-between px-3 py-2 bg-surface2/60 hover:bg-surface2 cursor-pointer select-none text-[12px] font-semibold text-text"
              >
                <span>
                  Clip {clipNumber} ({clip.mode})
                </span>
                <span className="text-[10px] text-text2">
                  {isCollapsed ? "▼ Show" : "▲ Hide"}
                </span>
              </div>

              {!isCollapsed && (
                <div className="p-2.5 space-y-2 border-t border-line">
                  <div className="flex flex-wrap items-center justify-between gap-1.5">
                    <div className="inline-flex rounded-md bg-surface2 p-0.5 border border-line">
                      <button
                        type="button"
                        onClick={() => setTab(idx, "argon")}
                        className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                          !isShowingOriginal
                            ? "bg-pri text-white shadow-xs"
                            : "text-text2 hover:text-text"
                        }`}
                      >
                        Argon
                      </button>
                      {showOriginalTab && (
                        <button
                          type="button"
                          onClick={() => setTab(idx, "original")}
                          className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                            isShowingOriginal
                              ? "bg-pri text-white shadow-xs"
                              : "text-text2 hover:text-text"
                          }`}
                        >
                          Original
                        </button>
                      )}
                    </div>

                    <button
                      type="button"
                      onClick={() => copyText(idx, textToShow)}
                      className="px-2 py-0.5 rounded border border-line bg-surface hover:bg-surface2 text-text text-[11px] font-medium transition-colors cursor-pointer"
                    >
                      {copiedKey === idx ? "✓ Copied" : "Copy"}
                    </button>
                  </div>

                  <pre className="whitespace-pre-wrap text-[11px] font-mono leading-relaxed bg-surface2/70 p-2 rounded border border-line text-text break-words select-text">
                    {textToShow}
                  </pre>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {changes && changes.length > 0 && (
        <div className="mt-2.5 pt-2 border-t border-line">
          <div className="text-[11px] font-semibold text-text mb-1">
            Changes by Argon
          </div>
          <ul className="list-disc list-inside space-y-0.5 text-[11px] text-text2">
            {changes.map((item, cIdx) => (
              <li key={cIdx} className="break-words">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
