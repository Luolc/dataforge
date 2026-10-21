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
 * The share site's page is share.html, or share-static.html, in the source
 * and index.html in what it builds: `dataforge share serve --static` serves
 * index.html, and a static host serves it for every path it does not hold.
 */
const shareIndex = (page: string): Plugin => ({
  name: "share-index",
  enforce: "post",
  generateBundle(_, bundle) {
    const built = bundle[page];
    if (built === undefined) throw new Error(`the share build has no ${page}`);
    built.fileName = "index.html";
  },
});

/** The share site's builds, by mode: its page and where it goes. */
const shareBuilds: Record<
  string,
  { page: string; outDir: string } | undefined
> = {
  share: { page: "share.html", outDir: "dist-share" },
  "share-static": { page: "share-static.html", outDir: "dist-share-static" },
};

// `vite build --mode share` builds the share site alone, from share.html
// into dist-share/, and `--mode share-static` its static site, from
// share-static.html into dist-share-static/; the viewer's build never holds
// either page.
export default defineConfig(({ mode }) => {
  const share = shareBuilds[mode];
  return {
    plugins: [
      react(),
      tailwindcss(),
      ...(share === undefined ? [] : [shareIndex(share.page)]),
    ],
    server: { proxy: { "/api": "http://127.0.0.1:8800" } },
    build:
      share === undefined
        ? { assetsInlineLimit: 0 }
        : {
            assetsInlineLimit: 0,
            outDir: share.outDir,
            rolldownOptions: { input: share.page },
          },
    test: {
      environment: "jsdom",
      setupFiles: ["src/test-setup.ts"],
      pool: "threads",
      maxWorkers: 4,
    },
  };
});
