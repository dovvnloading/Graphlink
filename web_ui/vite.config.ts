import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const __dirname = dirname(fileURLToPath(import.meta.url));

// One entry per island. Each island builds as its own fully self-contained
// bundle (own index.html, own outDir) - not a shared-chunk multi-page app -
// since graphlink_composer_web.py's _inline_bundle() inlines exactly one
// CSS file + one JS module per island into a single offline HTML document.
// Registering an island here does not build it; GRAPHLINK_ISLAND below
// selects which one this invocation builds.
const ISLANDS = ["composer"];

const island = process.env.GRAPHLINK_ISLAND || "composer";
if (!ISLANDS.includes(island)) {
  throw new Error(
    `Unknown island "${island}" (set via GRAPHLINK_ISLAND). Known islands: ${ISLANDS.join(", ")}`,
  );
}

export default defineConfig({
  plugins: [react()],
  base: "./",
  root: resolve(__dirname, "src/islands", island),
  build: {
    outDir: resolve(__dirname, "../assets", island),
    emptyOutDir: true,
    sourcemap: false,
  },
});
