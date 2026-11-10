import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Kehitys: npm run dev (http://127.0.0.1:5173), /api ohjataan käynnissä olevaan backendiin.
// Tuotanto: npm run build -> dist/, jonka FastAPI tarjoilee osoitteessa http://127.0.0.1:8765.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true, // backend hyväksyy vain oman Host-otsikkonsa
      },
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 800 },
});
