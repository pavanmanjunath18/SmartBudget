import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on :8000; proxying /api keeps the browser on one origin,
// so the backend needs no CORS setup. In Docker, nginx does the same proxying.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.VITE_API_PROXY ?? "http://localhost:8000" },
  },
});
