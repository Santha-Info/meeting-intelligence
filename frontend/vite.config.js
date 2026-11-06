import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// In dev, Vite serves the UI on :5173 and proxies /api to FastAPI on :8000.
// In the container, FastAPI serves the built files from frontend/dist.
export default defineConfig({
  plugins: [vue()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
