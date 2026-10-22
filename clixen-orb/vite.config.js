import { resolve } from "node:path";
import { defineConfig } from "vite";

// Both windows need to be emitted: `orb` is the sphere, `caption` is the
// whisper strip. Without the second entry Rollup only ships index.html and
// the caption window falls over to a blank surface in production builds.
export default defineConfig({
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "safari15",
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, "index.html"),
        caption: resolve(import.meta.dirname, "caption.html"),
        cursor: resolve(import.meta.dirname, "cursor.html"),
      },
    },
  },
});
