import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Qt-removal plan R7.6a: the SPA under src/app is now the ONLY build target.
//
// This config used to carry a 19-entry ISLANDS registry and a GRAPHLINK_ISLAND
// env switch, because each Qt webview hosted its own fully self-contained
// bundle (own index.html, own outDir under ../assets/<island>) that
// graphlink_composer_web.py's _inline_bundle() inlined into a single offline
// HTML document. R7.6a deleted every island - the SPA replaced all 19 - so the
// switch, the registry, and the per-island outDir have nothing left to select
// between. `npm run dev` and `npm run build` now just build the app.

export default defineConfig({
  plugins: [tailwindcss(), react()],
  base: "./",
  root: resolve(__dirname, "src/app"),
  // The desktop shell's live dev-server mode (GRAPHLINK_FRONTEND_DEV_URL)
  // allowlists ONE exact origin. These three settings keep the real served
  // origin pinned to that expectation: the literal IP avoids localhost's
  // IPv4/IPv6 resolution split, and strictPort makes a taken port fail loud at
  // startup instead of silently drifting to 5174+ where the allowlist would
  // block everything.
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
    },
  },
  build: {
    outDir: resolve(__dirname, "dist/app"),
    emptyOutDir: true,
    sourcemap: false,
  },
});
