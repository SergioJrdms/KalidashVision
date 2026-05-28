/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        kv: {
          purple: "#7c3aed",
          "purple-dark": "#6b21a8",
          "purple-50": "#faf5ff",
          "purple-100": "#f3e8ff",
          "purple-200": "#e9d5ff",
          "purple-300": "#d8b4fe",
          indigo: "#a5b4fc",
          "indigo-bg": "#eef2ff",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};
