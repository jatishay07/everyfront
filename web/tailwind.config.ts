import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#07090c",
          925: "#0b0e13",
          900: "#0f131a",
          850: "#141922",
          800: "#1a212c",
          700: "#232b38",
          600: "#313c4d",
          500: "#465065",
          400: "#6b7789",
          300: "#96a1b3",
          200: "#c3cad6",
          100: "#e6e9ee",
        },
        signal: {
          amber: "#f2a93b",
          red: "#f0554a",
          green: "#3fbf83",
          blue: "#4c8dff",
          violet: "#9b7bf0",
        },
      },
      fontFamily: {
        sans: [
          "-apple-system",
          "BlinkMacSystemFont",
          "Inter",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 8px 24px -12px rgba(0,0,0,0.6)",
      },
      keyframes: {
        pulseDot: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
        riseIn: {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        pulseDot: "pulseDot 1.6s ease-in-out infinite",
        riseIn: "riseIn 0.25s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
