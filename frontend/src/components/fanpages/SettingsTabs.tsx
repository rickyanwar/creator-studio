"use client";

import type React from "react";

export type SettingsTabId =
  | "content"
  | "channels"
  | "pacing"
  | "captions"
  | "design"
  | "ideas";

export interface SettingsTab {
  id: SettingsTabId;
  label: string;
  count?: string;
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
    <div
      role="tablist"
      aria-label="Brand settings sections"
      className="flex flex-wrap gap-1 p-1 rounded-2xl bg-surface2 border border-line"
    >
      {tabs.map((tab, idx) => {
        const isSelected = active === tab.id;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={"tab-" + tab.id}
            aria-selected={isSelected}
            aria-controls={"tab-panel-" + tab.id}
            tabIndex={isSelected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(e) => handleKeyDown(e, idx)}
            className={`flex-1 min-w-[120px] inline-flex items-center justify-center gap-2 min-h-[44px] px-4 rounded-xl text-sm transition-all cursor-pointer ${
              isSelected
                ? "bg-surface text-pri-text font-semibold shadow-sm border border-pri"
                : "bg-transparent text-text font-medium border border-transparent hover:bg-surface/50"
            }`}
          >
            <span>{tab.label}</span>
            {tab.count && (
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-surface2 text-text2 tabular-nums">
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
