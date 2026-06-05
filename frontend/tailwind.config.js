/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Mapeia variáveis CSS do design (Kalidash + Prism)
        ink: "var(--ink)",
        text: "var(--text)",
        muted: "var(--muted)",
        faint: "var(--faint)",
        line: "var(--line)",
        "line-2": "var(--line-2)",
        surface: "var(--surface)",
        soft: "var(--soft)",
        "app-bg": "var(--app-bg)",
        accent: "var(--accent)",
        "accent-deep": "var(--accent-deep)",
        va: "var(--va)",
        apoio: "var(--apoio)",
        desp: "var(--desp)",
        none: "var(--none)",
        p: {
          25: "#F7F5FD",
          50: "#EFEBFC",
          100: "#DDD5F7",
          200: "#C5B9F5",
          300: "#A78BFA",
          500: "#683BED",
          600: "#5E37D6",
          700: "#5330C0",
          800: "#4B2BB0",
          900: "#44279C",
        },
        // Aliases pra compatibilidade com componentes legados
        kv: {
          purple: "#5330C0",
          "purple-dark": "#44279C",
          "purple-50": "#EFEBFC",
          "purple-100": "#DDD5F7",
          "purple-200": "#C5B9F5",
          "purple-300": "#A78BFA",
          indigo: "#A78BFA",
          "indigo-bg": "#EFEBFC",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["Plus Jakarta Sans", "Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SF Mono", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
