/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // ── Primary — CSS-variable-driven for preset switching ──
        "primary-lighter":  "var(--primary-lighter)",
        "primary-light":    "var(--primary-light)",
        "primary-main":     "var(--primary-main)",
        "primary-dark":     "var(--primary-dark)",
        "primary-darker":   "var(--primary-darker)",

        // ── Secondary (Purple) ─────────────────────────
        "secondary-lighter":"#EFD6FF",
        "secondary-light":  "#C684FF",
        "secondary-main":   "#8E33FF",
        "secondary-dark":   "#5119B7",
        "secondary-darker": "#27097A",

        // ── Info (Cyan) ────────────────────────────────
        "info-lighter":     "#CAFDF5",
        "info-light":       "#61F3F3",
        "info-main":        "#00B8D9",
        "info-dark":        "#006C9C",
        "info-darker":      "#003768",

        // ── Warning (Amber) ────────────────────────────
        "warning-lighter":  "#FFF5CC",
        "warning-light":    "#FFD666",
        "warning-main":     "#FFAB00",
        "warning-dark":     "#B76E00",
        "warning-darker":   "#7A4100",

        // ── Error (Coral) ──────────────────────────────
        "error-lighter":    "#FFE9D5",
        "error-light":      "#FFAC82",
        "error-main":       "#FF5630",
        "error-dark":       "#B71D18",
        "error-darker":     "#7A0916",

        // ── Grey scale ─────────────────────────────────
        "grey-400": "#C4CDD5",
        "grey-500": "#919EAB",
        "grey-600": "#637381",
        "grey-700": "#454F5B",
        "grey-800": "#1C252E",
        "grey-900": "#141A21",

        // ── Surfaces & text — CSS-variable-driven for theme switching ──
        "bg-default":       "var(--bg-default)",
        "bg-paper":         "var(--bg-paper)",
        "bg-paper-hover":   "var(--bg-paper-hover)",
        "text-primary":     "var(--text-primary)",
        "text-secondary":   "var(--text-secondary)",
        "text-disabled":    "var(--text-disabled)",
        "divider-soft":     "var(--divider-soft)",

        // ── Legacy aliases so existing pages still compile ─
        primary:            "#00A76F",
        "primary-focus":    "#007867",
        "primary-dark-alias":"#004B50",
        ink:                "var(--text-primary)",
        "ink-80":           "var(--text-secondary)",
        "ink-48":           "var(--text-disabled)",
        canvas:             "var(--bg-paper)",
        parchment:          "var(--bg-default)",
        pearl:              "var(--bg-paper-hover)",
        "surface-black":    "var(--bg-default)",
        hairline:           "var(--divider-soft)",
        muted:              "var(--text-disabled)",
        "tile-1":           "var(--bg-paper)",
        "tile-2":           "var(--bg-paper-hover)",
      },

      fontFamily: {
        sans:    ['"Inter Variable"', "Inter", "-apple-system", "BlinkMacSystemFont", "sans-serif"],
        display: ['"Inter Variable"', "Inter", "-apple-system", "sans-serif"],
        body:    ['"Inter Variable"', "Inter", "-apple-system", "sans-serif"],
      },

      fontSize: {
        // ── Minimals scale ─────────────────────────────
        "xs":    ["12px", { lineHeight: "1.5", fontWeight: "600" }],
        "sm":    ["13px", { lineHeight: "1.5", fontWeight: "400" }],
        "body":  ["14px", { lineHeight: "1.57", fontWeight: "400" }],
        "md":    ["15px", { lineHeight: "1.5", fontWeight: "600" }],
        "lg":    ["16px", { lineHeight: "1.5", fontWeight: "700" }],
        "h6":    ["18px", { lineHeight: "1.55", fontWeight: "700" }],
        "h5":    ["20px", { lineHeight: "1.5", fontWeight: "700" }],
        "h4":    ["24px", { lineHeight: "1.5", fontWeight: "700" }],
        "h3":    ["30px", { lineHeight: "1.5", fontWeight: "800" }],
        "h2":    ["40px", { lineHeight: "1.22", fontWeight: "800" }],
        "display":["64px",{ lineHeight: "1.1", fontWeight: "800" }],

        // ── Legacy aliases ─────────────────────────────
        "hero":        ["48px", { lineHeight: "1.1" }],
        "display-lg":  ["40px", { lineHeight: "1.22" }],
        "display-md":  ["28px", { lineHeight: "1.3", fontWeight: "700" }],
        "lead":        ["24px", { lineHeight: "1.5" }],
        "tagline":     ["18px", { lineHeight: "1.55", fontWeight: "700" }],
        "body-lg":     ["15px", { lineHeight: "1.5" }],
        "caption":     ["13px", { lineHeight: "1.5" }],
        "fine":        ["12px", { lineHeight: "1.5" }],
      },

      borderRadius: {
        "xs":     "6px",
        "sm":     "8px",
        "md":     "10px",
        "lg":     "16px",
        "xl":     "20px",
        "2xl":    "24px",
        "pill":   "999px",
        "circle": "50%",
      },

      boxShadow: {
        "card":        "0 0 2px 0 rgba(0,0,0,0.6), 0 12px 24px -4px rgba(0,0,0,0.4)",
        "dropdown":    "0 0 2px 0 rgba(0,0,0,0.6), 0 20px 40px -4px rgba(0,0,0,0.5)",
        "primary-btn": "0 8px 16px 0 rgba(0,167,111,0.24)",
        "error-btn":   "0 8px 16px 0 rgba(255,86,48,0.24)",
        "product":     "rgba(0,0,0,0.4) 3px 5px 30px 0",
        "card-light":  "0 0 2px 0 rgba(145,158,171,0.20), 0 12px 24px -4px rgba(145,158,171,0.12)",
      },

      spacing: {
        "sidebar": "280px",
        "section": "80px",
      },
    },
  },
  plugins: [],
};
