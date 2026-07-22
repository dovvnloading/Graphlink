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
const ISLANDS = ["composer", "token-counter", "notification", "command-palette", "settings", "about", "help", "document-viewer", "chat-library", "search-overlay", "pin-overlay", "composer-picker", "composer-context", "toolbar", "plugin-picker", "grid-control", "font-control", "drag-speed", "minimap"];

// Qt-removal plan R0 (doc/QT_REMOVAL_PLAN.md): "app" is the single-SPA
// target that consolidates the islands. Unlike islands it is SERVED by the
// Python backend (not inlined into a Qt webview), so it has no single-chunk
// constraint and builds to dist/app instead of ../assets/<island>. The
// island targets stay buildable until the R7 cutover deletes the Qt hosts.
const APP_TARGET = "app";

const island = process.env.GRAPHLINK_ISLAND || "composer";
const isApp = island === APP_TARGET;
if (!isApp && !ISLANDS.includes(island)) {
  throw new Error(
    `Unknown island "${island}" (set via GRAPHLINK_ISLAND). Known islands: ${[APP_TARGET, ...ISLANDS].join(", ")}`,
  );
}

export default defineConfig({
  plugins: [tailwindcss(), react()],
  base: "./",
  root: resolve(__dirname, isApp ? "src/app" : `src/islands/${island}`),
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
    // App-target dev mode talks to a locally running backend; islands never
    // proxy (their transport is QWebChannel, not HTTP).
    proxy: isApp
      ? {
          "/api": "http://127.0.0.1:8765",
          "/ws": { target: "ws://127.0.0.1:8765", ws: true },
        }
      : undefined,
  },
  build: {
    outDir: resolve(__dirname, isApp ? "dist/app" : `../assets/${island}`),
    emptyOutDir: true,
    sourcemap: false,
  },
});
