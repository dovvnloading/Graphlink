/**
 * ADR-019 stage 19.2: the re-renders-per-update CI counting gate.
 *
 * The budget (ADR-019 §2): a single-node update re-renders exactly 1 node
 * view. ADR-011 stage 11.1/11.6 landed the fix - React.memo on every
 * *NodeView.tsx with a field-by-field comparator (chatNodePropsAreEqual and
 * its siblings), plus a stable per-node-id callback dispatcher and a
 * WeakMap-cached flow-node object in toFlowNodes (SceneCanvas.tsx) so an
 * unchanged node's emitted props are reference-/value-stable across scene
 * snapshots. RENDERS_PER_UPDATE_CEILING is tightened to 1 below per that
 * plan (was pinned at NODE_COUNT pre-ADR-011, when there was no React.memo
 * anywhere in the SPA and every flow-node object was rebuilt from scratch on
 * every call).
 *
 * This gate's own single-node update is a DRAG (only `x` changes - see
 * makeScene's `movedNodeX` param) rather than a content edit, which is why
 * the measured value below is 0, not 1: position is passed to a custom node
 * component as `positionAbsoluteX`/`positionAbsoluteY` (see @xyflow/react's
 * NodeWrapper), entirely separate from `data`, and no *NodeView.tsx reads
 * either - the dragged node's on-screen transform is applied by React Flow's
 * OWN wrapper div, never by the memoized content component - so a pure
 * position change legitimately needs zero content re-renders, which is
 * strictly inside (better than) the ADR's "exactly 1" budget for a content
 * change. Verified empirically: swapping the comparator below for a bare
 * `memo()` (default shallow prop compare, no custom comparator) makes this
 * count jump to NODE_COUNT (10) - the WeakMap flow-node cache in
 * toFlowNodes is a per-call cache keyed by SceneNodeRow reference, and this
 * test's own chatRow() fixture mints a brand-new row object for every node
 * on every makeScene() call (even unchanged ones), so EVERY node's flow-node
 * object misses that cache and is rebuilt (with a fresh `data` wrapper
 * object, albeit with the SAME stable callback references from the
 * id-keyed dispatcher cache) on every call - it is chatNodePropsAreEqual's
 * own field-by-field comparison, not `data` reference equality, doing the
 * work of collapsing that back down to 0 here. A regression that removes
 * React.memo, drops the comparator, or reintroduces unstable per-call
 * closures for the callback props would push this back up to NODE_COUNT,
 * so the gate is still exercising the real mechanism ADR-011 landed, not a
 * vacuous scenario.
 *
 *   1. commits per single-node update are pinned at today's measured 4 (one
 *      from SceneCanvas's own setState, three from React Flow's internal
 *      store-sync/measure cascade) - a 5th commit means someone added another
 *      cascading state update on top. Not revised by ADR-011: a drag still
 *      goes through the same setState + React Flow internal store-sync/
 *      measure cascade regardless of whether any node view's CONTENT
 *      re-renders, so this count is orthogonal to the memoization fix.
 *   2. node-view renders for that update are pinned at today's measured 0
 *      (see above) - a regression that pushes this back up (even by 1) means
 *      either an unstable prop reference reappeared somewhere in toFlowNodes/
 *      the dispatcher cache, or a comparator got too loose/removed.
 *
 * Counting mechanism: ChatNodeView is replaced (vi.mock) with a stub that
 * increments a counter per render - counting actual component renders, not
 * Profiler timings, so the assertion is a stable integer, never wall-clock.
 * The stub is wrapped in the SAME memo + SAME real comparator
 * (chatNodePropsAreEqual, exported from ChatNodeView.tsx for this purpose)
 * as production, so this gate gets to skip/no-skip exactly like production
 * would, just against a cheap stub body instead of the full markdown-
 * rendering tree - mocking away the memo wrapper entirely (the pre-ADR-011
 * posture, when there was nothing to preserve) would make this gate measure
 * "does React Flow always re-invoke the node component" (always true),
 * never actually exercising ADR-011's fix. The scene updates arrive through
 * the REAL SceneStore listener path (the same transport-level subscribe
 * callback production uses), not by poking React state directly - so the
 * gate covers the store->React wiring too.
 */
