/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        niger: {
          green: "#008751",
          orange: "#E05A10",
          gold: "#D4AF37",
          dark: "#0F172A",
          charcoal: "#1E293B",
        }
      }
    },
  },
  plugins: [],
}
