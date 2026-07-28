import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard talks to the FastAPI backend (uvicorn grid_copilot.server:app)
// on :8000. Proxy /api there in dev so the browser sees a single origin.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
