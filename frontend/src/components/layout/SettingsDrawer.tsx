"use client";

import { Icon } from "@iconify/react";
import { useTheme, type Preset, type NavColor } from "@/contexts/ThemeContext";
import { clsx } from "clsx";

interface Props { open: boolean; onClose: () => void; }

const PRESETS: { id: Preset; color: string; label: string }[] = [
  { id: "default", color: "#00A76F", label: "Green"  },
  { id: "cyan",    color: "#00B8D9", label: "Cyan"   },
  { id: "purple",  color: "#7635DC", label: "Purple" },
  { id: "blue",    color: "#2065D1", label: "Blue"   },
  { id: "orange",  color: "#FFAB00", label: "Orange" },
  { id: "red",     color: "#FF5630", label: "Red"    },
];

// Visual-only toggle — click is handled by the parent SettingCard button
function Toggle({ checked }: { checked: boolean }) {
  return (
    <div
      role="switch"
      aria-checked={checked}
      className="relative w-10 h-6 rounded-full flex-shrink-0 transition-colors duration-200"
      style={checked
        ? { background: "var(--primary-main)" }
        : { background: "var(--bg-paper-hover)", border: "1.5px solid var(--divider-soft)" }
      }
    >
      <span
        className="absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-all duration-200"
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
      className="flex flex-col gap-3 p-3 rounded-xl border border-divider-soft hover:border-primary-main transition-colors text-left w-full"
      style={{ background: "var(--bg-paper-hover)" }}
    >
      <div className="flex items-center justify-between w-full">
        <Icon icon={icon} width={22} className="text-text-secondary" />
        <Toggle checked={checked} />
      </div>
      <span className="text-sm font-semibold text-text-primary">{label}</span>
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
          "fixed top-0 right-0 h-full w-[360px] z-[301] flex flex-col",
          "shadow-dropdown overflow-hidden transition-transform duration-300 ease-in-out"
        )}
        style={{ background: "var(--bg-paper)", transform: open ? "translateX(0)" : "translateX(100%)" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 h-16 flex-shrink-0 border-b border-divider-soft">
          <h2 className="text-base font-bold text-text-primary">Settings</h2>
          <div className="flex items-center gap-1">
            <button
              onClick={handleReset}
              className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-bg-paper-hover transition-colors text-text-secondary hover:text-text-primary"
              title="Reset to default"
            >
              <Icon icon="solar:restart-bold-duotone" width={18} />
            </button>
            <button
              onClick={onClose}
              className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-bg-paper-hover transition-colors text-text-secondary hover:text-text-primary"
            >
              <Icon icon="solar:close-circle-bold-duotone" width={18} />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">

          {/* 2×2 toggles */}
          <div className="grid grid-cols-2 gap-3">
            <SettingCard
              icon={theme === "dark" ? "solar:moon-bold-duotone" : "solar:sun-bold-duotone"}
              label="Mode"
              checked={theme === "dark"}
              onChange={toggle}
            />
            <SettingCard
              icon="solar:contrast-bold-duotone"
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

          {/* Nav section */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="px-3 py-1 rounded-full bg-text-primary text-bg-paper text-xs font-bold">Nav</span>
            </div>

            <div
              className="rounded-xl border border-divider-soft p-4 space-y-4"
              style={{ background: "var(--bg-default)" }}
            >
              {/* Nav Color */}
              <div>
                <p className="text-xs font-semibold text-text-secondary mb-2 uppercase tracking-wide">Color</p>
                <div className="grid grid-cols-2 gap-2">
                  {(["integrate", "apparent"] as NavColor[]).map((c) => (
                    <button
                      key={c}
                      onClick={() => setNavColor(c)}
                      className={clsx(
                        "flex items-center gap-2 px-3 py-3 rounded-xl border-2 transition-colors",
                        navColor === c
                          ? "border-primary-main bg-[rgba(var(--primary-main),0.08)]"
                          : "border-divider-soft hover:border-primary-main"
                      )}
                      style={navColor === c
                        ? { borderColor: "var(--primary-main)", background: "rgba(0,167,111,0.08)" }
                        : {}}
                    >
                      <Icon
                        icon={c === "integrate" ? "solar:sidebar-minimalistic-bold-duotone" : "solar:sidebar-bold-duotone"}
                        width={20}
                        style={{ color: navColor === c ? "var(--primary-main)" : "var(--text-secondary)" }}
                      />
                      <span
                        className="text-sm font-semibold capitalize"
                        style={{ color: navColor === c ? "var(--primary-main)" : "var(--text-secondary)" }}
                      >
                        {c}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Presets section */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <span className="px-3 py-1 rounded-full bg-text-primary text-bg-paper text-xs font-bold">Presets</span>
            </div>

            <div className="grid grid-cols-3 gap-2">
              {PRESETS.map(({ id, color, label }) => (
                <button
                  key={id}
                  onClick={() => setPreset(id)}
                  className={clsx(
                    "flex flex-col items-center gap-2 p-3 rounded-xl border-2 transition-colors",
                    preset === id ? "border-[2px]" : "border-divider-soft hover:border-divider-soft"
                  )}
                  style={{
                    background: "var(--bg-default)",
                    borderColor: preset === id ? color : undefined,
                  }}
                >
                  <div
                    className="w-8 h-8 rounded-full flex items-center justify-center"
                    style={{ background: color }}
                  >
                    {preset === id && (
                      <Icon icon="solar:check-bold" width={14} className="text-white" />
                    )}
                  </div>
                  <span className="text-[11px] font-semibold text-text-secondary">{label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
