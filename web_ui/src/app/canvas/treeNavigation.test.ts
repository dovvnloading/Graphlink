import { describe, expect, it } from "vitest";
import {
  resolveTreeNavigationTarget,
  type TreeNavigationScene,
} from "./treeNavigation";

// Direct coverage of the legacy _navigate_up/_down/_left/_right port - each
// documented rule (navigable-kind filter, x-position sibling ordering,
// leftmost-child, parent requirement, pure no-op boundaries) gets its own
// named test so a regression in any one fails loudly.

/**
 *        chat-root
 *      /     |      \
 *  chat-b  chat-a   chat-c        (declared out of order on purpose;
 *  x=200   x=100    x=300          visual order is a, b, c)
 */
function branchScene(): TreeNavigationScene {
  return {
    nodes: [
      { id: "root", kind: "chat", x: 150 },
      { id: "b", kind: "chat", x: 200 },
      { id: "a", kind: "chat", x: 100 },
      { id: "c", kind: "chat", x: 300 },
    ],
    edges: [
      { source: "root", target: "b" },
      { source: "root", target: "a" },
      { source: "root", target: "c" },
    ],
  };
}

describe("resolveTreeNavigationTarget", () => {
  it("no-ops when nothing is selected", () => {
    expect(resolveTreeNavigationTarget(branchScene(), null, "down")).toBeNull();
  });

  it("no-ops when the selected node is not a navigable kind", () => {
    const scene: TreeNavigationScene = {
      nodes: [
        { id: "chat-1", kind: "chat", x: 0 },
        { id: "code-1", kind: "code", x: 0 },
      ],
      edges: [{ source: "chat-1", target: "code-1" }],
    };
    // Standing on the code node: legacy's _get_single_selected_node filter
    // rejects it outright, so even "up" (which has a real parent) no-ops.
    expect(resolveTreeNavigationTarget(scene, "code-1", "up")).toBeNull();
  });

  it("moves up to the parent", () => {
    expect(resolveTreeNavigationTarget(branchScene(), "a", "up")).toBe("root");
  });

  it("no-ops going up from a root (no parent)", () => {
    expect(resolveTreeNavigationTarget(branchScene(), "root", "up")).toBeNull();
  });

  it("moves down to the LEFTMOST child by x, not the first-created one", () => {
    // "b" is the first edge declared, but "a" sits further left.
    expect(resolveTreeNavigationTarget(branchScene(), "root", "down")).toBe("a");
  });

  it("no-ops going down from a leaf (no children)", () => {
    expect(resolveTreeNavigationTarget(branchScene(), "a", "down")).toBeNull();
  });

  it("moves left/right through siblings in x order", () => {
    const scene = branchScene();
    expect(resolveTreeNavigationTarget(scene, "a", "right")).toBe("b");
    expect(resolveTreeNavigationTarget(scene, "b", "right")).toBe("c");
    expect(resolveTreeNavigationTarget(scene, "c", "left")).toBe("b");
    expect(resolveTreeNavigationTarget(scene, "b", "left")).toBe("a");
  });

  it("no-ops at both sibling boundaries - never wraps around", () => {
    const scene = branchScene();
    expect(resolveTreeNavigationTarget(scene, "a", "left")).toBeNull();
    expect(resolveTreeNavigationTarget(scene, "c", "right")).toBeNull();
  });

  it("no-ops sideways from a parentless root - siblings need a parent", () => {
    const scene = branchScene();
    expect(resolveTreeNavigationTarget(scene, "root", "left")).toBeNull();
    expect(resolveTreeNavigationTarget(scene, "root", "right")).toBeNull();
  });

  it("skips non-navigable children entirely when moving down", () => {
    // A code node sits furthest left, but was never in legacy's children
    // graph - so "down" must land on the leftmost NAVIGABLE child instead.
    const scene: TreeNavigationScene = {
      nodes: [
        { id: "root", kind: "chat", x: 0 },
        { id: "code-1", kind: "code", x: 10 },
        { id: "chat-1", kind: "chat", x: 50 },
      ],
      edges: [
        { source: "root", target: "code-1" },
        { source: "root", target: "chat-1" },
      ],
    };
    expect(resolveTreeNavigationTarget(scene, "root", "down")).toBe("chat-1");
  });

  it("excludes non-navigable siblings from the left/right ordering", () => {
    const scene: TreeNavigationScene = {
      nodes: [
        { id: "root", kind: "chat", x: 0 },
        { id: "chat-1", kind: "chat", x: 100 },
        { id: "img-1", kind: "image", x: 200 },
        { id: "chat-2", kind: "chat", x: 300 },
      ],
      edges: [
        { source: "root", target: "chat-1" },
        { source: "root", target: "img-1" },
        { source: "root", target: "chat-2" },
      ],
    };
    // The image node sits between them visually but must be stepped over.
    expect(resolveTreeNavigationTarget(scene, "chat-1", "right")).toBe("chat-2");
  });

  it("treats conversation and html nodes as navigable, like legacy", () => {
    const scene: TreeNavigationScene = {
      nodes: [
        { id: "root", kind: "chat", x: 0 },
        { id: "conv", kind: "conversation", x: 10 },
        { id: "html", kind: "html", x: 20 },
      ],
      edges: [
        { source: "root", target: "conv" },
        { source: "conv", target: "html" },
      ],
    };
    expect(resolveTreeNavigationTarget(scene, "root", "down")).toBe("conv");
    expect(resolveTreeNavigationTarget(scene, "conv", "down")).toBe("html");
    expect(resolveTreeNavigationTarget(scene, "html", "up")).toBe("conv");
  });

  it("falls back to declaration order for siblings tied on x (stable sort)", () => {
    const scene: TreeNavigationScene = {
      nodes: [
        { id: "root", kind: "chat", x: 0 },
        { id: "first", kind: "chat", x: 50 },
        { id: "second", kind: "chat", x: 50 },
      ],
      edges: [
        { source: "root", target: "first" },
        { source: "root", target: "second" },
      ],
    };
    expect(resolveTreeNavigationTarget(scene, "root", "down")).toBe("first");
    expect(resolveTreeNavigationTarget(scene, "first", "right")).toBe("second");
  });

  it("no-ops for a selected id that isn't in the scene at all", () => {
    expect(resolveTreeNavigationTarget(branchScene(), "ghost", "up")).toBeNull();
  });
});
