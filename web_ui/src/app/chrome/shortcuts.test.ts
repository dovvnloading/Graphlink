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
    expect(resolveShortcut(key("ArrowUp"))).toBe("navigate-up");
    expect(resolveShortcut(key("ArrowDown"))).toBe("navigate-down");
    expect(resolveShortcut(key("ArrowLeft"))).toBe("navigate-left");
    expect(resolveShortcut(key("ArrowRight"))).toBe("navigate-right");
  });

  it("treats Ctrl+Shift+G as Container - a separate legacy binding from Ctrl+G", () => {
    expect(resolveShortcut(key("g", { shift: true }))).toBe("create-container");
    expect(resolveShortcut(key("G", { shift: true }))).toBe("create-container");
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
  // contract-tests. Mirrored here so a future edit to the set has to be
  // deliberate rather than incidental.
  const GATED: ShortcutId[] = [
    "new-chat",
    "toggle-library",
    "toggle-search",
    "create-frame",
    "create-container",
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

  it("gates exactly the 9 legacy combinations, no more and no fewer", () => {
    const all: ShortcutId[] = [...GATED, ...EXEMPT];
    expect(all.filter(isGatedWhileTyping)).toHaveLength(9);
  });
});
