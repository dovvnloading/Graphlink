import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Separate from vite.config.ts on purpose: that config's root/outDir are
// dynamically selected per-island (GRAPHLINK_ISLAND) for production builds,
// which is meaningless for tests - vitest runs across the whole workspace
// (every island + lib/) from a single, fixed root.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: false,
    css: false,
  },
});
