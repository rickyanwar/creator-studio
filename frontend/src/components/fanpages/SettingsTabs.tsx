"use client";

import type React from "react";

export type SettingsTabId =
  | "global"
  | "ig"
  | "news"
  | "discussion"
  | "pinterest"
  | "facebook"
  | "youtube"
  | "aivideo"
  | "radar";

export interface SettingsTab {
  id: SettingsTabId;
  label: string;
  mode?: string;
  enabled?: boolean;
}

export function SettingsTabs({
  tabs,
  active,
  onChange,
}: {
  tabs: SettingsTab[];
  active: SettingsTabId;
  onChange: (id: SettingsTabId) => void;
}) {
  function handleKeyDown(e: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) {
    if (e.key === "ArrowRight") {
      e.preventDefault();
      const nextIndex = (currentIndex + 1) % tabs.length;
      const nextTab = tabs[nextIndex];
      onChange(nextTab.id);
      document.getElementById("tab-" + nextTab.id)?.focus();
    } else if (e.key === "ArrowLeft") {
      e.preventDefault();
      const prevIndex = (currentIndex - 1 + tabs.length) % tabs.length;
      const prevTab = tabs[prevIndex];
      onChange(prevTab.id);
      document.getElementById("tab-" + prevTab.id)?.focus();
    }
  }

  return (
    <div className="sticky top-16 z-40 -mx-1 px-1 py-2 bg-bg-default border-b border-hairline">
      <div role="tablist" aria-label="Fanpage settings" className="flex gap-1.5 overflow-x-auto sm:flex-wrap sm:overflow-visible">
        {tabs.map((tab, idx) => {
          const isSelected = active === tab.id;
          const title =
            tab.label +
            (tab.enabled === undefined ? "" : tab.enabled ? " — on" : " — off");

          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={"tab-" + tab.id}
              aria-selected={isSelected}
              aria-controls={"tab-panel-" + tab.id}
              tabIndex={isSelected ? 0 : -1}
              title={title}
              onClick={() => onChange(tab.id)}
              onKeyDown={(e) => handleKeyDown(e, idx)}
              className={`shrink-0 inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold whitespace-nowrap transition-colors ${
                isSelected
                  ? "bg-primary-main text-white"
                  : "text-text-secondary hover:bg-bg-paper-hover hover:text-text-primary"
              }`}
            >
              {tab.enabled !== undefined && (
                <span
                  className={
                    "w-1.5 h-1.5 rounded-full " +
                    (tab.enabled ? "bg-emerald-500" : "bg-hairline")
                  }
                  aria-hidden
                />
              )}
              {tab.label}
              {tab.mode && (
                <span className="opacity-70 font-normal">{tab.mode}</span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
