import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#17201C",
        mint: "#CFE8D6",
        pine: "#1F6B4E",
        tea: "#F3EBDD",
        coral: "#D96C4A",
        navy: "#002659",
        primary: {
          50: "#EBF4FB",
          100: "#CCE5F7",
          600: "#004D9F",
          700: "#004391",
        },
        teal: {
          50: "#E8FBF7",
          100: "#CCF2F2",
          200: "#80E0E0",
          500: "#00A0A0",
          600: "#009090",
          700: "#007575",
        },
      },
      fontFamily: {
        sans: ["PingFang SC", "Microsoft YaHei", "Source Han Sans CN", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "SFMono-Regular", "Consolas", "monospace"],
      },
      boxShadow: {
        panel: "0 8px 24px rgba(16, 39, 54, 0.07)",
      },
    },
  },
  plugins: [],
} satisfies Config;
