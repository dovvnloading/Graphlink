import { describe, expect, it, vi } from "vitest";
import { SceneStore, initialSceneState, scaleDragPosition } from "./sceneStore";
import type { ScenePatch, WsTransport } from "../../lib/ws/transport";

type StateListener = (payload: Record<string, unknown>) => void;
type PatchListener = (patch: ScenePatch) => void;

function makeFakeTransport() {
  const listeners = new Map<string, StateListener>();
  const patchListeners = new Map<string, PatchListener>();
  const resubscribes: string[] = [];
  const intents: Array<{ topic: string; intent: string; args: unknown[] }> = [];
  const requests: Array<{ topic: string; intent: string; args: unknown[] }> = [];
  // A queue rather than mockResolvedValueOnce: the latter REPLACES the
  // implementation for that call (skipping the requests.push below
  // entirely), which would make every request-based intent look
  // unsent. Shifting a pre-loaded result here keeps both the recorded call
  // AND a controllable resolved value.
  const requestResults: unknown[] = [];
  const requestImpl = vi.fn((topic: string, intent: string, args: unknown[] = []) => {
    requests.push({ topic, intent, args });
    return Promise.resolve(requestResults.length > 0 ? requestResults.shift() : undefined);
  });
  const transport = {
    subscribe: vi.fn((topic: string, listener: StateListener) => {
      listeners.set(topic, listener);
      return () => listeners.delete(topic);
    }),
    intent: vi.fn((topic: string, intent: string, args: unknown[] = []) => {
      intents.push({ topic, intent, args });
    }),
    // ADR-003 stage 3.1: SceneStore's own mutating intent call sites now go
    // through fireIntent, not the bare intent() above - recorded into the
    // SAME `intents` array (real WsTransport.fireIntent's own id-tracking/
    // error-recovery path is exercised by transport.test.ts, not re-tested
    // at every one of this file's call sites) so none of this file's many
    // existing `expect(intents).toEqual([...])` assertions needed to change.
    fireIntent: vi.fn((topic: string, intent: string, args: unknown[] = []) => {
      intents.push({ topic, intent, args });
    }),
    request: requestImpl,
    // ADR-003 stage 3.4: the scene topic's delta channel. Kept in its own
    // map (mirroring the real WsTransport's own parallel registry) so a
    // test can drive patches and snapshots independently, which is exactly
    // what the baseRevision-gap cases below need.
    subscribePatch: vi.fn((topic: string, listener: PatchListener) => {
      patchListeners.set(topic, listener);
      return () => patchListeners.delete(topic);
    }),
    resubscribe: vi.fn((topic: string) => {
      resubscribes.push(topic);
    }),
  } as unknown as WsTransport;
  return {
    transport,
    listeners,
    patchListeners,
    resubscribes,
    intents,
    requests,
    requestImpl,
    requestResults,
  };
}

function validScenePayload(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 1,
    minCompatibleSchemaVersion: 1,
    revision: 3,
    nodes: [
      {
        id: "n0",
        x: 1,
        y: 2,
        title: "A",
        kind: "placeholder",
        content: "",
        isUser: false,
        isCollapsed: false,
        code: "",
        language: "",
        attachmentKind: "",
        filePath: "",
        mimeType: "",
        previewLabel: "",
        isDocked: false,
        imageAssetId: "",
        history: [],
        researchStage: "",
        researchCompleted: 0,
        researchTotal: 0,
        researchError: "",
        artifactContent: "",
        gitlinkRepo: "",
        gitlinkBranch: "",
        gitlinkScopeMode: "selected",
        gitlinkLocalRoot: "",
        gitlinkRepoFilePaths: [],
        gitlinkSelectedPaths: [],
        gitlinkTaskPrompt: "",
        gitlinkContextStats: {},
        gitlinkContextSummary: "",
        gitlinkContextVersion: 0,
        gitlinkProposalMarkdown: "",
        gitlinkPendingChanges: [],
        gitlinkPreviewText: "",
        gitlinkChangeState: "",
        gitlinkError: "",
        pycoderMode: "ai_driven",
        pycoderPrompt: "",
        pycoderCode: "",
        pycoderOutput: "",
        pycoderAnalysis: "",
        pycoderLastRunFailed: false,
        pycoderAwaitingApproval: false,
        pycoderError: "",
        codeSandboxRequirements: "",
        codeSandboxApprovalRequirements: "",
        codeSandboxApprovalAllowSourceBuilds: false,
        codeSandboxApprovalIsRepair: false,
        codeSandboxPrompt: "",
        codeSandboxCode: "",
        codeSandboxOutput: "",
        codeSandboxAnalysis: "",
        codeSandboxAwaitingApproval: false,
        codeSandboxError: "",
        // ADR-003 stage 3.3 (C9)
        isBranchSynthesis: false,
        synthesisInstructions: "",
        branchStatus: "active",
        isFinalDeliverable: false,
        isSystemPrompt: false,
        isSummaryNote: false,
        isBranchComparison: false,
        itemIds: [],
        isLocked: true,
        chartType: "",
        chartData: {},
        chartError: "",
        chartAssetId: "",
        chartAssetVersion: 0,
        chartWidth: 680.0,
        chartHeight: 500.0,
        chartAspectLocked: true,
        chartSourceNodeId: "",
        chatScrollValue: 0.0,
      },
    ],
    edges: [],
    pins: [],
    snapToGrid: true,
    fadeConnectionsEnabled: false,
    orthogonalRouting: false,
    smartGuides: false,
    hasSavedChat: false,
    dragFactor: 0.5,
    fontFamily: "Segoe UI",
    fontSizePt: 9,
    fontColor: "#F0F0F0",
    ...overrides,
  };
}

