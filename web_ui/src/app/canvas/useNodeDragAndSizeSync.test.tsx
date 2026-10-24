/**
 * useNodeDragAndSizeSync's drag-gesture bookkeeping, driven through the
 * hook's own two halves in production order: the change middleware React
 * Flow runs INSIDE its own update, then onNodesChange with the middleware's
 * output. Same capture-the-middleware posture as SceneCanvas.virtualization.
 * test.tsx (see that file's module doc for why a real <ReactFlow> mount
 * cannot be driven here), but at the hook level so the gesture state the
 * hook returns - draggingRef, dragActive, smartGuideLines - can be asserted
 * directly rather than inferred from <ReactFlow> props.
 */
import { ReactFlowProvider, useReactFlow, type NodeChange } from "@xyflow/react";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

let capturedMiddleware: ((changes: NodeChange[]) => NodeChange[]) | null = null;

vi.mock("@xyflow/react", async (importOriginal) => {
  const original = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...original,
    experimental_useOnNodesChangeMiddleware: (fn: (changes: NodeChange[]) => NodeChange[]) => {
      capturedMiddleware = fn;
    },
  };
});

import { useNodeDragAndSizeSync } from "./useNodeDragAndSizeSync";
import { initialSceneState, type SceneStore } from "./sceneStore";
import type { SceneFlowNode } from "./SceneCanvas";
import type { SceneState } from "../../lib/bridge-core/generated/scene-state";

function flowNode(
  id: string,
  type: string,
  x: number,
  y: number,
  size: number,
  data: Record<string, unknown> = {},
): SceneFlowNode {
  // width/height on the flow node itself is what buildDragSizeCache reads
  // first (flowNodeOwnSize), so the smart-guide cache resolves every node
  // here without a DOM to measure.
  return { id, type, position: { x, y }, width: size, height: size, data } as unknown as SceneFlowNode;
}

function mountHook() {
  const store = { moveNodes: vi.fn(), reportNodeSizes: vi.fn() } as unknown as SceneStore;
  const scene: SceneState = { ...initialSceneState, smartGuides: true };
  capturedMiddleware = null;
  const { result } = renderHook(() => useNodeDragAndSizeSync(scene, useReactFlow(), store), {
    wrapper: ReactFlowProvider,
  });
  // A container at the origin carrying two members, plus one unrelated
  // node whose top edge (y=0) the container's top edge aligns with - the
  // smart-guide candidate that makes each drag frame publish a guide line.
  result.current.nodesRef.current = [
    flowNode("g", "container", 0, 0, 200, { itemIds: ["m1", "m2"] }),
    flowNode("m1", "chat", 20, 20, 50),
    flowNode("m2", "chat", 100, 100, 50),
    flowNode("anchor", "chat", 500, 0, 100),
  ];
  // Production's real sequence: React Flow runs the registered middleware
  // inside its own update first, then hands the CORRECTED batch to
  // onNodesChange.
  const frame = (changes: Array<Partial<NodeChange> & { id: string }>) =>
    act(() => {
      const raw = changes.map((change) => ({ type: "position", ...change }) as NodeChange);
      result.current.onNodesChange(capturedMiddleware!(raw) as NodeChange<SceneFlowNode>[]);
    });
  return { result, store, frame };
}

describe("useNodeDragAndSizeSync group drag (container/frame carrying members)", () => {
  it("ends the gesture when a group with members is released - dragging, culling suspension and guides all clear", () => {
    const { result, frame } = mountHook();

    frame([{ id: "g", dragging: true, position: { x: 10, y: 3 } }]);
    // Mid-drag: the gesture is live and the top-edge alignment published a
    // guide - the precondition the release below must undo.
    expect(result.current.draggingRef.current).toBe(true);
    expect(result.current.dragActive).toBe(true);
    expect(result.current.smartGuideLines).toHaveLength(1);

    frame([{ id: "g", dragging: true, position: { x: 20, y: 3 } }]);
    // React Flow's own drag-STOP change: no dragging flag.
    frame([{ id: "g", dragging: false, position: { x: 20, y: 3 } }]);

    expect(result.current.draggingRef.current).toBe(false);
    expect(result.current.dragActive).toBe(false);
    expect(result.current.smartGuideLines).toEqual([]);
    // The carried members settle as not-dragging too, rather than keeping
    // React Flow's per-node dragging flag set after the release.
    const byId = new Map(result.current.nodesRef.current.map((n) => [n.id, n]));
    expect(byId.get("m1")!.dragging).toBe(false);
    expect(byId.get("m2")!.dragging).toBe(false);
  });

  it("still commits the group and every carried member's settled position in one batch on release", () => {
    const { store, frame } = mountHook();

    frame([{ id: "g", dragging: true, position: { x: 10, y: 3 } }]);
    frame([{ id: "g", dragging: true, position: { x: 20, y: 3 } }]);
    frame([{ id: "g", dragging: false, position: { x: 20, y: 3 } }]);

    // y snaps to the anchor's top edge (0), so the carried delta is (20, 0).
    expect(store.moveNodes).toHaveBeenCalledTimes(1);
    expect(store.moveNodes).toHaveBeenCalledWith([
      { id: "g", x: 20, y: 0 },
      { id: "m1", x: 40, y: 20 },
      { id: "m2", x: 120, y: 100 },
    ]);
  });

  it("ends a multi-select drag of a group plus an unrelated node the same way", () => {
    const { result, store, frame } = mountHook();

    frame([
      { id: "g", dragging: true, position: { x: 10, y: 40 } },
      { id: "anchor", dragging: true, position: { x: 510, y: 40 } },
    ]);
    expect(result.current.draggingRef.current).toBe(true);
    frame([
      { id: "g", dragging: false, position: { x: 10, y: 40 } },
      { id: "anchor", dragging: false, position: { x: 510, y: 40 } },
    ]);

    expect(result.current.draggingRef.current).toBe(false);
    expect(result.current.dragActive).toBe(false);
    expect(result.current.smartGuideLines).toEqual([]);
    expect(store.moveNodes).toHaveBeenCalledTimes(1);
    const committed = (store.moveNodes as ReturnType<typeof vi.fn>).mock.calls[0][0] as Array<{ id: string }>;
    expect(committed.map((intent) => intent.id).sort()).toEqual(["anchor", "g", "m1", "m2"]);
  });
});
