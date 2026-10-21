/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// The dev server forwards the API to a backend started with
// `dataforge viz serve` at the address of fixtures/site.yaml.
// Times show in the browser's zone: the tests, in worker threads that share
// this process's, read them in UTC.
process.env["TZ"] = "UTC";

/**
 * The share site's page is share.html in the source and index.html in what
 * it builds: `dataforge share serve --static` serves index.html.
 */
const shareIndex: Plugin = {
  name: "share-index",
  enforce: "post",
  generateBundle(_, bundle) {
    const page = bundle["share.html"];
    if (page === undefined)
      throw new Error("the share build has no share.html");
    page.fileName = "index.html";
  },
};

// `vite build --mode share` builds the share site alone, from share.html
// into dist-share/; the viewer's build never holds its page.
export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss(), ...(mode === "share" ? [shareIndex] : [])],
  server: { proxy: { "/api": "http://127.0.0.1:8800" } },
  build:
    mode === "share"
      ? {
          assetsInlineLimit: 0,
          outDir: "dist-share",
          rolldownOptions: { input: "share.html" },
        }
      : { assetsInlineLimit: 0 },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test-setup.ts"],
    pool: "threads",
    maxWorkers: 4,
  },
}));
