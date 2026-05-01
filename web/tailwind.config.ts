import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // Matches the CV template palette — warm, natural, human
        bg: {
          base:     "#F0EDE8",   // warm off-white page
          surface:  "#F8F5F1",   // card / panel surface
          elevated: "#FFFFFF",   // elevated panels
          border:   "#D6CFC6",   // warm rule / border
        },
        accent: {
          DEFAULT: "#5C7254",    // sage green
          dim:     "#7A9670",    // lighter sage
          glow:    "rgba(92,114,84,0.10)",
        },
        text: {
          primary:   "#1C1812",  // deep warm near-black
          secondary: "#6B6158",  // warm medium gray
          muted:     "#A89D94",  // muted warm gray
        },
        status: {
          generated: "#7A9670",  // sage — new generation
          reviewing: "#C8963C",  // warm amber — under review
          applied:   "#5C7254",  // sage green — applied
          rejected:  "#9B5A5A",  // muted red
          offer:     "#4A7A6A",  // deeper sage — positive
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      animation: {
        "fade-in":  "fadeIn 0.3s ease forwards",
        "slide-up": "slideUp 0.3s ease forwards",
        "spin-slow": "spin 2s linear infinite",
      },
      keyframes: {
        fadeIn: {
          from: { opacity: "0" },
          to:   { opacity: "1" },
        },
        slideUp: {
          from: { opacity: "0", transform: "translateY(8px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
