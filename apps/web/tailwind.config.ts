import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Confidence bands drive the whole review screen, so they are named
        // by meaning rather than by hue.
        confident: "#15803d",
        uncertain: "#b45309",
        doubtful: "#b91c1c",
      },
    },
  },
  plugins: [],
} satisfies Config;
