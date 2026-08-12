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
