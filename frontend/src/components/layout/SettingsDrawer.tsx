"use client";

import { Icon } from "@iconify/react";
import { useTheme, type Preset, type NavColor } from "@/contexts/ThemeContext";
import { clsx } from "clsx";

interface Props { open: boolean; onClose: () => void; }

const PRESETS: { id: Preset; color: string; label: string }[] = [
  { id: "default", color: "#007A55", label: "Green"  },
  { id: "cyan",    color: "#00B8D9", label: "Cyan"   },
  { id: "purple",  color: "#7635DC", label: "Purple" },
  { id: "blue",    color: "#2065D1", label: "Blue"   },
  { id: "orange",  color: "#FFAB00", label: "Orange" },
  { id: "red",     color: "#FF5630", label: "Red"    },
];

// Visual-only toggle — click handled by parent SettingCard button
function Toggle({ checked }: { checked: boolean }) {
  return (
    <div
      role="switch"
      aria-checked={checked}
      className={clsx(
        "relative w-10 h-6 rounded-full flex-shrink-0 transition-colors duration-200",
        checked ? "bg-pri" : "bg-surface2 border border-line"
      )}
    >
      <span
        className="absolute top-0.5 w-5 h-5 rounded-full bg-white shadow-sm transition-all duration-200"
        style={{ left: checked ? "calc(100% - 22px)" : "2px" }}
      />
    </div>
  );
}

function SettingCard({
  icon, label, checked, onChange,
}: { icon: string; label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!checked)}
      className="flex flex-col gap-3 p-3 rounded-xl border border-line bg-surface2 hover:border-pri transition-colors text-left w-full cursor-pointer"
    >
      <div className="flex items-center justify-between w-full">
        <Icon icon={icon} width={22} className="text-text2" />
        <Toggle checked={checked} />
      </div>
      <span className="text-sm font-semibold text-text">{label}</span>
    </button>
  );
}

export default function SettingsDrawer({ open, onClose }: Props) {
  const { theme, compact, navColor, preset, toggle, setCompact, setNavColor, setPreset } = useTheme();

  function handleReset() {
    setCompact(false);
    setNavColor("integrate");
    setPreset("default");
    if (theme === "dark") toggle();
  }

  return (
    <>
      {/* Backdrop */}
      {open && (
        <div
          className="fixed inset-0 z-[300] bg-black/40 backdrop-blur-sm"
          onClick={onClose}
        />
      )}

      {/* Drawer */}
      <div
        className={clsx(
          "fixed top-0 right-0 h-full w-[360px] max-w-[100vw] z-[301] flex flex-col bg-surface border-l border-line",
          "overflow-hidden transition-transform duration-300 ease-in-out",
          open ? "shadow-dropdown" : "shadow-none pointer-events-none"
        )}
        style={{ transform: open ? "translateX(0)" : "translateX(calc(100% + 80px))" }}
        aria-hidden={!open}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 h-16 flex-shrink-0 border-b border-line">
          <h2 className="text-base font-bold text-text">Settings</h2>
          <div className="flex items-center gap-1">
            <button
              onClick={handleReset}
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-surface2 transition-colors text-text2 hover:text-text cursor-pointer"
              title="Reset to default"
              aria-label="Reset to default"
            >
              <Icon icon="solar:restart-bold-duotone" width={18} />
            </button>
            <button
              onClick={onClose}
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-surface2 transition-colors text-text2 hover:text-text cursor-pointer"
              title="Close settings"
              aria-label="Close settings"
            >
              <Icon icon="solar:close-circle-bold-duotone" width={18} />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {/* Mode / Contrast / RTL / Compact toggles */}
          <div className="grid grid-cols-2 gap-3">
            <SettingCard
              icon={theme === "dark" ? "solar:moon-bold-duotone" : "solar:sun-bold-duotone"}
              label="Mode"
              checked={theme === "dark"}
              onChange={toggle}
            />
            <SettingCard
              icon="solar:sun-2-bold-duotone"
              label="Contrast"
              checked={false}
              onChange={() => {}}
            />
            <SettingCard
              icon="solar:align-right-bold-duotone"
              label="Right to left"
              checked={false}
              onChange={() => {}}
            />
            <SettingCard
              icon="solar:minimize-square-bold-duotone"
              label="Compact"
              checked={compact}
              onChange={setCompact}
            />
          </div>

          {/* Nav Section */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="px-3 py-1 rounded-full bg-text text-surface text-xs font-bold">Nav</span>
            </div>

            <div className="rounded-xl border border-line bg-surface2 p-4 space-y-4">
              <div>
                <p className="text-xs font-semibold text-text2 mb-2 uppercase tracking-wide">Color</p>
                <div className="grid grid-cols-2 gap-2">
                  {(["integrate", "apparent"] as NavColor[]).map((c) => (
                    <button
                      key={c}
                      onClick={() => setNavColor(c)}
                      className={clsx(
                        "flex items-center gap-2 px-3 py-2.5 rounded-xl border transition-colors cursor-pointer",
                        navColor === c
                          ? "border-pri bg-pri-soft text-pri-text font-semibold"
                          : "border-line bg-surface text-text hover:border-pri"
                      )}
                    >
                      <Icon
                        icon={c === "integrate" ? "solar:sidebar-minimalistic-bold-duotone" : "solar:sidebar-bold-duotone"}
                        width={20}
                      />
                      <span className="text-sm capitalize">{c}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Presets Section */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="px-3 py-1 rounded-full bg-text text-surface text-xs font-bold">Presets</span>
            </div>

            <div className="grid grid-cols-3 gap-2">
              {PRESETS.map(({ id, color, label }) => (
                <button
                  key={id}
                  onClick={() => setPreset(id)}
                  className={clsx(
                    "flex flex-col items-center gap-2 p-3 rounded-xl border transition-colors cursor-pointer bg-surface2",
                    preset === id ? "border-pri ring-2 ring-pri/20" : "border-line hover:border-line/80"
                  )}
                >
                  <div
                    className="w-7 h-7 rounded-full flex items-center justify-center text-white"
                    style={{ background: color }}
                  >
                    {preset === id && (
                      <Icon icon="solar:check-bold" width={14} />
                    )}
                  </div>
                  <span className="text-[11px] font-semibold text-text2">{label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
