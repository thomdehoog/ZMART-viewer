import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // npm runs the build from this folder, which holds the page and everything
  // Node needs to make it. The built page goes to gui/build beside it, which
  // is the folder the Python package ships.
  root: ".",
  plugins: [react()],

  // --- production build ---
  build: {
    outDir: "../build",
    emptyOutDir: true,
    // Emit the engine's background worker as a real file, never inlined as a
    // data: URL. A data:-URL worker has no origin, so absolute-path fetches from
    // inside it (how the worker loads image chunks) cannot resolve — metadata
    // would load but pixels never would. (The workers themselves are compiled
    // ahead of time by scripts/precompile-workers.mjs; see that file.)
    assetsInlineLimit: 0,
  },

  // --- dev server only (`vite dev`); the shipped app is served by Python ---
  optimizeDeps: {
    // Vite's dev-mode pre-bundling rewrites neuroglancer's `new Worker(new
    // URL(...))` and breaks it. Leaving neuroglancer out of pre-bundling keeps
    // the dev server usable. Has no effect on the production build above.
    exclude: ["neuroglancer"],
  },
  server: {
    // The page imports embedding.js from engine/drawing, where the Python
    // server also serves it to other interfaces, so the dev server must be
    // allowed to read that folder as well as this one.
    fs: { allow: [".", "../../engine/drawing"] },
    // Under `vite dev` the frontend runs on Vite's own port; anything it asks
    // for under /data (the image volume) or /api (Python) is forwarded to the
    // Python server on 8848, so the browser sees a single origin. The shipped
    // app needs none of this — Python serves everything itself.
    proxy: {
      "/data": "http://127.0.0.1:8848",
      "/api": "http://127.0.0.1:8848",
    },
  },
});
