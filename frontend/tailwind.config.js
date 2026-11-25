/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ctrl: {
          root: "#090c10",
          panel: "#0f141b",
          elevated: "#151c26",
          hover: "#1b2431",
          border: "#1f2937",
          strong: "#2e3c4f"
        }
      },
      fontFamily: {
        sans: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "Inter", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "SFMono-Regular", "Consolas", "monospace"]
      }
    }
  },
  plugins: []
};
