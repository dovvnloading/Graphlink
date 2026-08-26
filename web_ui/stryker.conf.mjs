// ADR-022 stage 22.5: mutation testing scoped to 4 pure modules already
// covered by this ADR's own property-based tests (stage 22.3) - a curated
// slice, not the whole codebase, since mutation cost scales with mutant
// count x test-suite runtime. Run only by .github/workflows/nightly-
// mutation.yml, never per-PR (see that workflow's own comment).
//
// islandState.ts dropped from this list in the tech-debt sweep that retired
// its own parseIslandState() as dead code (see that file's current header) -
// its only remaining content is a type declaration with no executable body
// left for Stryker to mutate.
export default {
  testRunner: "vitest",
  vitest: {
    configFile: "vitest.config.ts",
  },
  mutate: [
    "src/app/chrome/quickSwitcherFuzzy.ts",
    "src/app/canvas/charts/chartScales.ts",
    "src/lib/bridge-core/schemaVersion.ts",
    "src/app/canvas/documentViewSearchHighlight.ts",
  ],
};
