/**
 * ADR-019 stage 19.2: the re-renders-per-update CI counting gate.
 *
 * The budget (ADR-019 §2): a single-node update re-renders exactly 1 node
 * view. Today's reality (audit finding P-class, confirmed by this gate's own
 * baseline measurement): every node view re-renders on every update - there
 * is no React.memo anywhere in the SPA, and a scene snapshot/patch rebuilds
 * every flow-node object so React Flow's own internal memoization never gets
 * reference-equal props. Fixing that is ADR-011's memoization stage; this
 * gate exists so the number can only ever move DOWN in the meantime:
 *
 *   1. commits per single-node update are pinned at today's measured 4 (one
 *      from SceneCanvas's own setState, three from React Flow's internal
 *      store-sync/measure cascade) - a 5th commit means someone added another
 *      cascading state update on top;
 *   2. node-view renders for that update are pinned at today's baseline
 *      (every chat node, = NODE_COUNT) - growing past it means views render
 *      more than once per update.
 *
 * When ADR-011's memoization lands, tighten RENDERS_PER_UPDATE_CEILING to 1
 * (and revisit the commit cascade) - per ADR-019 §4 that is a deliberate
 * amendment tied to that stage, and this comment is the reminder.
 *
 * Counting mechanism: ChatNodeView is replaced (vi.mock) with a stub that
 * increments a counter per render - counting actual component renders, not
 * Profiler timings, so the assertion is a stable integer, never wall-clock.
 * The scene updates arrive through the REAL SceneStore listener path (the
 * same transport-level subscribe callback production uses), not by poking
 * React state directly - so the gate covers the store->React wiring too.
 */
import { Profiler, type ReactNode } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const chatNodeRenders = { count: 0 };

vi.mock("./ChatNodeView", async (importOriginal) => {
  const original = await importOriginal<typeof import("./ChatNodeView")>();
  return {
    ...original,
    ChatNodeView: () => {
      chatNodeRenders.count += 1;
      return <div data-testid="chat-node-stub" />;
    },
  };
});

import { SceneCanvas } from "./SceneCanvas";
import { SceneStore, initialSceneState } from "./sceneStore";
import type { WsTransport } from "../../lib/ws/transport";
import type { SceneNodeRow, SceneState } from "../../lib/bridge-core/generated/scene-state";

const NODE_COUNT = 10;
// Today's baseline: every node re-renders on a single-node update (no memo
// anywhere - see module doc). ADR-011 tightens this to 1.
const RENDERS_PER_UPDATE_CEILING = NODE_COUNT;
// Today's measured cascade (see module doc): 1 SceneCanvas commit + 3 React
// Flow internal store-sync/measure commits. The gate holds this from GROWING.
const COMMITS_PER_UPDATE_CEILING = 4;

function chatRow(id: string, x: number): SceneNodeRow {
  // Only the fields toFlowNodes/ChatNodeView actually branch on need real
  // values; everything else takes the wire default. Kept minimal on purpose -
  // SceneCanvas.test.tsx's own baseNode() is the exhaustive fixture.
  return {
    ...(Object.fromEntries(
      Object.entries({
        id, x, y: 0, title: id, kind: "chat", content: "hello", isUser: false,
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
        chartAssetId: "", chartAssetVersion: 0, chartWidth: 680.0, chartHeight: 500.0,
        chartAspectLocked: true, chartSourceNodeId: "", htmlSplitterState: null,
        chatScrollValue: 0.0,
      }),
    ) as unknown as SceneNodeRow),
  };
}

function makeScene(movedNodeX: number | null = null): SceneState {
  const nodes = Array.from({ length: NODE_COUNT }, (_, i) => chatRow(`n${i}`, i * 100));
  if (movedNodeX !== null) nodes[0] = { ...nodes[0], x: movedNodeX };
  return { ...initialSceneState, nodes, edges: [] };
}

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

function mountWithCommitCounter(children: ReactNode) {
  const commits = { count: 0 };
  render(
    <Profiler
      id="render-count-gate"
      onRender={() => {
        commits.count += 1;
      }}
    >
      {children}
    </Profiler>,
  );
  return commits;
}

describe("re-renders per single-node update (ADR-019 stage 19.2)", () => {
  it("finds real renders at all (guards the guard)", () => {
    const { store, stateListeners } = makeWiredStore();
    mountWithCommitCounter(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );
    act(() => {
      stateListeners.get("scene")!(makeScene() as unknown as Record<string, unknown>);
    });
    // The stub actually rendered once per node on mount - a broken mock or a
    // React Flow harness change would make the gate below pass vacuously.
    expect(chatNodeRenders.count).toBeGreaterThanOrEqual(NODE_COUNT);
  });

  it("a single-node update stays within the pinned commit and render baselines", () => {
    const { store, stateListeners } = makeWiredStore();
    const commits = mountWithCommitCounter(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );
    act(() => {
      stateListeners.get("scene")!(makeScene() as unknown as Record<string, unknown>);
    });

    chatNodeRenders.count = 0;
    commits.count = 0;
    act(() => {
      stateListeners.get("scene")!(makeScene(9999) as unknown as Record<string, unknown>);
    });

    expect(commits.count).toBeLessThanOrEqual(COMMITS_PER_UPDATE_CEILING);
    expect(chatNodeRenders.count).toBeLessThanOrEqual(RENDERS_PER_UPDATE_CEILING);
    // Baseline honesty: today it IS the ceiling (all nodes re-render). If
    // this starts failing LOW, memoization landed - tighten the ceiling to 1
    // per ADR-011 instead of deleting the assertion.
    expect(chatNodeRenders.count).toBe(NODE_COUNT);
  });
});
