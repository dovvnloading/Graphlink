import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const __dirname = dirname(fileURLToPath(import.meta.url));

// One entry per island. Each island builds as its own fully self-contained
// bundle (own index.html, own outDir) - not a shared-chunk multi-page app -
// since graphlink_composer_web.py's _inline_bundle() inlines exactly one
// CSS file + one JS module per island into a single offline HTML document.
// Registering an island here does not build it; GRAPHLINK_ISLAND below
// selects which one this invocation builds.
const ISLANDS = ["composer", "token-counter", "notification", "command-palette", "settings", "about", "help", "document-viewer", "chat-library", "search-overlay", "pin-overlay", "composer-picker", "composer-context", "toolbar"];

const island = process.env.GRAPHLINK_ISLAND || "composer";
if (!ISLANDS.includes(island)) {
  throw new Error(
    `Unknown island "${island}" (set via GRAPHLINK_ISLAND). Known islands: ${ISLANDS.join(", ")}`,
  );
}

export default defineConfig({
  plugins: [tailwindcss(), react()],
  base: "./",
  root: resolve(__dirname, "src/islands", island),
  // The desktop app's live dev-server mode (GRAPHLINK_FRONTEND_DEV_URL)
  // allowlists ONE exact origin in its WebEngine request interceptor. These
  // three settings keep the real served origin pinned to that expectation:
  // the literal IP avoids localhost's IPv4/IPv6 resolution split, and
  // strictPort makes a taken port fail loud at startup instead of silently
  // drifting to 5174+ where the interceptor would block everything.
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: resolve(__dirname, "../assets", island),
    emptyOutDir: true,
    sourcemap: false,
  },
});
