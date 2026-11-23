/**
 * UI-02 baseline from main (626e10567a82c9aaf9b33e3bcbd75d708463537b).
 * Keep one entry per rule/file, sorted by relative path, and leave a blank
 * line between entries to minimize conflicts with parallel UI migrations.
 * Zero-count files are intentionally absent: an unpinned file must stay at 0.
 *
 * app/styles.css R12 line count is a maximum rather than an exact count,
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




























  { file: "app/styles.css", rule: "R12", count: 243 },

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