import { memo, Profiler, type ReactNode } from "react";
import { ReactFlowProvider } from "@xyflow/react";
import { act, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const chatNodeRenders = { count: 0 };

vi.mock("./ChatNodeView", async (importOriginal) => {
  const original = await importOriginal<typeof import("./ChatNodeView")>();
  return {
    ...original,
    // ADR-011 stage 11.1/11.6: the real ChatNodeView is now `memo(...,
    // chatNodePropsAreEqual)` (see ChatNodeView.tsx). Stubbing the export
    // with a bare, unmemoized function - as this gate did pre-ADR-011, back
    // when nothing in the SPA was memoized - would silently strip that
    // wrapper back off, making this gate measure "does React Flow always
    // re-invoke the node component" (always true) rather than "does
    // SceneCanvas emit stable-enough props for the real memo to skip a
    // render", which is the entire thing ADR-011 stage 11.1 changed. Wrap
    // the counting stub in the SAME memo + the SAME real comparator
    // (exported from ChatNodeView.tsx specifically so gates like this one
    // can reuse it) so the gate is exercising the real skip/no-skip
    // decision, just against a cheap stub body instead of the full
    // markdown-rendering tree.
    ChatNodeView: memo(() => {
      chatNodeRenders.count += 1;
      return <div data-testid="chat-node-stub" />;
    }, original.chatNodePropsAreEqual),
  };
});

import { SceneCanvas } from "./SceneCanvas";
import { SceneStore, initialSceneState } from "./sceneStore";
import type { WsTransport } from "../../lib/ws/transport";
import type { SceneNodeRow, SceneState } from "../../lib/bridge-core/generated/scene-state";

const NODE_COUNT = 10;
// ADR-011 stage 11.1/11.6 memoization landed (React.memo + stable per-node
// dispatchers on the toFlowNodes side) - tightened from NODE_COUNT to 1.
const RENDERS_PER_UPDATE_CEILING = 1;
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
        researchActiveSourceId: null, researchError: "", researchResult: null, researchRetainToKnowledge: false,
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
        toolCalls: [], // ADR-007 stage 7.4
        overrideProvider: "", // ADR-018 stage 18.3
        overrideModelId: "",
        indexIntoKnowledge: false, // ADR-017 stage 17.5
        planGoal: "",
        planSteps: [],
  builderActivity: [],
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
        harnessGoal: "",
        harnessReply: "",
        harnessStatus: "",
        harnessStatusDetail: "",
        harnessRunId: "",
        harnessActivity: [],
        harnessMaxTurns: 0,
        harnessSpentTurns: 0,
        harnessSpentTokens: 0,
        // ADR-014 stage 14.2
        pluginState: {},
      }),
    ) as unknown as SceneNodeRow),
  };
}

function makeScene(movedNodeX: number | null = null, editedNodeContent: string | null = null): SceneState {
  const nodes = Array.from({ length: NODE_COUNT }, (_, i) => chatRow(`n${i}`, i * 100));
  if (movedNodeX !== null) nodes[0] = { ...nodes[0], x: movedNodeX };
  // `content` (unlike `x` above) IS part of `data` and IS one of the fields
  // chatNodePropsAreEqual compares - this is the knob the "editing one node
  // re-renders one node" test below uses, since a position-only update can
  // never exercise that comparator's real job.
  if (editedNodeContent !== null) nodes[0] = { ...nodes[0], content: editedNodeContent };
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
    // Baseline honesty: with ADR-011 memoization landed, ZERO node views
    // re-render for this update - see the module doc's "why 0, not 1"
    // section above. This update only moves n0's `x`; position is not part
    // of `data` and no *NodeView reads positionAbsoluteX/Y, so even the
    // moved node's own content component correctly has nothing to
    // re-render (React Flow's wrapper div applies the CSS transform on its
    // own, independent of the memoized child). If this starts failing HIGH,
    // memoization regressed; if it starts failing because the number moved
    // to exactly 1 (a real content field changing on the moved node), that
    // would mean this fixture itself changed to mutate content, not
    // position - update this pin to match rather than treating it as a
    // regression.
    expect(chatNodeRenders.count).toBe(0);
  });

  // Review-fix (ADR-019 stage 19.2 self-audit): the drag-only test above
  // measures 0 node re-renders because position is deliberately outside
  // `data` - it never sends a change to any field ChatNodeView actually
  // reads, so it cannot by itself prove the ADR's own literal exit
  // criterion, "editing one node re-renders one node". This test sends a
  // genuine content-changing single-node scene update through the SAME real
  // SceneStore -> SceneCanvas -> toFlowNodes -> ChatNodeView pipeline and
  // asserts exactly 1 of the NODE_COUNT mounted node views re-renders - the
  // missing end-to-end half of that claim (previously only checked in
  // isolated pieces: chatNodePropsAreEqual's own pure-function unit tests,
  // and a real-DOM render check keyed off `selected`, never `content`,
  // never through the store - see ChatNodeView.test.tsx).
  it("a single-node CONTENT edit (the ADR's own literal exit criterion) re-renders exactly 1 node view end-to-end through the real store pipeline", () => {
    const { store, stateListeners } = makeWiredStore();
    mountWithCommitCounter(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );
    act(() => {
      stateListeners.get("scene")!(makeScene() as unknown as Record<string, unknown>);
    });

    chatNodeRenders.count = 0;
    act(() => {
      stateListeners.get("scene")!(
        makeScene(null, "edited content") as unknown as Record<string, unknown>,
      );
    });

    // Unlike the position-only drag above (0 re-renders is correct there -
    // position lives outside `data`), a genuine content edit IS a field
    // chatNodePropsAreEqual compares, so exactly the one edited node's view
    // re-renders and the other NODE_COUNT - 1 unchanged nodes do not.
    expect(chatNodeRenders.count).toBe(1);
    expect(chatNodeRenders.count).toBeLessThanOrEqual(RENDERS_PER_UPDATE_CEILING);
  });
});
