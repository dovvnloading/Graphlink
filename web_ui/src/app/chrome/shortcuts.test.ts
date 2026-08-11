import { describe, expect, it } from "vitest";
import { isGatedWhileTyping, resolveShortcut, type ShortcutId } from "./shortcuts";

function key(k: string, mods: Partial<{ ctrl: boolean; meta: boolean; shift: boolean; alt: boolean }> = {}) {
  return {
    key: k,
    ctrlKey: mods.ctrl ?? true,
    metaKey: mods.meta ?? false,
    shiftKey: mods.shift ?? false,
    altKey: mods.alt ?? false,
  };
}

describe("resolveShortcut", () => {
  it("maps each legacy binding to its shortcut id", () => {
    expect(resolveShortcut(key("t"))).toBe("new-chat");
    expect(resolveShortcut(key("l"))).toBe("toggle-library");
    expect(resolveShortcut(key("s"))).toBe("save-chat");
    expect(resolveShortcut(key("k"))).toBe("toggle-palette");
    expect(resolveShortcut(key("f"))).toBe("toggle-search");
    expect(resolveShortcut(key("g"))).toBe("create-frame");
    expect(resolveShortcut(key("p"))).toBe("toggle-quick-switcher");
    expect(resolveShortcut(key("ArrowUp"))).toBe("navigate-up");
    expect(resolveShortcut(key("ArrowDown"))).toBe("navigate-down");
    expect(resolveShortcut(key("ArrowLeft"))).toBe("navigate-left");
    expect(resolveShortcut(key("ArrowRight"))).toBe("navigate-right");
  });

  it("treats Ctrl+Shift+G as Container - a separate legacy binding from Ctrl+G", () => {
    expect(resolveShortcut(key("g", { shift: true }))).toBe("create-container");
    expect(resolveShortcut(key("G", { shift: true }))).toBe("create-container");
  });

  // ADR-002 Workstream 1 ("Compare Branches") - a genuinely new binding, not
  // a legacy port.
  it("treats Ctrl+Shift+C as Compare Branches", () => {
    expect(resolveShortcut(key("c", { shift: true }))).toBe("compare-branches");
    expect(resolveShortcut(key("C", { shift: true }))).toBe("compare-branches");
  });

  it("does NOT bind bare Ctrl+C - it must stay the browser's native copy shortcut", () => {
    expect(resolveShortcut(key("c"))).toBeNull();
  });

  // ADR-002 Workstream 1 ("Synthesize Branches") - a genuinely new binding,
  // not a legacy port. "M" (Merge/coMbine) since "S" is already save-chat.
  it("treats Ctrl+Shift+M as Synthesize Branches", () => {
    expect(resolveShortcut(key("m", { shift: true }))).toBe("synthesize-branches");
    expect(resolveShortcut(key("M", { shift: true }))).toBe("synthesize-branches");
  });

  it("does NOT bind bare Ctrl+M", () => {
    expect(resolveShortcut(key("m"))).toBeNull();
  });

  it("is case-insensitive, since Shift/CapsLock change event.key's case", () => {
    expect(resolveShortcut(key("T"))).toBe("new-chat");
  });

  it("accepts Cmd as well as Ctrl", () => {
    expect(resolveShortcut(key("t", { ctrl: false, meta: true }))).toBe("new-chat");
  });

  it("ignores keys with no Ctrl/Cmd modifier", () => {
    expect(resolveShortcut(key("t", { ctrl: false }))).toBeNull();
    expect(resolveShortcut(key("ArrowUp", { ctrl: false }))).toBeNull();
  });

  it("ignores Alt combinations so AltGr-produced characters still type", () => {
    expect(resolveShortcut(key("t", { alt: true }))).toBeNull();
    expect(resolveShortcut(key("g", { alt: true, shift: true }))).toBeNull();
  });

  it("ignores Shift on bindings legacy did not define with Shift", () => {
    expect(resolveShortcut(key("t", { shift: true }))).toBeNull();
    expect(resolveShortcut(key("s", { shift: true }))).toBeNull();
    expect(resolveShortcut(key("ArrowUp", { shift: true }))).toBeNull();
  });

  it("returns null for unbound keys", () => {
    expect(resolveShortcut(key("q"))).toBeNull();
    expect(resolveShortcut(key("Enter"))).toBeNull();
  });
});

describe("isGatedWhileTyping", () => {
  // The exact membership of legacy's GATED_SHORTCUTS
  // (graphlink_web_island_host.py:735-745), which legacy itself
  // contract-tests, PLUS "compare-branches"/"synthesize-branches" (ADR-002
  // Workstream 1 - new shortcuts, not legacy ports, gated for the same
  // reason as their closest sibling create-frame/create-container - see
  // shortcuts.ts's own comment) PLUS "toggle-quick-switcher" (ADR-020 stage
  // 20.5 - a new shortcut, gated for the same "jump to a different
  // document" reason as toggle-library - see shortcuts.ts's own comment).
  // Mirrored here so a future edit to the set has to be deliberate rather
  // than incidental.
  const GATED: ShortcutId[] = [
    "new-chat",
    "toggle-library",
    "toggle-search",
    "toggle-quick-switcher",
    "create-frame",
    "create-container",
    "compare-branches",
    "synthesize-branches",
    "navigate-up",
    "navigate-down",
    "navigate-left",
    "navigate-right",
  ];
  const EXEMPT: ShortcutId[] = ["save-chat", "toggle-palette"];

  it.each(GATED)("suppresses %s while a text field has focus", (id) => {
    expect(isGatedWhileTyping(id)).toBe(true);
  });

  it.each(EXEMPT)("lets %s through while typing - a documented legacy exemption", (id) => {
    expect(isGatedWhileTyping(id)).toBe(false);
  });

  it("gates exactly the 9 legacy combinations plus compare-branches/synthesize-branches/toggle-quick-switcher (12 total), no more and no fewer", () => {
    const all: ShortcutId[] = [...GATED, ...EXEMPT];
    expect(all.filter(isGatedWhileTyping)).toHaveLength(12);
  });
});

// ADR-010 stage 10.2: undo/redo bindings.
describe("undo/redo shortcuts (ADR-010 stage 10.2)", () => {
  it("maps Ctrl+Z to undo and Ctrl+Shift+Z to redo", () => {
    expect(resolveShortcut(key("z"))).toBe("undo");
    expect(resolveShortcut(key("z", { shift: true }))).toBe("redo");
  });

  it("also maps Ctrl+Y to redo, the Windows convention", () => {
    // The app runs on Windows, where Ctrl+Y is the platform norm, while
    // Ctrl+Shift+Z is the cross-platform one. Supporting only one would
    // feel broken to half the muscle memory in the room.
    expect(resolveShortcut(key("y"))).toBe("redo");
  });

  it("gates both while typing so native text undo is never shadowed", () => {
    // Deliberately unlike Save's exemption: Ctrl+Z inside a text field must
    // stay the browser's own undo. The ADR names that boundary explicitly.
    expect(isGatedWhileTyping("undo")).toBe(true);
    expect(isGatedWhileTyping("redo")).toBe(true);
  });
});
