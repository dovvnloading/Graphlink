/**
 * ADR-011 stages 11.2/11.3: SceneCanvas's own wiring into <ReactFlow> -
 * covered separately from SceneCanvas.test.tsx (which drives toFlowNodes/
 * toFlowEdges/etc. as pure functions, that file's own established posture)
 * because these three claims are genuinely about the WIRING between
 * CanvasInner's hooks and the <ReactFlow> element itself, not a pure
 * function's output: onlyRenderVisibleElements being suspended during an
 * export, the edge-hover memo actually skipping a recompute, and the
 * smart-guide drag-size cache actually being built once per GESTURE rather
 * than once per FRAME.
 *
 * @xyflow/react's own <ReactFlow> is replaced with a thin prop-capturing
 * stub - every other export (ReactFlowProvider, useReactFlow, Handle,
 * Position, ...) stays real - rather than mounting the genuine component.
 * Confirmed empirically while building this file, not assumed: a REAL
 * <ReactFlow onlyRenderVisibleElements> mounted in this test environment
 * renders every node regardless of position, because xyflow's own
 * visibility filter (getNodesInside, @xyflow/system) treats any node it has
 * never been able to MEASURE as unconditionally visible - and jsdom can
 * never measure one, since @xyflow/react's per-node ResizeObserver callback
 * never fires against this project's own jsdom ResizeObserver stub
 * (vitest.setup.ts - a deliberate no-op, "none exercise real layout"), and
 * even forcing a firing one hits `window.DOMMatrixReadOnly is not a
 * constructor` inside xyflow's own updateNodeInternals - a jsdom platform
 * gap, not anything this app's code controls. Capturing the exact props/
 * handlers SceneCanvas hands to <ReactFlow> and driving them directly is
 * what actually pins the WIRING this stage changed, without depending on a
 * working browser layout/measurement pipeline underneath.
 *
 * The genuinely mount-DEPENDENT halves of the ADR-011 stage 11.2 audit -
 * "does xyflow's own onlyRenderVisibleElements filter correctly by
 * position" - is xyflow's own library contract, not this app's code; this
 * file pins that SceneCanvas asks for it (onlyRenderVisibleElements: true)
 * and correctly suspends it for an export, which is the actual surface this
 * app owns.
 */
import { ReactFlowProvider, type Edge, type NodeChange } from "@xyflow/react";
import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

type CapturedProps = {
  nodes: Array<{ id: string; selected?: boolean }>;
  edges: Edge[];
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgeMouseEnter: (event: unknown, edge: Edge) => void;
  onEdgeMouseLeave: () => void;
  onlyRenderVisibleElements: boolean;
};

let capturedProps: CapturedProps[] = [];
// Drag-sync rebuild: the drag-position corrections (drag factor, smart-guide
// snap, group cascade) now run as a React Flow CHANGE MIDDLEWARE, inside the
// library's own update, rather than downstream in onNodesChange - see
// SceneCanvas.tsx's dragCorrectionMiddleware doc. The registration hook is
// stubbed here to capture that function, so a test can drive production's
// real sequence (middleware first, then onNodesChange with its output)
// without a working xyflow measurement pipeline underneath, exactly as this
// file already drives the captured props directly.
let capturedMiddleware: ((changes: NodeChange[]) => NodeChange[]) | null = null;

vi.mock("@xyflow/react", async (importOriginal) => {
  const original = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...original,
    experimental_useOnNodesChangeMiddleware: (fn: (changes: NodeChange[]) => NodeChange[]) => {
      capturedMiddleware = fn;
    },
    // ADR-011 stages 11.2/11.3: a thin capture stub, not the real renderer -
    // see this file's own module doc for why. Every prop SceneCanvas passes
    // is recorded verbatim on each render so tests can invoke its handlers
    // directly (onNodesChange, onEdgeMouseEnter/Leave) and assert on its
    // declarative props (onlyRenderVisibleElements, edges) without needing
    // a working xyflow measurement pipeline underneath.
    ReactFlow: (props: CapturedProps) => {
      capturedProps.push(props);
      return null;
    },
  };
});

