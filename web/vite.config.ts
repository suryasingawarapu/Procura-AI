import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Served by FastAPI at /app (see app/api/main.py), so assets are built relative to that base.
export default defineConfig({
  base: "/app/",
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          charts: ["recharts"],
          motion: ["framer-motion"],
        },
      },
    },
  },
  // `npm run dev`: the API runs on :8000; the app talks to it through this proxy.
  server: {
    port: 5173,
    proxy: {
      "/procurement": "http://127.0.0.1:8000",
      "/observability": "http://127.0.0.1:8000",
    },
  },
});
