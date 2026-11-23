/**
 * UI-02 baseline from main (626e10567a82c9aaf9b33e3bcbd75d708463537b).
 * Keep one entry per rule/file, sorted by relative path, and leave a blank
 * line between entries to minimize conflicts with parallel UI migrations.
 * Zero-count files are intentionally absent: an unpinned file must stay at 0.
 *
 * app/styles.css pins for R9–R12 and R14 are maxima rather than exact counts,
 * to avoid blocking unrelated ongoing CSS cleanups. All other pins are exact.
 * See docs/DESIGN_SYSTEM.md for the replacement component conventions.
 */
export type RatchetRule =
  | "R1" | "R2" | "R3" | "R4" | "R5" | "R6" | "R7"
  | "R8" | "R9" | "R10" | "R11" | "R12" | "R13" | "R14";

export const UI_RATCHET_INVENTORY: ReadonlyArray<{
  readonly file: string;
  readonly rule: RatchetRule;
  readonly count: number;
}> = [
  { file: "app/canvas/ChatCitationChips.css", rule: "R9", count: 5 },

  { file: "app/canvas/ChatCitationChips.css", rule: "R10", count: 1 },

  { file: "app/canvas/ChatCitationChips.css", rule: "R14", count: 13 },

  { file: "app/canvas/ChatRerunDialog.css", rule: "R9", count: 2 },

  { file: "app/canvas/ChatRerunDialog.css", rule: "R10", count: 4 },

  { file: "app/canvas/CodeReviewNodeView.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/GitlinkNodeView.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/NodeCards.css", rule: "R9", count: 12 },

  { file: "app/canvas/NodeCards.css", rule: "R10", count: 19 },

  { file: "app/canvas/NodeCards.css", rule: "R14", count: 71 },

  { file: "app/canvas/SceneCanvas.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/SceneCanvas.tsx", rule: "R8", count: 2 },

  { file: "app/canvas/SceneCanvas.tsx", rule: "R13", count: 1 },

  { file: "app/canvas/ScienceArrayInput.tsx", rule: "R1", count: 1 },

  { file: "app/canvas/ScienceArrayInput.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/ScienceColumnBinding.tsx", rule: "R4", count: 3 },

  { file: "app/canvas/ScienceColumnBinding.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/ScienceTableSource.tsx", rule: "R4", count: 1 },

  { file: "app/canvas/ScientificDataTable.tsx", rule: "R5", count: 1 },

  { file: "app/canvas/ScientificNodeView.tsx", rule: "R2", count: 1 },

  { file: "app/canvas/ScientificWorkspace.tsx", rule: "R8", count: 1 },

  { file: "app/canvas/workspace.css", rule: "R11", count: 2 },

  { file: "app/chrome/Composer.tsx", rule: "R1", count: 3 },

  { file: "app/chrome/Composer.tsx", rule: "R2", count: 1 },

  { file: "app/chrome/Composer.tsx", rule: "R8", count: 9 },

  { file: "app/chrome/KnowledgeSettingsPage.tsx", rule: "R13", count: 1 },

  { file: "app/chrome/ModelComparisonPicker.tsx", rule: "R1", count: 1 },

  { file: "app/chrome/ModelPicker.tsx", rule: "R1", count: 1 },

  { file: "app/chrome/ModelPicker.tsx", rule: "R8", count: 1 },

  { file: "app/chrome/OrganizationDialog.tsx", rule: "R2", count: 1 },

  { file: "app/chrome/PluginPicker.tsx", rule: "R1", count: 1 },

  { file: "app/context/ContextInspector.css", rule: "R9", count: 1 },

  { file: "app/context/ContextInspector.css", rule: "R10", count: 3 },

  { file: "app/context/ContextInspector.css", rule: "R14", count: 13 },

  { file: "app/context/ContextInspectorLauncher.tsx", rule: "R2", count: 1 },

  { file: "app/context/ContextInspectorLauncher.tsx", rule: "R8", count: 1 },

  { file: "app/styles.css", rule: "R9", count: 259 },

  { file: "app/styles.css", rule: "R10", count: 128 },

  { file: "app/styles.css", rule: "R11", count: 2 },

  { file: "app/styles.css", rule: "R12", count: 6662 },

  { file: "app/styles.css", rule: "R14", count: 564 },
];

/** Each historical R1 exception must remain exact, too; it is not a blanket
 * file exclusion. These are the only non-icon uses of icon-like characters.
 * The diff signs are text in a comparison key, not replacement iconography.
 */
export const R1_ALLOWLIST = [
  {
    file: "app/canvas/ChatVersionCompareDialog.tsx",
    needle: "+",
    count: 1,
    reason: "Diff legend's insertion + is semantic diff notation, not a UI icon.",
  },

  {
    file: "app/canvas/ChatVersionCompareDialog.tsx",
    needle: "−",
    count: 1,
    reason: "Diff legend's deletion minus is semantic diff notation, not a UI icon.",
  },

  {
    file: "app/canvas/HarnessNodeView.tsx",
    needle: "×",
    count: 1,
    reason: "Compaction multiplier N× is a quantity suffix, not a close icon.",
  },

  {
    file: "app/canvas/scientificWorkbench.ts",
    needle: "×",
    count: 2,
    reason: "Scientific notation uses the multiplication sign in mantissa × 10^n.",
  },
] as const;