import { SceneCanvas } from "./SceneCanvas";
import { SceneStore, initialSceneState } from "./sceneStore";
import type { WsTransport } from "../../lib/ws/transport";
import type { SceneNodeRow, SceneState } from "../../lib/bridge-core/generated/scene-state";

type StateListener = (payload: Record<string, unknown>) => void;

function makeWiredStore() {
  const stateListeners = new Map<string, StateListener>();
  const transport = {
    subscribe: vi.fn((topic: string, listener: StateListener) => {
      stateListeners.set(topic, listener);
      return () => stateListeners.delete(topic);
    }),
    intent: vi.fn(),
    fireIntent: vi.fn(),
    subscribePatch: vi.fn(),
    onVersionRejection: vi.fn((_topic: string, listener: (r: null) => void) => {
      listener(null);
      return () => {};
    }),
    setTopicBlocked: vi.fn(),
  } as unknown as WsTransport;
  const store = new SceneStore(transport);
  store.connect();
  return { store, stateListeners };
}

// Minimal fixture - only the fields toFlowNodes actually branches on need
// real values, same "kept minimal on purpose" posture as renderCountGate.
// test.tsx's own chatRow (SceneCanvas.test.tsx's baseNode is the exhaustive
// fixture for when a test needs every field).
function chatRow(id: string, x: number, y = 0): SceneNodeRow {
  return {
    ...(Object.fromEntries(
      Object.entries({
        id, x, y, title: id, kind: "chat", content: "hello", isUser: false,
        isCollapsed: false, code: "", language: "", attachmentKind: "", filePath: "",
        mimeType: "", durationSeconds: null, byteSize: null, previewLabel: "",
        isDocked: false, imageAssetId: "", history: [], pendingRequestId: null,
        researchStage: "", researchCompleted: 0, researchTotal: 0,
        researchActiveSourceId: null, researchError: "", researchResult: null,
        artifactContent: "", gitlinkRepo: "", gitlinkBranch: "",
        gitlinkScopeMode: "selected", gitlinkLocalRoot: "", gitlinkRepoFilePaths: [],
        gitlinkSelectedPaths: [], gitlinkTaskPrompt: "", gitlinkContextStats: {},
        gitlinkContextSummary: "", gitlinkContextVersion: 0,
        gitlinkProposalMarkdown: "", gitlinkPendingChanges: [], gitlinkPreviewText: "",
        gitlinkChangeFingerprint: null, gitlinkChangeState: "", gitlinkError: "",
        pycoderMode: "ai_driven", pycoderPrompt: "", pycoderCode: "",
        pycoderOutput: "", pycoderAnalysis: "", pycoderLastRunFailed: false,
        pycoderAwaitingApproval: false, pycoderError: "",
        codeSandboxRequirements: "", codeSandboxApprovalRequirements: "",
        codeSandboxApprovalAllowSourceBuilds: false, codeSandboxApprovalIsRepair: false,
        codeSandboxPrompt: "", codeSandboxCode: "", codeSandboxOutput: "",
        codeSandboxAnalysis: "", codeSandboxAwaitingApproval: false,
        codeSandboxError: "", provider: null, model: null, isBranchSynthesis: false,
        synthesisInstructions: "", branchStatus: "active", responseIncomplete: false,
        isFinalDeliverable: false,
        color: null, headerColor: null, isSystemPrompt: false, isSummaryNote: false,
        isBranchComparison: false, itemIds: [], isLocked: true, groupWidth: null,
        groupHeight: null, chartType: "", chartData: {}, chartError: "",
        chartWidth: 680.0, chartHeight: 500.0,
        chartAspectLocked: true, chartSourceNodeId: "", htmlSplitterState: null,
        chatScrollValue: 0.0,
        toolCalls: [],
        // ADR-018 stage 18.3
        overrideProvider: "",
        overrideModelId: "",
        // ADR-017 stage 17.5
        indexIntoKnowledge: false,
  planGoal: "",
  planSteps: [],
  builderStatus: "",
  builderMode: "",
  builderRunId: "",
  builderMaxSteps: 0,
  builderMaxTokens: 0,
  builderMaxWallSeconds: 0,
  builderSpentSteps: 0,
  builderSpentTokens: 0,
  builderSpentWallSeconds: 0,
  builderAwaitingToolApproval: false,
  builderApprovalToolName: "",
  builderApprovalSummary: "",
  builderStatusDetail: "",
  // ADR-014 stage 14.2
  pluginState: {},
      }),
    ) as unknown as SceneNodeRow),
  };
}

