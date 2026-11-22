  /// <reference types="vitest/config" />
  import react from "@vitejs/plugin-react";
  import { defineConfig } from "vite";

  // In development the browser talks to this dev server and `/api` is proxied to the FastAPI backend, so no CORS and no
  // API base URL are needed. For another origin set VITE_API_BASE_URL (and CLERK_AUTHORIZED_PARTIES on the backend).
  export default defineConfig({
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": "http://127.0.0.1:8010",
        "/health": "http://127.0.0.1:8010", 
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      css: false,
    },
  });
