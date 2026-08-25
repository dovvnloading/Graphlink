// ADR-022 stage 22.5: mutation testing scoped to 5 pure modules already
// covered by this ADR's own property-based tests (stage 22.3) - a curated
// slice, not the whole codebase, since mutation cost scales with mutant
// count x test-suite runtime. Run only by .github/workflows/nightly-
// mutation.yml, never per-PR (see that workflow's own comment).
export default {
  testRunner: "vitest",
  vitest: {
    configFile: "vitest.config.ts",
  },
  mutate: [
    "src/app/chrome/quickSwitcherFuzzy.ts",
    "src/app/canvas/charts/chartScales.ts",
    "src/lib/bridge-core/schemaVersion.ts",
    "src/lib/bridge-core/islandState.ts",
    "src/app/canvas/documentViewSearchHighlight.ts",
  ],
};