function mount() {
  const { store, stateListeners } = makeWiredStore();
  capturedProps = [];
  render(
    <ReactFlowProvider>
      <SceneCanvas store={store} onOpenDocumentView={() => {}} />
    </ReactFlowProvider>,
  );
  return { store, stateListeners };
}

function publish(stateListeners: Map<string, StateListener>, scene: Partial<SceneState>) {
  act(() => {
    stateListeners.get("scene")!({ ...initialSceneState, ...scene } as unknown as Record<string, unknown>);
  });
}

function lastProps(): CapturedProps {
  return capturedProps[capturedProps.length - 1];
}

describe("SceneCanvas <ReactFlow> wiring (ADR-011 stages 11.2/11.3)", () => {
  describe("onlyRenderVisibleElements (stage 11.2)", () => {
    it("is on by default", () => {
      const { stateListeners } = mount();
      publish(stateListeners, { nodes: [chatRow("n0", 0)], edges: [] });
      expect(lastProps().onlyRenderVisibleElements).toBe(true);
    });

    it("is suspended for exactly the duration of SceneStore.setExportInProgress(true)", () => {
      const { store, stateListeners } = mount();
      publish(stateListeners, { nodes: [chatRow("n0", 0)], edges: [] });
      expect(lastProps().onlyRenderVisibleElements).toBe(true);

      act(() => store.setExportInProgress(true));
      expect(lastProps().onlyRenderVisibleElements).toBe(false);

      act(() => store.setExportInProgress(false));
      expect(lastProps().onlyRenderVisibleElements).toBe(true);
    });
  });

  describe("edge-hover memo gate (stage 11.3, P4)", () => {
    it("hands React Flow no edges at all - connections are drawn by ConnectionCanvas", () => {
      // The canvas owns connection rendering now (see ConnectionCanvas's own
      // module doc). React Flow's edge machinery is left inert rather than
      // merely invisible, which is what removes it from the drag path.
      const { stateListeners } = mount();
      publish(stateListeners, {
        nodes: [chatRow("a", 0), chatRow("b", 200)],
        edges: [{ id: "e1", source: "a", target: "b" }],
      });
      expect(lastProps().edges).toEqual([]);
    });

    it("keeps the edge model stable across a hover, since fading is applied while drawing", () => {
      // Hover used to rebuild the whole edge array to restyle one edge. The
      // canvas applies the faded-connections lens itself, so the model is a
      // function of the scene alone and a hover cannot churn it.
      const { stateListeners } = mount();
      const scene = {
        nodes: [chatRow("a", 0), chatRow("b", 200)],
        edges: [{ id: "e1", source: "a", target: "b" }],
        fadeConnectionsEnabled: true,
      };
      publish(stateListeners, scene);
      const before = lastProps().edges;
      publish(stateListeners, scene);
      expect(lastProps().edges).toBe(before);
    });
  });

  describe("smart-guide drag-size cache (stage 11.3, P3)", () => {
    it("queries the DOM fallback for the drag gesture's candidates AT MOST ONCE, not once per frame", () => {
      // getInternalNode always empty (no real <ReactFlow> ever mounted its
      // own internal store here - see this file's own module doc), so
      // measuredNodeSize's DOM fallback is hit for every plain "chat" node
      // (no flow-node-level width/height to short-circuit it) - the exact
      // worst case the ADR's own P3 finding describes.
      const querySelectorSpy = vi.spyOn(document, "querySelector");
      const { stateListeners } = mount();
      const nodes = Array.from({ length: 5 }, (_, i) => chatRow(`n${i}`, i * 300));
      publish(stateListeners, { nodes, edges: [], smartGuides: true });

      querySelectorSpy.mockClear();
      // Re-reads lastProps().onNodesChange fresh before EVERY call, not a
      // single cached reference - onNodesChange is recreated (useCallback's
      // own `nodes` dependency changes every setNodes call inside it), and
      // production React Flow always invokes whatever the LATEST prop
      // reference is, exactly like this.
      // Production's real sequence: React Flow runs the registered middleware
      // inside its own update first, then hands the CORRECTED changes to
      // onNodesChange. Driving both in that order keeps this test pinned to
      // the shipping path rather than to a handler in isolation.
      const drag = (change: Partial<NodeChange> & { id: string }) =>
        act(() => {
          const raw = [{ type: "position", ...change } as NodeChange];
          lastProps().onNodesChange(capturedMiddleware ? capturedMiddleware(raw) : raw);
        });

      // Frame 1 of a drag gesture on n0 - this is where the batch cache
      // read happens.
      drag({ id: "n0", dragging: true, position: { x: 10, y: 0 } });
      const afterFrame1 = querySelectorSpy.mock.calls.length;
      expect(afterFrame1).toBeGreaterThan(0);

      // Frames 2 and 3 of the SAME gesture - call count must not scale with
      // the number of simulated frames.
      drag({ id: "n0", dragging: true, position: { x: 20, y: 0 } });
      expect(querySelectorSpy.mock.calls.length).toBe(afterFrame1);
      drag({ id: "n0", dragging: true, position: { x: 30, y: 0 } });
      expect(querySelectorSpy.mock.calls.length).toBe(afterFrame1);

      // Drag end - still no new DOM reads.
      drag({ id: "n0", dragging: false, position: { x: 30, y: 0 } });
      expect(querySelectorSpy.mock.calls.length).toBe(afterFrame1);

      // A NEW drag gesture rebuilds the cache again - proves the cache
      // isn't just frozen forever, only frozen for one gesture at a time.
      drag({ id: "n1", dragging: true, position: { x: 310, y: 0 } });
      expect(querySelectorSpy.mock.calls.length).toBeGreaterThan(afterFrame1);

      querySelectorSpy.mockRestore();
    });

    it("never touches the DOM fallback at all when smartGuides is off", () => {
      const querySelectorSpy = vi.spyOn(document, "querySelector");
      const { stateListeners } = mount();
      const nodes = Array.from({ length: 3 }, (_, i) => chatRow(`n${i}`, i * 300));
      publish(stateListeners, { nodes, edges: [], smartGuides: false });

      querySelectorSpy.mockClear();
      // Production's real sequence: React Flow runs the registered middleware
      // inside its own update first, then hands the CORRECTED changes to
      // onNodesChange. Driving both in that order keeps this test pinned to
      // the shipping path rather than to a handler in isolation.
      const drag = (change: Partial<NodeChange> & { id: string }) =>
        act(() => {
          const raw = [{ type: "position", ...change } as NodeChange];
          lastProps().onNodesChange(capturedMiddleware ? capturedMiddleware(raw) : raw);
        });
      drag({ id: "n0", dragging: true, position: { x: 10, y: 0 } });
      drag({ id: "n0", dragging: true, position: { x: 20, y: 0 } });
      drag({ id: "n0", dragging: false, position: { x: 20, y: 0 } });

      expect(querySelectorSpy).not.toHaveBeenCalled();
      querySelectorSpy.mockRestore();
    });
  });
});