describe("SceneStore", () => {
  it("accepts a VALID scene snapshot and notifies subscribers", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    const seen = vi.fn();
    store.subscribe(seen);

    listeners.get("scene")!(validScenePayload());
    expect(seen).toHaveBeenCalledTimes(1);
    expect(store.getScene().nodes[0].title).toBe("A");
    expect(store.getScene().dragFactor).toBe(0.5);
  });

  // ADR-003 stage 3.3 (C9) review-fix: every other test that touches
  // chartData only ever exercises the ALL-FIELDS-absent default ({}) the
  // base validScenePayload() node uses - the runtime validator's
  // checkChartDataRow/checkChartFlowRow (bridge-core/generated/scene-
  // state.ts) had never been proven against a real POPULATED chart payload,
  // including the one field shape (sankey's nested `flows` list of
  // ChartFlowRow objects) no other test anywhere touches at all.
  it("accepts a real populated sankey chart node's chartData (flows) within a scene snapshot", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    const seen = vi.fn();
    store.subscribe(seen);

    const base = validScenePayload().nodes[0] as Record<string, unknown>;
    const chartNode = {
      ...base,
      id: "chart-1",
      kind: "chart",
      chartType: "sankey",
      chartData: {
        type: "sankey",
        title: "Flow",
        flows: [
          { source: "A", target: "B", value: 10 },
          { source: "B", target: "C", value: 4.5 },
        ],
      },
      chartAssetId: "asset-chart-1",
      chartAssetVersion: 1,
    };

    listeners.get("scene")!(validScenePayload({ nodes: [chartNode] }));
    expect(seen).toHaveBeenCalledTimes(1);
    expect(store.getScene().nodes[0].chartData).toEqual(chartNode.chartData);
  });

  it("accepts a real populated bar chart node's chartData (labels/values) within a scene snapshot", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    const seen = vi.fn();
    store.subscribe(seen);

    const base = validScenePayload().nodes[0] as Record<string, unknown>;
    const chartNode = {
      ...base,
      id: "chart-1",
      kind: "chart",
      chartType: "bar",
      chartData: {
        type: "bar",
        title: "Revenue",
        labels: ["Q1", "Q2"],
        values: [10, 20],
        xAxis: "Quarter",
        yAxis: "Revenue",
      },
      chartAssetId: "asset-chart-1",
      chartAssetVersion: 1,
    };

    listeners.get("scene")!(validScenePayload({ nodes: [chartNode] }));
    expect(seen).toHaveBeenCalledTimes(1);
    expect(store.getScene().nodes[0].chartData).toEqual(chartNode.chartData);
  });

  it("REJECTS a scene snapshot whose sankey flow has a wrong-typed nested field", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    const base = validScenePayload().nodes[0] as Record<string, unknown>;
    const chartNode = {
      ...base,
      id: "chart-1",
      kind: "chart",
      chartType: "sankey",
      chartData: {
        type: "sankey",
        title: "Flow",
        flows: [{ source: "A", target: "B", value: "not-a-number" }],
      },
    };

    listeners.get("scene")!(validScenePayload({ nodes: [chartNode] }));
    expect(store.getScene()).toEqual(initialSceneState);
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("REJECTS a malformed snapshot and keeps the previous state", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    listeners.get("scene")!({ revision: "not-a-scene" });
    expect(store.getScene()).toEqual(initialSceneState);
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  // ADR-003 stage 3.4: the scene patch protocol. The store now receives
  // `kind:"patch"` deltas (upsertNode/removeNodes/upsertEdge/removeEdges/
  // setView/setMeta) applied ON TOP of the current scene, instead of a full
  // snapshot on every backend mutation - see SceneStore.applyScenePatch.
  describe("scene patch application (ADR-003 stage 3.4)", () => {
    function connectedStoreAtRevision3() {
      const fake = makeFakeTransport();
      const store = new SceneStore(fake.transport);
      store.connect();
      // validScenePayload() is revision 3 - every patch below must therefore
      // arrive with baseRevision 3 to be accepted.
      fake.listeners.get("scene")!(validScenePayload());
      return { ...fake, store };
    }

    it("applies an upsertNode op onto the existing scene without replacing it wholesale", () => {
      const { store, patchListeners } = connectedStoreAtRevision3();
      const existing = store.getScene().nodes[0];

      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [{ op: "upsertNode", node: { ...existing, title: "renamed" } }],
      });

      expect(store.getScene().nodes).toHaveLength(1);
      expect(store.getScene().nodes[0].title).toBe("renamed");
      expect(store.getScene().revision).toBe(4);
    });

    it("adds a node it has never seen, and removes one via removeNodes", () => {
      const { store, patchListeners } = connectedStoreAtRevision3();
      const existing = store.getScene().nodes[0];

      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [{ op: "upsertNode", node: { ...existing, id: "n1", title: "second" } }],
      });
      expect(store.getScene().nodes.map((n) => n.id)).toEqual(["n0", "n1"]);

      patchListeners.get("scene")!({
        revision: 5,
        baseRevision: 4,
        ops: [{ op: "removeNodes", ids: ["n0"] }],
      });
      expect(store.getScene().nodes.map((n) => n.id)).toEqual(["n1"]);
    });

    it("preserves the object identity of nodes the patch did not touch", () => {
      // The property ADR-011 stage 11.1's React.memo work depends on: a
      // patch that changes one node must not mint new objects for the rest.
      // Nothing in the app exploits this yet (toFlowNodes still rebuilds
      // everything, and there is no React.memo anywhere), so without this
      // test the property could silently regress before 11.1 arrives to
      // depend on it.
      const { store, patchListeners } = connectedStoreAtRevision3();
      const first = store.getScene().nodes[0];
      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [{ op: "upsertNode", node: { ...first, id: "n1", title: "second" } }],
      });
      const untouched = store.getScene().nodes[0];

      patchListeners.get("scene")!({
        revision: 5,
        baseRevision: 4,
        ops: [{ op: "upsertNode", node: { ...untouched, id: "n1", title: "changed again" } }],
      });

      expect(store.getScene().nodes[0]).toBe(untouched);
    });

    it("applies setView and setMeta ops onto the scene's own fields", () => {
      const { store, patchListeners } = connectedStoreAtRevision3();

      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [
          { op: "setView", view: { zoomFactor: 2, scrollX: 10, scrollY: 20 } },
          { op: "setMeta", meta: { dragFactor: 0.25, fontFamily: "Consolas" } },
        ],
      });

      const scene = store.getScene() as unknown as Record<string, unknown>;
      expect(scene.zoomFactor).toBe(2);
      expect(scene.scrollX).toBe(10);
      expect(store.getScene().dragFactor).toBe(0.25);
      expect(store.getScene().fontFamily).toBe("Consolas");
    });

    it("upserts and removes edges", () => {
      const { store, patchListeners } = connectedStoreAtRevision3();

      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [{ op: "upsertEdge", edge: { id: "e1", source: "n0", target: "n0" } }],
      });
      expect(store.getScene().edges.map((e) => e.id)).toEqual(["e1"]);

      patchListeners.get("scene")!({
        revision: 5,
        baseRevision: 4,
        ops: [{ op: "removeEdges", ids: ["e1"] }],
      });
      expect(store.getScene().edges).toEqual([]);
    });

    it("REFUSES a patch whose baseRevision does not match, and asks for a fresh snapshot", () => {
      // The self-healing half of the protocol: a gap means a frame was
      // missed, and the missing ops are the only thing that could close it,
      // so applying this one would produce a scene that never existed
      // server-side. Re-snapshot instead of guessing.
      const { store, patchListeners, resubscribes } = connectedStoreAtRevision3();
      const before = store.getScene();

      patchListeners.get("scene")!({
        revision: 9,
        baseRevision: 8, // client is at 3 - a real gap
        ops: [{ op: "removeNodes", ids: ["n0"] }],
      });

      expect(store.getScene()).toBe(before);
      expect(resubscribes).toEqual(["scene"]);
    });

    it("REFUSES a patch carrying an unknown op kind rather than applying the ops around it", () => {
      const { store, patchListeners, resubscribes } = connectedStoreAtRevision3();
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
      const before = store.getScene();

      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [{ op: "reticulateSplines", whatever: true }],
      });

      expect(store.getScene()).toBe(before);
      expect(resubscribes).toEqual(["scene"]);
      expect(consoleError).toHaveBeenCalled();
      consoleError.mockRestore();
    });

    it("notifies subscribers exactly once per applied patch", () => {
      const { store, patchListeners } = connectedStoreAtRevision3();
      const seen = vi.fn();
      store.subscribe(seen);

      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [{ op: "setMeta", meta: { dragFactor: 0.75 } }],
      });

      expect(seen).toHaveBeenCalledTimes(1);
    });

    it("does not notify subscribers when a patch is refused", () => {
      const { store, patchListeners } = connectedStoreAtRevision3();
      const seen = vi.fn();
      store.subscribe(seen);

      patchListeners.get("scene")!({ revision: 9, baseRevision: 8, ops: [] });

      expect(seen).not.toHaveBeenCalled();
    });

    // Review-fix: ops used to be applied from bare TS casts with no runtime
    // check. That was strictly WORSE than the snapshot path it replaced: a
    // malformed op that does not throw still advanced `revision`, so every
    // later patch's baseRevision matched and the gap detector could never
    // notice - permanent divergence with nothing able to detect it. The
    // result is now validated and the whole patch discarded if it fails.
    it("REFUSES a patch whose removeNodes ids are not an array (silently removed nothing before)", () => {
      const { store, patchListeners, resubscribes } = connectedStoreAtRevision3();
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
      const before = store.getScene();

      // `new Set(null)` is an EMPTY set - this removed nothing, returned
      // true, and advanced the revision, so the client kept showing a node
      // the server had deleted, undetectably, until a reconnect.
      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [{ op: "removeNodes", ids: null }],
      });

      expect(store.getScene()).toBe(before);
      expect(store.getScene().revision).toBe(3);
      expect(resubscribes).toEqual(["scene"]);
      consoleError.mockRestore();
    });

    it("REFUSES a patch whose upsertNode carries a wrong-typed field", () => {
      // A string `x` reached React Flow's `position: {x, y}` and turned the
      // canvas transform into NaN. The generated validator has always
      // rejected this on the snapshot path.
      const { store, patchListeners, resubscribes } = connectedStoreAtRevision3();
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
      const existing = store.getScene().nodes[0];

      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [{ op: "upsertNode", node: { ...existing, x: "not-a-number" } }],
      });

      expect(store.getScene().revision).toBe(3);
      expect(resubscribes).toEqual(["scene"]);
      consoleError.mockRestore();
    });

    it("REFUSES a patch whose op body THROWS rather than merely being wrong", () => {
      // `new Set(7)` is a TypeError. The exception used to escape through the
      // listener fan-out to socket.onmessage, so the `if (!applyScenePatch)`
      // resync never ran - the "refuse whole and self-heal" contract silently
      // did not hold for this input class.
      const { store, patchListeners, resubscribes } = connectedStoreAtRevision3();
      const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

      expect(() =>
        patchListeners.get("scene")!({
          revision: 4,
          baseRevision: 3,
          ops: [{ op: "removeNodes", ids: 7 }],
        }),
      ).not.toThrow();

      expect(store.getScene().revision).toBe(3);
      expect(resubscribes).toEqual(["scene"]);
      consoleError.mockRestore();
    });

    it("does not let a setMeta op blank structural fields like pins", () => {
      // The op bodies are spread in blindly, so `nodes`/`edges`/`revision`
      // were protected only by key order and `pins` not at all - a
      // `{"pins": null}` would crash PinOverlay's scene.pins.filter() on
      // every render. No backend key does this today; the guard is
      // structural rather than one field name away from being needed.
      const { store, patchListeners } = connectedStoreAtRevision3();
      const pinsBefore = store.getScene().pins;

      patchListeners.get("scene")!({
        revision: 4,
        baseRevision: 3,
        ops: [{ op: "setMeta", meta: { pins: null, nodes: null, revision: 999, dragFactor: 0.3 } }],
      });

      expect(store.getScene().pins).toBe(pinsBefore);
      expect(store.getScene().nodes).toHaveLength(1);
      expect(store.getScene().revision).toBe(4);
      expect(store.getScene().dragFactor).toBe(0.3);
    });

    it("asks for ONE resync per gap, not one per refused patch", () => {
      // Review-fix, measured against a real backend: without this guard a
      // single dropped frame during a burst of mutations made things far
      // WORSE than the full snapshots this stage replaced. The client
      // refuses every subsequent patch until its snapshot lands, and each
      // refusal fired another resync answered with another FULL snapshot -
      // 14 requests and 14 snapshots from one dropped frame in a
      // 15-mutation burst (~22 MB at the 500-node workload).
      const { store, patchListeners, resubscribes } = connectedStoreAtRevision3();
      const patch = patchListeners.get("scene")!;

      // A burst arriving while the client sits at revision 3 with a gap.
      for (let revision = 10; revision < 20; revision++) {
        patch({ revision, baseRevision: revision - 1, ops: [] });
      }

      expect(resubscribes).toEqual(["scene"]);
      expect(store.getScene().revision).toBe(3);
    });

    it("re-arms the resync request once the recovering snapshot lands", () => {
      // The flag must not wedge recovery permanently shut: after the
      // snapshot closes one gap, a LATER gap must be able to ask again.
      const { store, patchListeners, listeners, resubscribes } = connectedStoreAtRevision3();
      const patch = patchListeners.get("scene")!;

      patch({ revision: 9, baseRevision: 8, ops: [] });
      expect(resubscribes).toEqual(["scene"]);

      listeners.get("scene")!(validScenePayload({ revision: 9 }));
      expect(store.getScene().revision).toBe(9);

      patch({ revision: 30, baseRevision: 29, ops: [] });
      expect(resubscribes).toEqual(["scene", "scene"]);
    });

    it("accepts a later snapshot after a refused patch, resyncing the client", () => {
      const { store, patchListeners, listeners } = connectedStoreAtRevision3();
      patchListeners.get("scene")!({ revision: 9, baseRevision: 8, ops: [] });

      listeners.get("scene")!(validScenePayload({ revision: 9 }));

      expect(store.getScene().revision).toBe(9);
      // ...and a patch built on THAT revision now applies cleanly.
      patchListeners.get("scene")!({
        revision: 10,
        baseRevision: 9,
        ops: [{ op: "setMeta", meta: { dragFactor: 0.5 } }],
      });
      expect(store.getScene().dragFactor).toBe(0.5);
    });
  });

  it("routes grid snapshots through the grid validator", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    listeners.get("grid-control")!({
      schemaVersion: 1,
      minCompatibleSchemaVersion: 1,
      revision: 1,
      gridSize: 50,
      gridOpacityPercent: 80,
      gridStyle: "Lines",
      gridColor: "#404040",
      sizePresets: [10, 20, 50, 100],
      stylePresets: ["Dots", "Lines", "Cross"],
      colorPresets: [],
    });
    expect(store.getGrid().gridSize).toBe(50);
    expect(store.getGrid().gridStyle).toBe("Lines");
  });

  it("sends intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addNode(10, 20, "hello");
    store.moveNode("n1", 3, 4);
    store.connectNodes("n1", "n2");
    store.addPin("P", 5, 6, "note");
    store.setSnapToGrid(true);
    store.setDragFactor(0.25);
    expect(intents).toEqual([
      { topic: "scene", intent: "addNode", args: [10, 20, "hello"] },
      { topic: "scene", intent: "moveNode", args: ["n1", 3, 4] },
      { topic: "scene", intent: "connectNodes", args: ["n1", "n2"] },
      { topic: "scene", intent: "addPin", args: ["P", 5, 6, "note"] },
      { topic: "scene", intent: "setSnapToGrid", args: [true] },
      { topic: "scene", intent: "setDragFactor", args: [0.25] },
    ]);
  });

  it("moveNodes sends the scene-topic moveNodes intent with one [id, x, y] triple per position", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.moveNodes([
      { id: "frame-1", x: 120, y: 220 },
      { id: "m1", x: 20, y: 20 },
      { id: "m2", x: 320, y: 320 },
    ]);
    expect(intents).toEqual([
      {
        topic: "scene",
        intent: "moveNodes",
        args: [
          [
            ["frame-1", 120, 220],
            ["m1", 20, 20],
            ["m2", 320, 320],
          ],
        ],
      },
    ]);
  });

  it("sends chat-node intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addChatNode(10, 20, "hello", true);
    store.addChatNode(30, 40, "hi back", false, "n1");
    store.setChatCollapsed("n1", true);
    store.deleteChatNode("n1");
    expect(intents).toEqual([
      { topic: "scene", intent: "addChatNode", args: [10, 20, "hello", true] },
      { topic: "scene", intent: "addChatNode", args: [30, 40, "hi back", false, "n1"] },
      { topic: "scene", intent: "setChatCollapsed", args: ["n1", true] },
      { topic: "scene", intent: "deleteChatNode", args: ["n1"] },
    ]);
  });

  it("collapseAllNodes sends the scene-topic collapseAllNodes intent with no args", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.collapseAllNodes();
    expect(intents).toEqual([{ topic: "scene", intent: "collapseAllNodes", args: [] }]);
  });

  it("expandAllNodes sends the scene-topic expandAllNodes intent with no args", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.expandAllNodes();
    expect(intents).toEqual([{ topic: "scene", intent: "expandAllNodes", args: [] }]);
  });

  it("sends code-node intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addCodeNode(10, 20, "print('hi')", "python");
    store.addCodeNode(30, 40, "console.log('hi')", "javascript", "n1");
    expect(intents).toEqual([
      { topic: "scene", intent: "addCodeNode", args: [10, 20, "print('hi')", "python"] },
      { topic: "scene", intent: "addCodeNode", args: [30, 40, "console.log('hi')", "javascript", "n1"] },
    ]);
  });

  it("sends document-node intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addDocumentNode(10, 20, "notes.pdf", "some content", "document", "n1");
    store.addDocumentNode(30, 40, "clip.mp3", "", "audio", "n1", {
      filePath: "C:/audio/clip.mp3",
      mimeType: "audio/mpeg",
      durationSeconds: 125,
      byteSize: 48000,
      previewLabel: "Audio | 2:05",
    });
    expect(intents).toEqual([
      {
        topic: "scene",
        intent: "addDocumentNode",
        args: [10, 20, "notes.pdf", "some content", "document", "n1", "", "", null, null, ""],
      },
      {
        topic: "scene",
        intent: "addDocumentNode",
        args: [
          30,
          40,
          "clip.mp3",
          "",
          "audio",
          "n1",
          "C:/audio/clip.mp3",
          "audio/mpeg",
          125,
          48000,
          "Audio | 2:05",
        ],
      },
    ]);
  });

  it("sends thinking-node and docking intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addThinkingNode(10, 20, "Weighing the options...", "n1");
    store.setNodeDocked("n2", true);
    store.setNodeDocked("n2", false);
    expect(intents).toEqual([
      { topic: "scene", intent: "addThinkingNode", args: [10, 20, "Weighing the options...", "n1"] },
      { topic: "scene", intent: "setNodeDocked", args: ["n2", true] },
      { topic: "scene", intent: "setNodeDocked", args: ["n2", false] },
    ]);
  });

  it("sends html-node intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addHtmlNode(10, 20, "<p>hello</p>", "n1");
    expect(intents).toEqual([
      { topic: "scene", intent: "addHtmlNode", args: [10, 20, "<p>hello</p>", "n1"] },
    ]);
  });

  it("sends image-node intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addImageNode(10, 20, "base64bytes==", "a red fox in the snow", "n1");
    store.addImageNode(30, 40, "base64bytes2==", "a mountain lake", "n1", "image/jpeg");
    expect(intents).toEqual([
      {
        topic: "scene",
        intent: "addImageNode",
        args: [10, 20, "base64bytes==", "a red fox in the snow", "n1", "image/png"],
      },
      {
        topic: "scene",
        intent: "addImageNode",
        args: [30, 40, "base64bytes2==", "a mountain lake", "n1", "image/jpeg"],
      },
    ]);
  });

  it("sends conversation-node intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addConversationNode(10, 20, "n1");
    store.sendConversationMessage("n2", "hello there");
    store.appendConversationAssistantMessage("n2", "hi back");
    store.deleteConversationMessage("n2", 0);
    expect(intents).toEqual([
      { topic: "scene", intent: "addConversationNode", args: [10, 20, "n1"] },
      { topic: "scene", intent: "sendConversationMessage", args: ["n2", "hello there"] },
      { topic: "scene", intent: "appendConversationAssistantMessage", args: ["n2", "hi back"] },
      { topic: "scene", intent: "deleteConversationMessage", args: ["n2", 0] },
    ]);
  });

  it("cancelConversationRequest fires the scene-topic cancelChatRequest intent", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.cancelConversationRequest("req-42");
    expect(intents).toEqual([{ topic: "scene", intent: "cancelChatRequest", args: ["req-42"] }]);
  });

  it("regenerateResponse sends the scene-topic regenerateResponse intent with the chat node id", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.regenerateResponse("n1");
    expect(intents).toEqual([{ topic: "scene", intent: "regenerateResponse", args: ["n1"] }]);
  });

  it("generateImage sends the scene-topic generateImage intent with the chat node id", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.generateImage("n1");
    expect(intents).toEqual([{ topic: "scene", intent: "generateImage", args: ["n1"] }]);
  });

  it("regenerateImage sends the scene-topic regenerateImage intent with the image node id", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.regenerateImage("img1");
    expect(intents).toEqual([{ topic: "scene", intent: "regenerateImage", args: ["img1"] }]);
  });

  it("runWebResearch sends the scene-topic runWebResearch intent with [nodeId, queryText]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.runWebResearch("n1", "who won the 2019 world series");
    expect(intents).toEqual([
      { topic: "scene", intent: "runWebResearch", args: ["n1", "who won the 2019 world series"] },
    ]);
  });

  it("cancelWebResearchRequest sends the scene-topic cancelWebResearchRequest intent with the requestId", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.cancelWebResearchRequest("req-99");
    expect(intents).toEqual([
      { topic: "scene", intent: "cancelWebResearchRequest", args: ["req-99"] },
    ]);
  });

  it("sendArtifactMessage sends the scene-topic sendArtifactMessage intent with [nodeId, text]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.sendArtifactMessage("n1", "Draft a project proposal");
    expect(intents).toEqual([
      { topic: "scene", intent: "sendArtifactMessage", args: ["n1", "Draft a project proposal"] },
    ]);
  });

  it("cancelArtifactRequest sends the scene-topic cancelArtifactRequest intent with the requestId", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.cancelArtifactRequest("req-13");
    expect(intents).toEqual([
      { topic: "scene", intent: "cancelArtifactRequest", args: ["req-13"] },
    ]);
  });

  it("fetchGitlinkRepositories sends a REQUEST (not a fire-and-forget intent) with [nodeId], and resolves to the reply", async () => {
    const { transport, requests, intents, requestResults } = makeFakeTransport();
    requestResults.push(["owner/repo-a", "owner/repo-b"]);
    const store = new SceneStore(transport);

    const result = await store.fetchGitlinkRepositories("n1");
    expect(requests).toEqual([
      { topic: "scene", intent: "fetchGitlinkRepositories", args: ["n1"] },
    ]);
    expect(intents).toEqual([]);
    expect(result).toEqual(["owner/repo-a", "owner/repo-b"]);
  });

  it("loadGitlinkRepoTree sends the scene-topic loadGitlinkRepoTree intent with [nodeId, repo, branch]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.loadGitlinkRepoTree("n1", "owner/repo", "main");
    expect(intents).toEqual([
      { topic: "scene", intent: "loadGitlinkRepoTree", args: ["n1", "owner/repo", "main"] },
    ]);
  });

  it("setGitlinkLocalRoot sends the scene-topic setGitlinkLocalRoot intent with [nodeId, localRoot]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setGitlinkLocalRoot("n1", "C:/repos/graphlink");
    expect(intents).toEqual([
      { topic: "scene", intent: "setGitlinkLocalRoot", args: ["n1", "C:/repos/graphlink"] },
    ]);
  });

  it("pickGitlinkLocalRoot sends the scene-topic pickGitlinkLocalRoot intent with [nodeId]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.pickGitlinkLocalRoot("n1");
    expect(intents).toEqual([{ topic: "scene", intent: "pickGitlinkLocalRoot", args: ["n1"] }]);
  });

  it("importGitlinkSnapshot sends the scene-topic importGitlinkSnapshot intent with [nodeId, repo, branch]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.importGitlinkSnapshot("n1", "owner/repo", "main");
    expect(intents).toEqual([
      { topic: "scene", intent: "importGitlinkSnapshot", args: ["n1", "owner/repo", "main"] },
    ]);
  });

  it("buildGitlinkContext sends the scene-topic buildGitlinkContext intent with [nodeId, scopeMode, selectedPaths]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.buildGitlinkContext("n1", "selected", ["src/a.py", "src/b.py"]);
    expect(intents).toEqual([
      {
        topic: "scene",
        intent: "buildGitlinkContext",
        args: ["n1", "selected", ["src/a.py", "src/b.py"]],
      },
    ]);
  });

  it("fetchGitlinkContext sends a REQUEST (not a fire-and-forget intent) with [nodeId], and resolves to the reply", async () => {
    const { transport, requests, intents, requestResults } = makeFakeTransport();
    requestResults.push("<context>...</context>");
    const store = new SceneStore(transport);

    const result = await store.fetchGitlinkContext("n1");
    expect(requests).toEqual([{ topic: "scene", intent: "fetchGitlinkContext", args: ["n1"] }]);
    expect(intents).toEqual([]);
    expect(result).toBe("<context>...</context>");
  });

  it("runGitlinkChangeSet sends the scene-topic runGitlinkChangeSet intent with [nodeId, taskPrompt]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.runGitlinkChangeSet("n1", "Add a health-check endpoint");
    expect(intents).toEqual([
      { topic: "scene", intent: "runGitlinkChangeSet", args: ["n1", "Add a health-check endpoint"] },
    ]);
  });

  it("cancelGitlinkRequest sends the scene-topic cancelGitlinkRequest intent with the requestId", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.cancelGitlinkRequest("req-55");
    expect(intents).toEqual([{ topic: "scene", intent: "cancelGitlinkRequest", args: ["req-55"] }]);
  });

  it("applyGitlinkChanges sends the scene-topic applyGitlinkChanges intent with [nodeId, fingerprint]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.applyGitlinkChanges("n1", "fingerprint-abc123");
    expect(intents).toEqual([
      { topic: "scene", intent: "applyGitlinkChanges", args: ["n1", "fingerprint-abc123"] },
    ]);
  });

  it("setPyCoderMode sends the scene-topic setPyCoderMode intent with [nodeId, mode]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setPyCoderMode("n1", "manual");
    expect(intents).toEqual([{ topic: "scene", intent: "setPyCoderMode", args: ["n1", "manual"] }]);
  });

  it("runPyCoder sends the scene-topic runPyCoder intent with [nodeId, inputText]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.runPyCoder("n1", "write a fibonacci function");
    expect(intents).toEqual([
      { topic: "scene", intent: "runPyCoder", args: ["n1", "write a fibonacci function"] },
    ]);
  });

  it("cancelPyCoderRequest sends the scene-topic cancelPyCoderRequest intent with the requestId", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.cancelPyCoderRequest("req-1");
    expect(intents).toEqual([{ topic: "scene", intent: "cancelPyCoderRequest", args: ["req-1"] }]);
  });

  it("setCodeSandboxRequirements sends the scene-topic setCodeSandboxRequirements intent with [nodeId, requirementsText]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setCodeSandboxRequirements("n1", "numpy\npandas==2.2.0");
    expect(intents).toEqual([
      {
        topic: "scene",
        intent: "setCodeSandboxRequirements",
        args: ["n1", "numpy\npandas==2.2.0"],
      },
    ]);
  });

  it("setCodeSandboxAllowSourceBuilds sends the scene-topic setCodeSandboxAllowSourceBuilds intent with [nodeId, allow]", () => {
    // ADR-005 stage 5.5 test-coverage-gap fix: the sibling requirements
    // field above already had this parity test; the source-build checkbox
    // did not, so a wrong intent name/argument order would ship silently.
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setCodeSandboxAllowSourceBuilds("n1", true);
    expect(intents).toEqual([
      {
        topic: "scene",
        intent: "setCodeSandboxAllowSourceBuilds",
        args: ["n1", true],
      },
    ]);
  });

  it("runCodeSandbox sends the scene-topic runCodeSandbox intent with [nodeId, inputText]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.runCodeSandbox("n1", "plot a sine wave");
    expect(intents).toEqual([
      { topic: "scene", intent: "runCodeSandbox", args: ["n1", "plot a sine wave"] },
    ]);
  });

  it("cancelCodeSandboxRequest sends the scene-topic cancelCodeSandboxRequest intent with the requestId", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.cancelCodeSandboxRequest("req-2");
    expect(intents).toEqual([
      { topic: "scene", intent: "cancelCodeSandboxRequest", args: ["req-2"] },
    ]);
  });

  it("approveCodeExecution sends the scene-topic approveCodeExecution intent with ONLY the requestId", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.approveCodeExecution("req-3");
    expect(intents).toEqual([{ topic: "scene", intent: "approveCodeExecution", args: ["req-3"] }]);
  });

  it("denyCodeExecution sends the scene-topic denyCodeExecution intent with ONLY the requestId", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.denyCodeExecution("req-4");
    expect(intents).toEqual([{ topic: "scene", intent: "denyCodeExecution", args: ["req-4"] }]);
  });

  it("addNote sends the scene-topic addNote intent with [x, y, isSystemPrompt, isSummaryNote], defaulting both flags false", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addNote(10, 20);
    expect(intents).toEqual([{ topic: "scene", intent: "addNote", args: [10, 20, false, false] }]);
  });

  it("addNote forwards isSystemPrompt/isSummaryNote when supplied", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addNote(10, 20, { isSystemPrompt: true });
    store.addNote(30, 40, { isSummaryNote: true });
    expect(intents).toEqual([
      { topic: "scene", intent: "addNote", args: [10, 20, true, false] },
      { topic: "scene", intent: "addNote", args: [30, 40, false, true] },
    ]);
  });

  it("setNoteContent sends the scene-topic setNoteContent intent with [nodeId, content]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setNoteContent("n1", "updated text");
    expect(intents).toEqual([{ topic: "scene", intent: "setNoteContent", args: ["n1", "updated text"] }]);
  });

  it("createFrame sends the scene-topic createFrame intent with [itemIds]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.createFrame(["n1", "n2"]);
    expect(intents).toEqual([{ topic: "scene", intent: "createFrame", args: [["n1", "n2"]] }]);
  });

  it("createContainer sends the scene-topic createContainer intent with [itemIds]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.createContainer(["n1", "n2"]);
    expect(intents).toEqual([{ topic: "scene", intent: "createContainer", args: [["n1", "n2"]] }]);
  });

  it("compareBranches sends the scene-topic compareBranches intent with [nodeIds] (ADR-002 Workstream 1)", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.compareBranches(["n1", "n2", "n3"]);
    expect(intents).toEqual([{ topic: "scene", intent: "compareBranches", args: [["n1", "n2", "n3"]] }]);
  });

  it("setBranchStatus sends the scene-topic setBranchStatus intent with [nodeId, status] (ADR-002 Workstream 1)", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setBranchStatus("n1", "accepted");
    expect(intents).toEqual([{ topic: "scene", intent: "setBranchStatus", args: ["n1", "accepted"] }]);
  });

  it("setFinalDeliverable sends the scene-topic setFinalDeliverable intent with [nodeId, isFinal] (ADR-002 Workstream 1)", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setFinalDeliverable("n1", true);
    expect(intents).toEqual([{ topic: "scene", intent: "setFinalDeliverable", args: ["n1", true] }]);
  });

  it("collapseBranch sends the scene-topic collapseBranch intent with [nodeId, collapsed] (ADR-002 Workstream 1)", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.collapseBranch("n1", true);
    expect(intents).toEqual([{ topic: "scene", intent: "collapseBranch", args: ["n1", true] }]);
  });

  // ADR-002 Workstream 1 ("Branch status and lifecycle"): "Focus Accepted
  // Paths" - local UI state only (see sceneStore.ts's own comment on why
  // it lives here rather than as component state), NOT a WS intent -
  // no transport call is ever expected from these.
  it("getFocusAcceptedPaths/setFocusAcceptedPaths update state and notify listeners", () => {
    const { transport } = makeFakeTransport();
    const store = new SceneStore(transport);
    const seen = vi.fn();
    store.subscribe(seen);

    expect(store.getFocusAcceptedPaths()).toBe(false);
    store.setFocusAcceptedPaths(true);
    expect(store.getFocusAcceptedPaths()).toBe(true);
    expect(seen).toHaveBeenCalledTimes(1);

    store.setFocusAcceptedPaths(false);
    expect(store.getFocusAcceptedPaths()).toBe(false);
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it("setFocusAcceptedPaths is a no-op (no re-emit) when the value is unchanged", () => {
    const { transport } = makeFakeTransport();
    const store = new SceneStore(transport);
    const seen = vi.fn();
    store.subscribe(seen);

    store.setFocusAcceptedPaths(true);
    expect(seen).toHaveBeenCalledTimes(1);
    store.setFocusAcceptedPaths(true);
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("setGroupLabel sends the scene-topic setGroupLabel intent with [nodeId, text]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setGroupLabel("n1", "New Label");
    expect(intents).toEqual([{ topic: "scene", intent: "setGroupLabel", args: ["n1", "New Label"] }]);
  });

  it("setGroupColor sends the scene-topic setGroupColor intent with [nodeId, color, headerColor], both nullable", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setGroupColor("n1", "#3f8f5c", null);
    store.setGroupColor("n1", null, null);
    expect(intents).toEqual([
      { topic: "scene", intent: "setGroupColor", args: ["n1", "#3f8f5c", null] },
      { topic: "scene", intent: "setGroupColor", args: ["n1", null, null] },
    ]);
  });

  it("toggleFrameLock sends the scene-topic toggleFrameLock intent with [nodeId]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.toggleFrameLock("n1");
    expect(intents).toEqual([{ topic: "scene", intent: "toggleFrameLock", args: ["n1"] }]);
  });

  it("toggleGroupCollapsed sends the scene-topic toggleGroupCollapsed intent with [nodeId]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.toggleGroupCollapsed("n1");
    expect(intents).toEqual([{ topic: "scene", intent: "toggleGroupCollapsed", args: ["n1"] }]);
  });

  it("resizeFrame sends the scene-topic resizeFrame intent with [nodeId, width, height]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.resizeFrame("n1", 500, 300);
    expect(intents).toEqual([{ topic: "scene", intent: "resizeFrame", args: ["n1", 500, 300] }]);
  });

  it("fitFrameToContent sends the scene-topic fitFrameToContent intent with [nodeId]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.fitFrameToContent("n1");
    expect(intents).toEqual([{ topic: "scene", intent: "fitFrameToContent", args: ["n1"] }]);
  });

  it("ungroup sends the scene-topic ungroup intent with [nodeId]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.ungroup("n1");
    expect(intents).toEqual([{ topic: "scene", intent: "ungroup", args: ["n1"] }]);
  });

  it("generateChart sends the scene-topic generateChart intent with [parentNodeId, chartType]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.generateChart("chat-1", "bar");
    expect(intents).toEqual([{ topic: "scene", intent: "generateChart", args: ["chat-1", "bar"] }]);
  });

  it("resizeChart sends the scene-topic resizeChart intent with [nodeId, width, height]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.resizeChart("n1", 900, 650);
    expect(intents).toEqual([{ topic: "scene", intent: "resizeChart", args: ["n1", 900, 650] }]);
  });

  it("toggleChartAspectLock sends the scene-topic toggleChartAspectLock intent with [nodeId]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.toggleChartAspectLock("n1");
    expect(intents).toEqual([{ topic: "scene", intent: "toggleChartAspectLock", args: ["n1"] }]);
  });

  it("setViewState sends the scene-topic setViewState intent with [zoomFactor, scrollX, scrollY]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setViewState(1.5, 120, -80);
    expect(intents).toEqual([{ topic: "scene", intent: "setViewState", args: [1.5, 120, -80] }]);
  });

  it("setHtmlSplitterState sends the scene-topic setHtmlSplitterState intent with [nodeId, value]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setHtmlSplitterState("html-1", 0.4);
    expect(intents).toEqual([{ topic: "scene", intent: "setHtmlSplitterState", args: ["html-1", 0.4] }]);
  });

  it("setChatScrollValue sends the scene-topic setChatScrollValue intent with [nodeId, value]", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setChatScrollValue("chat-1", 320);
    expect(intents).toEqual([{ topic: "scene", intent: "setChatScrollValue", args: ["chat-1", 320] }]);
  });

  it("subscribeStream forwards directly to transport.subscribeStream and returns its unsubscribe function", () => {
    const { transport } = makeFakeTransport();
    const unsubscribe = vi.fn();
    const subscribeStreamMock = vi.fn().mockReturnValue(unsubscribe);
    (transport as unknown as { subscribeStream: typeof subscribeStreamMock }).subscribeStream =
      subscribeStreamMock;
    const store = new SceneStore(transport);
    const listener = vi.fn();

    const result = store.subscribeStream("req-5", listener);
    expect(subscribeStreamMock).toHaveBeenCalledWith("req-5", listener);
    expect(result).toBe(unsubscribe);
  });

  it("suppresses empty removal intents", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.removeNodes([]);
    store.removeEdges([]);
    expect(intents).toEqual([]);
  });

  it("setSelectedNodeId/getSelectedNodeId update state and notify listeners", () => {
    const { transport } = makeFakeTransport();
    const store = new SceneStore(transport);
    const seen = vi.fn();
    store.subscribe(seen);

    expect(store.getSelectedNodeId()).toBeNull();
    store.setSelectedNodeId("n1");
    expect(store.getSelectedNodeId()).toBe("n1");
    expect(seen).toHaveBeenCalledTimes(1);

    store.setSelectedNodeId(null);
    expect(store.getSelectedNodeId()).toBeNull();
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it("setSelectedNodeId is a no-op (no re-emit) when the id is unchanged", () => {
    const { transport } = makeFakeTransport();
    const store = new SceneStore(transport);
    const seen = vi.fn();

    // Baseline no-op: already null -> null, before subscribing even matters.
    store.setSelectedNodeId(null);
    expect(store.getSelectedNodeId()).toBeNull();

    store.subscribe(seen);
    store.setSelectedNodeId("n1");
    expect(seen).toHaveBeenCalledTimes(1);

    // Re-selecting the SAME id must not re-emit.
    store.setSelectedNodeId("n1");
    expect(seen).toHaveBeenCalledTimes(1);
    expect(store.getSelectedNodeId()).toBe("n1");
  });

  // -- ADR-002 Workstream 1: "Branch from here" ------------------------------

  it("setReplyTargetNodeId/getReplyTargetNodeId update state and notify listeners", () => {
    const { transport } = makeFakeTransport();
    const store = new SceneStore(transport);
    const seen = vi.fn();
    store.subscribe(seen);

    expect(store.getReplyTargetNodeId()).toBeNull();
    store.setReplyTargetNodeId("n1");
    expect(store.getReplyTargetNodeId()).toBe("n1");
    expect(seen).toHaveBeenCalledTimes(1);

    store.setReplyTargetNodeId(null);
    expect(store.getReplyTargetNodeId()).toBeNull();
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it("setReplyTargetNodeId is a no-op (no re-emit) when the id is unchanged", () => {
    const { transport } = makeFakeTransport();
    const store = new SceneStore(transport);
    const seen = vi.fn();
    store.subscribe(seen);

    store.setReplyTargetNodeId("n1");
    expect(seen).toHaveBeenCalledTimes(1);
    store.setReplyTargetNodeId("n1");
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("sendMessage with no pending reply target sends just [text] - unmodified default behavior", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.sendMessage("hello");
    expect(intents).toEqual([{ topic: "scene", intent: "sendMessage", args: ["hello"] }]);
  });

  it("sendMessage consumes a pending reply target as the branch_from_node_id arg, then clears it", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setReplyTargetNodeId("n-root");

    store.sendMessage("branching off root");
    expect(intents).toEqual([
      { topic: "scene", intent: "sendMessage", args: ["branching off root", "n-root"] },
    ]);
    expect(store.getReplyTargetNodeId()).toBeNull();

    // A SECOND send, with no reply target pending anymore, must NOT replay
    // the stale target - proving it was genuinely consumed, not just read.
    store.sendMessage("a normal follow-up");
    expect(intents).toEqual([
      { topic: "scene", intent: "sendMessage", args: ["branching off root", "n-root"] },
      { topic: "scene", intent: "sendMessage", args: ["a normal follow-up"] },
    ]);
  });

  // -- ADR-002 Workstream 1: "Synthesize Branches" ---------------------------

  it("setSynthesizeTargetNodeIds/getSynthesizeTargetNodeIds update state, notify listeners, and clear any pending reply target", () => {
    const { transport } = makeFakeTransport();
    const store = new SceneStore(transport);
    const seen = vi.fn();
    store.subscribe(seen);

    expect(store.getSynthesizeTargetNodeIds()).toBeNull();
    store.setSynthesizeTargetNodeIds(["n1", "n2"]);
    expect(store.getSynthesizeTargetNodeIds()).toEqual(["n1", "n2"]);
    expect(seen).toHaveBeenCalledTimes(1);

    store.setSynthesizeTargetNodeIds(null);
    expect(store.getSynthesizeTargetNodeIds()).toBeNull();
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it("setSynthesizeTargetNodeIds and setReplyTargetNodeId are mutually exclusive - setting one clears the other", () => {
    const { transport } = makeFakeTransport();
    const store = new SceneStore(transport);

    store.setReplyTargetNodeId("n-root");
    expect(store.getReplyTargetNodeId()).toBe("n-root");

    store.setSynthesizeTargetNodeIds(["n1", "n2"]);
    expect(store.getSynthesizeTargetNodeIds()).toEqual(["n1", "n2"]);
    expect(store.getReplyTargetNodeId()).toBeNull();

    store.setReplyTargetNodeId("n-other");
    expect(store.getReplyTargetNodeId()).toBe("n-other");
    expect(store.getSynthesizeTargetNodeIds()).toBeNull();
  });

  it("setReplyTargetNodeId still emits when it clears a pending synthesize target, even if its own id is unchanged (null -> null)", () => {
    const { transport } = makeFakeTransport();
    const store = new SceneStore(transport);
    const seen = vi.fn();
    store.setSynthesizeTargetNodeIds(["n1", "n2"]);
    store.subscribe(seen);

    // replyTargetNodeId is already null, but a synthesize target IS pending -
    // this must still count as a real state change and emit.
    store.setReplyTargetNodeId(null);
    expect(seen).toHaveBeenCalledTimes(1);
    expect(store.getSynthesizeTargetNodeIds()).toBeNull();
  });

  it("sendMessage with a pending synthesize target fires synthesizeBranches with [nodeIds, text] instead of sendMessage, then clears it", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setSynthesizeTargetNodeIds(["n1", "n2"]);

    store.sendMessage("merge the best of both");
    expect(intents).toEqual([
      { topic: "scene", intent: "synthesizeBranches", args: [["n1", "n2"], "merge the best of both"] },
    ]);
    expect(store.getSynthesizeTargetNodeIds()).toBeNull();

    // A SECOND send, with no synthesize target pending anymore, must fall
    // through to an ordinary sendMessage - proving it was genuinely
    // consumed, not just read.
    store.sendMessage("a normal follow-up");
    expect(intents).toEqual([
      { topic: "scene", intent: "synthesizeBranches", args: [["n1", "n2"], "merge the best of both"] },
      { topic: "scene", intent: "sendMessage", args: ["a normal follow-up"] },
    ]);
  });

  it("sendMessage prefers a pending synthesize target over a pending reply target (the two are already mutually exclusive, but this proves the check order)", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.setReplyTargetNodeId("n-root");
    store.setSynthesizeTargetNodeIds(["n1", "n2"]);

    store.sendMessage("combine them");
    expect(intents).toEqual([
      { topic: "scene", intent: "synthesizeBranches", args: [["n1", "n2"], "combine them"] },
    ]);
  });

  it("showInfoNotification sends the notification-topic showInfo intent, not scene", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.showInfoNotification("No document view content is available for this node yet.");
    expect(intents).toEqual([
      {
        topic: "notification",
        intent: "showInfo",
        args: ["No document view content is available for this node yet."],
      },
    ]);
  });

  it("dispose() unsubscribes every topic", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    expect(listeners.size).toBe(4);
    store.dispose();
    expect(listeners.size).toBe(0);
  });
});

describe("scaleDragPosition (the drag-speed contract)", () => {
  it("factor 1 leaves motion unscaled", () => {
    expect(scaleDragPosition({ x: 0, y: 0 }, { x: 100, y: 40 }, 1)).toEqual({ x: 100, y: 40 });
  });

  it("factor 0.5 halves the delta from the drag start", () => {
    expect(scaleDragPosition({ x: 10, y: 10 }, { x: 110, y: 50 }, 0.5)).toEqual({ x: 60, y: 30 });
  });

  it("scales relative to the start, not the origin", () => {
    expect(scaleDragPosition({ x: -20, y: 8 }, { x: -20, y: 8 }, 0.25)).toEqual({ x: -20, y: 8 });
  });
});
