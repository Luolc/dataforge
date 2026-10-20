/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server forwards the API to a backend started with
// `dataforge viz serve` at the address of fixtures/site.yaml.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": "http://127.0.0.1:8800" } },
  build: { assetsInlineLimit: 0 },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test-setup.ts"],
    pool: "threads",
    maxWorkers: 4,
  },
});
