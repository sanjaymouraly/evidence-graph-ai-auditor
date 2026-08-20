import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 950: "#070b12", 900: "#0b1220", 850: "#0f1728", 800: "#131d31", 700: "#1c2a44" },
        accent: { DEFAULT: "#e2a33c", soft: "#f3c579" },
        ok: "#43c59e",
        warn: "#e2a33c",
        bad: "#ef6461",
        info: "#6aa7f7",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: { card: "0 1px 0 rgba(255,255,255,0.04) inset, 0 12px 30px -18px rgba(0,0,0,0.8)" },
    },
  },
  plugins: [],
};
export default config;
