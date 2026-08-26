import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Separate from vite.config.ts on purpose: that config's root is scoped to
// src/app (the SPA build - Qt-removal plan R7.6a retired the per-island
// GRAPHLINK_ISLAND build switch it used to carry, see its own module doc),
// while vitest runs across the whole workspace (src/app + lib/) from a
// single, fixed root - the two configs' roots have never been the same
// thing, so they stay separate files rather than one shared config.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: false,
    css: false,
    // ADR-015 stage 15.6: e2e/ is Playwright's, not vitest's. Both use the
    // `*.spec.ts` suffix, so vitest's default include glob picks the
    // Playwright specs up and they fail with "Playwright Test did not
    // expect test() to be called here" - the two runners' test() functions
    // are different objects and neither tolerates the other's. Excluding
    // the directory (rather than renaming the specs to some other suffix)
    // keeps the Playwright convention intact and matches how playwright.
    // config.ts already scopes itself with testDir: "./e2e".
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"],
  },
});
