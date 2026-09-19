import { describe, expect, it } from "vitest";
import {
  SCENE_NODE_ROW_WIRE_DEFAULTS,
  hydrateSceneNodeRow,
  validateSceneState,
  type SceneNodeRow,
} from "./generated/scene-state";

// Scene node rows cross the wire sparse: the backend (backend/domain/
// node_wire.py) leaves out every field at its contract default, and the
// generated hydrateSceneNodeRow restores them - inside validateSceneState for
// snapshots, and in SceneStore.applyScenePatch for upserts. Everything past
// those two points reads full rows, exactly as before the wire went sparse.

const IDENTITY = { id: "n7", x: 10, y: 20, title: "Seven", kind: "chat" } as const;

function scene(nodes: unknown[]) {
  return {
    schemaVersion: 2,
    minCompatibleSchemaVersion: 2,
    revision: 1,
    nodes,
    edges: [],
    pins: [],
    snapToGrid: false,
    fadeConnectionsEnabled: false,
    orthogonalRouting: false,
    smartGuides: true,
    hasSavedChat: false,
    dragFactor: 0.5,
    fontFamily: "Segoe UI",
    fontSizePt: 10,
    fontColor: "#ffffff",
    canUndo: false,
    canRedo: false,
    undoLabel: "",
    redoLabel: "",
  };
}

describe("hydrateSceneNodeRow", () => {
  it("restores every omitted field to its contract default", () => {
    const row = hydrateSceneNodeRow({ ...IDENTITY }) as Record<string, unknown>;
    for (const [key, fallback] of SCENE_NODE_ROW_WIRE_DEFAULTS) {
      expect(row[key], key).toEqual(fallback);
    }
    expect(row).toMatchObject(IDENTITY);
  });

  it("keeps every field the sender did send", () => {
    const row = hydrateSceneNodeRow({
      ...IDENTITY,
      branchStatus: "accepted",
      isUser: true,
      history: [{ role: "user", content: "hi", incomplete: false }],
    }) as SceneNodeRow;
    expect(row.branchStatus).toBe("accepted");
    expect(row.isUser).toBe(true);
    expect(row.history).toEqual([{ role: "user", content: "hi", incomplete: false }]);
    expect(row.chartWidth).toBe(480);
  });

  it("leaves an explicit null alone - only an ABSENT field is restored", () => {
    // A null the sender actually sent is data; whether it is legal is the
    // validator's call, not something hydration may paper over.
    const row = hydrateSceneNodeRow({ ...IDENTITY, branchStatus: null }) as Record<string, unknown>;
    expect(row.branchStatus).toBeNull();
  });

  it("returns the same object when nothing is missing", () => {
    const whole = hydrateSceneNodeRow({ ...IDENTITY });
    expect(hydrateSceneNodeRow(whole)).toBe(whole);
  });

  it("never shares a restored container between rows", () => {
    const a = hydrateSceneNodeRow({ ...IDENTITY }) as SceneNodeRow;
    const b = hydrateSceneNodeRow({ ...IDENTITY, id: "n8" }) as SceneNodeRow;
    expect(a.history).not.toBe(b.history);
    expect(a.chartData).not.toBe(b.chartData);
  });

  it("passes a non-object through untouched for the validator to reject", () => {
    expect(hydrateSceneNodeRow(null)).toBeNull();
    expect(hydrateSceneNodeRow("row")).toBe("row");
  });
});

describe("validateSceneState on a sparse snapshot", () => {
  it("accepts rows carrying only their identity and hands back restored rows", () => {
    const result = validateSceneState(scene([{ ...IDENTITY }, { ...IDENTITY, id: "n8", kind: "note", isSystemPrompt: true }]));
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    const [chat, note] = result.value.nodes;
    expect(chat.content).toBe("");
    expect(chat.pendingRequestId).toBeNull();
    expect(chat.isLocked).toBe(true);
    expect(note.isSystemPrompt).toBe(true);
    expect(note.gitlinkScopeMode).toBe("selected");
  });

  it("still rejects a row missing a field that has no default", () => {
    const { id: _id, ...noId } = IDENTITY;
    expect(validateSceneState(scene([noId])).ok).toBe(false);
  });

  it("still rejects a sent field of the wrong type", () => {
    expect(validateSceneState(scene([{ ...IDENTITY, isUser: "yes" }])).ok).toBe(false);
  });

  it("returns the very same payload when every row is already whole", () => {
    const whole = scene([hydrateSceneNodeRow({ ...IDENTITY })]);
    const result = validateSceneState(whole);
    expect(result.ok && result.value).toBe(whole);
  });
});
