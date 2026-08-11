/**
 * Global keyboard shortcuts (Qt-removal plan R7.5c) - the SPA successor to
 * the QShortcut block at graphlink_window.py:307-318 plus the
 * AcceleratorForwardingFilter focus arbitration at
 * graphlink_web_island_host.py:692-755.
 *
 * Key MATCHING and the typing-suppression RULE live here as pure functions so
 * both are unit-testable without mounting the app; the dispatch half (which
 * needs the scene store, overlay context and React Flow instance) stays in
 * App.tsx's GlobalShortcuts. Same split posture as smartGuides.ts /
 * treeNavigation.ts.
 *
 * Modifier note: legacy compared raw Qt ControlModifier and shipped
 * Windows-only (no Cmd handling anywhere in that codebase). This keeps the
 * SPA's pre-existing ctrl-or-meta matching so the bindings also work on a
 * Mac keyboard - a deliberate superset of legacy, not a divergence in
 * behavior on the platform legacy actually targeted.
 */

export type ShortcutId =
  | "new-chat"
  | "toggle-library"
  | "save-chat"
  | "create-frame"
  | "create-container"
  | "compare-branches"
  | "synthesize-branches"
  | "toggle-palette"
  | "toggle-search"
  | "toggle-quick-switcher"
  | "navigate-up"
  | "navigate-down"
  | "navigate-left"
  | "navigate-right"
  | "undo"
  | "redo";

/**
 * The shortcuts legacy SUPPRESSES while a text input has focus - ported
 * verbatim from AcceleratorForwardingFilter's GATED_SHORTCUTS
 * (graphlink_web_island_host.py:735-745), whose exact membership is itself
 * contract-tested in graphlink_app/tests/test_keyboard_arbitration.py.
 *
 * Save and the command palette are deliberately ABSENT: legacy documents both
 * as intentional exemptions - Ctrl+S is non-destructive and reflexively
 * expected mid-sentence, and Ctrl+K is the palette's own summon key (its
 * handler was made idempotent specifically so the exemption is safe).
 *
 * "compare-branches" and "synthesize-branches" are genuinely NEW shortcuts
 * (ADR-002 Workstream 1, not legacy ports) but gated here for the exact
 * same reason as their closest sibling create-frame/create-container: a
 * canvas-wide, selection-driven action that should never fire mid-sentence
 * while typing in the composer or a node's own editable field -
 * "synthesize-branches" doubly so, since its effect is staging a
 * selection that then hijacks the very next Send.
 */
const GATED_WHILE_TYPING = new Set<ShortcutId>([
  "new-chat",
  "toggle-library",
  "toggle-search",
  // ADR-020 stage 20.5: the quick switcher is a "jump to a different
  // document" surface, same posture as toggle-library right above it, not
  // Ctrl+K's own exempt "summon key, idempotent handler" posture (see this
  // file's own module doc for why Save/palette are the deliberate
  // exceptions) - Ctrl+P mid-sentence in the composer or a node's inline
  // editor must stay a plain "p", not hijack the field to open a dialog.
  "toggle-quick-switcher",
  "create-frame",
  "create-container",
  "compare-branches",
  "synthesize-branches",
  "navigate-up",
  "navigate-down",
  "navigate-left",
  "navigate-right",
  // ADR-010 stage 10.2: undo/redo are GATED, deliberately unlike Save's
  // exemption above. Ctrl+Z inside a text field must stay the browser's own
  // native text undo - shadowing it with canvas-structural undo would be a
  // regression for anyone mid-sentence, and the ADR names that boundary
  // explicitly ("text edits inside a node keep native browser undo; canvas-
  // structural undo is the command stack").
  "undo",
  "redo",
]);

export function isGatedWhileTyping(id: ShortcutId): boolean {
  return GATED_WHILE_TYPING.has(id);
}

/** Just the modifier/key shape of a keyboard event - so tests can pass plain
 * objects instead of constructing real KeyboardEvents. */
export interface ShortcutKeyEvent {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

const ARROW_DIRECTIONS: Record<string, ShortcutId> = {
  ArrowUp: "navigate-up",
  ArrowDown: "navigate-down",
  ArrowLeft: "navigate-left",
  ArrowRight: "navigate-right",
};

/**
 * Which shortcut (if any) this key event means. Returns null for anything
 * unbound, so the caller leaves the event completely alone.
 *
 * Alt is required to be UP: legacy matched an exact modifier mask, and
 * letting Ctrl+Alt through would also swallow AltGr combinations that
 * produce real characters on several keyboard layouts.
 */
export function resolveShortcut(event: ShortcutKeyEvent): ShortcutId | null {
  if (!(event.ctrlKey || event.metaKey)) return null;
  if (event.altKey) return null;

  const arrow = ARROW_DIRECTIONS[event.key];
  if (arrow) return event.shiftKey ? null : arrow;

  switch (event.key.toLowerCase()) {
    case "t":
      return event.shiftKey ? null : "new-chat";
    case "l":
      return event.shiftKey ? null : "toggle-library";
    case "s":
      return event.shiftKey ? null : "save-chat";
    case "k":
      return event.shiftKey ? null : "toggle-palette";
    case "f":
      return event.shiftKey ? null : "toggle-search";
    // ADR-020 stage 20.5: Ctrl+P is a new binding (no legacy key to match,
    // same posture as compare-branches/synthesize-branches above) - the
    // conventional "quick open"/"go to file" key VS Code and Sublime both
    // use, which is exactly what this surface is for a graph. The browser's
    // own native Ctrl+P (print) is already suppressed the same way every
    // other ctrl-matched binding here is - see onKeyDown's own
    // event.preventDefault() in App.tsx's GlobalShortcuts.
    case "p":
      return event.shiftKey ? null : "toggle-quick-switcher";
    // Ctrl+G -> Frame, Ctrl+Shift+G -> Container: two distinct legacy
    // bindings (graphlink_window.py:312-313), not one with a modifier.
    case "g":
      return event.shiftKey ? "create-container" : "create-frame";
    // ADR-002 Workstream 1: Ctrl+Shift+C only (never bare Ctrl+C, which
    // must stay the browser's native copy shortcut) - "compare-branches"
    // is a new binding, not a legacy port, so there's no existing key to
    // match.
    case "c":
      return event.shiftKey ? "compare-branches" : null;
    // ADR-002 Workstream 1 ("Synthesize Branches"): Ctrl+Shift+M only, same
    // "new binding, no legacy key to match" posture as compare-branches
    // above. "M" for Merge/coMbine - "S" (the mnemonic match for Synthesize)
    // is already save-chat's key.
    case "m":
      return event.shiftKey ? "synthesize-branches" : null;
    // ADR-010 stage 10.2: Ctrl+Z / Ctrl+Shift+Z, plus Ctrl+Y as the second
    // redo binding Windows users reach for. Both redo spellings are wired
    // because the app runs on Windows, where Ctrl+Y is the platform norm
    // and Ctrl+Shift+Z is the cross-platform one - supporting only one
    // would feel broken to half the muscle memory in the room.
    case "z":
      return event.shiftKey ? "redo" : "undo";
    case "y":
      return event.shiftKey ? null : "redo";
    default:
      return null;
  }
}
