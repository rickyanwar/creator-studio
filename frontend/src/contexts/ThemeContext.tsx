"use client";

import { createContext, useContext, useEffect, useState } from "react";

export type Theme    = "dark" | "light";
export type Preset   = "default" | "cyan" | "purple" | "blue" | "orange" | "red";
export type NavColor = "integrate" | "apparent";

interface ThemeCtx {
  theme:       Theme;
  compact:     boolean;
  navColor:    NavColor;
  preset:      Preset;
  toggle:      () => void;
  setCompact:  (v: boolean) => void;
  setNavColor: (v: NavColor) => void;
  setPreset:   (v: Preset) => void;
}

const ThemeContext = createContext<ThemeCtx>({
  theme: "dark", compact: false, navColor: "integrate", preset: "default",
  toggle: () => {}, setCompact: () => {}, setNavColor: () => {}, setPreset: () => {},
});

function applyToDOM(theme: Theme, compact: boolean, navColor: NavColor, preset: Preset) {
  const html = document.documentElement;
  html.classList.toggle("dark", theme === "dark");
  html.classList.toggle("compact", compact);
  if (preset === "default") html.removeAttribute("data-preset");
  else html.dataset.preset = preset;
  html.dataset.navColor = navColor;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme,    setTheme]    = useState<Theme>("dark");
  const [compact,  setCompactS] = useState(false);
  const [navColor, setNavColorS]= useState<NavColor>("integrate");
  const [preset,   setPresetS]  = useState<Preset>("default");

  useEffect(() => {
    const t  = (localStorage.getItem("theme")    as Theme    | null) ?? "dark";
    const c  = localStorage.getItem("compact")   === "true";
    const nc = (localStorage.getItem("navColor") as NavColor | null) ?? "integrate";
    const p  = (localStorage.getItem("preset")   as Preset   | null) ?? "default";
    setTheme(t); setCompactS(c); setNavColorS(nc); setPresetS(p);
    applyToDOM(t, c, nc, p);
  }, []);

  function toggle() {
    setTheme(prev => {
      const next = prev === "dark" ? "light" : "dark";
      localStorage.setItem("theme", next);
      applyToDOM(next, compact, navColor, preset);
      return next;
    });
  }

  function setCompact(v: boolean) {
    setCompactS(v);
    localStorage.setItem("compact", String(v));
    applyToDOM(theme, v, navColor, preset);
  }

  function setNavColor(v: NavColor) {
    setNavColorS(v);
    localStorage.setItem("navColor", v);
    applyToDOM(theme, compact, v, preset);
  }

  function setPreset(v: Preset) {
    setPresetS(v);
    localStorage.setItem("preset", v);
    applyToDOM(theme, compact, navColor, v);
  }

  return (
    <ThemeContext.Provider value={{ theme, compact, navColor, preset, toggle, setCompact, setNavColor, setPreset }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
