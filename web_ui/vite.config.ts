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
    // ADR-011 stage 11.6: "hidden" writes real .js.map files (so a
    // maintainer can load one into a debugger / symbolicate a stack trace
    // pulled from the logs) without emitting a `//# sourceMappingURL`
    // comment in the shipped .js - the pywebview shell's own devtools never
    // auto-fetch them, and nothing about this desktop app's distribution
    // model treats the source as secret (the dist bundle already ships the
    // equivalent of the source client-side, readable via view-source, same
    // as any other unminified-by-choice web app). Plain `true` would be
    // fine functionally too; "hidden" is strictly better here since it
    // costs nothing and avoids advertising the map to anything that just
    // happens to load the built HTML in a normal browser tab.
    sourcemap: "hidden",
    rollupOptions: {
      output: {
        // ADR-011 stage 11.6: NodeMarkdown.tsx (every chat-bearing node
        // view's shared markdown renderer, itself eagerly rendered - not a
        // React.lazy boundary, so nothing splits it out automatically) is
        // the ONLY thing in the app that imports katex/rehype-katex or
        // highlight.js/rehype-highlight. Both are large (katex's parser +
        // fonts-adjacent JS, highlight.js's grammar table) and otherwise
        // land in the single main chunk just because one eagerly-rendered
        // node view uses markdown - manualChunks forces them into their own
        // chunks instead, so the main chunk's floor isn't set by two
        // dependencies most sessions may only use through node text.
        manualChunks: {
          katex: ["katex", "rehype-katex"],
          "highlight.js": ["highlight.js", "rehype-highlight"],
        },
      },
    },
  },
});
