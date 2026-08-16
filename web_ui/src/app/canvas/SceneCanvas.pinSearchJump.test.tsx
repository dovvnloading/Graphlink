/**
 * ADR-011 stage 11.2 virtualization audit - "Search/pin-highlight logic":
 * does jumping to a pin/search match already pan the viewport BEFORE
 * expecting the target node to be interactable, or does it need a fix now
 * that onlyRenderVisibleElements can leave an off-viewport node unmounted?
 *
 * Verified, not fixed: PinOverlay.tsx/SearchOverlay.tsx both call React
 * Flow's setCenter with the target's own SCENE x/y (never a DOM-measured
 * position, never gated on the target already being mounted) - see
 * SceneCanvas.tsx's own <ReactFlow onlyRenderVisibleElements> comment for
 * the full audit summary. Panning the viewport is what MAKES a node mount
 * under virtualization, not something that requires it already being
 * mounted - so this was already correct before stage 11.2, and this file
 * is the regression test proving it stays that way with virtualization
 * actually turned on.
 *
 * Asserts on setCenter's CALL ARGUMENTS, not the settled viewport transform:
 * both call sites pass `{ duration: 300 }`, an ANIMATED d3-zoom transition,
 * not an instant jump - reading the live transform right after the click
 * would race that animation (confirmed empirically: it reads back
 * {x:0,y:0,zoom:1}, i.e. the animation's FIRST frame, not its result).
 * Spying on the exact call is deterministic and avoids wall-clock/rAF
 * timing entirely, matching this codebase's own established preference for
 * call-count/spy assertions over timing-dependent ones (renderCountGate.
 * test.tsx's own module doc). useReactFlow is wrapped (not fully mocked -
 * every other export, and the underlying setCenter behavior, stays real)
 * so BOTH the audited claim ("was setCenter actually called, with the
 * target's own x/y") and the real <ReactFlow onlyRenderVisibleElements>
 * mounted alongside it are exercised in the same test.
 */
import { ReactFlowProvider, type useReactFlow as UseReactFlowType } from "@xyflow/react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

type SetCenterCall = [number, number, { zoom?: number; duration?: number } | undefined];
const setCenterCalls: SetCenterCall[] = [];

vi.mock("@xyflow/react", async (importOriginal) => {
  const original = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...original,
    // Wraps the REAL useReactFlow so every consumer (PinOverlay,
    // SearchOverlay, SceneCanvas's own CanvasInner) still gets fully
    // functional pan/zoom/getNodes/etc. - only setCenter is intercepted,
    // and it still calls straight through to the real implementation.
    useReactFlow: (...args: Parameters<typeof UseReactFlowType>) => {
      const real = original.useReactFlow(...args);
      return {
        ...real,
        setCenter: (x: number, y: number, options?: { zoom?: number; duration?: number }) => {
          setCenterCalls.push([x, y, options]);
          return real.setCenter(x, y, options);
        },
      };
    },
  };
});

import { CanvasSearchProvider } from "./CanvasSearchContext";
import { SceneCanvas } from "./SceneCanvas";
import { SceneStore, initialSceneState } from "./sceneStore";
import { PinOverlay } from "../chrome/PinOverlay";
import { SearchOverlay } from "../chrome/SearchOverlay";
import { OverlayProvider, useOverlays } from "../overlays/overlays";
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

// Minimal fixture, same "kept minimal on purpose" posture as
// renderCountGate.test.tsx's own chatRow.
function chatRow(id: string, x: number, y: number, title = id): SceneNodeRow {
  return {
    ...(Object.fromEntries(
      Object.entries({
        id, x, y, title, kind: "chat", content: "hello world", isUser: false,
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
  // ADR-014 stage 14.2
  pluginState: {},
      }),
    ) as unknown as SceneNodeRow),
  };
}

function publish(stateListeners: Map<string, StateListener>, scene: Partial<SceneState>) {
  act(() => {
    stateListeners.get("scene")!({ ...initialSceneState, ...scene } as unknown as Record<string, unknown>);
  });
}

// Same "force the popover open through the real overlay context" posture as
// PinOverlay.test.tsx's own OpenPinsOnMount - both PinOverlay and
// SearchOverlay only mount their real content while their own surface is
// open.
function OpenSurfaceOnMount({ name, children }: { name: "pins" | "search"; children: React.ReactNode }) {
  const overlays = useOverlays();
  if (!overlays.isOpen(name)) overlays.open(name, "popover");
  return <>{children}</>;
}

describe("pin/search jump navigates to an off-viewport target with onlyRenderVisibleElements on (ADR-011 stage 11.2 audit)", () => {
  beforeEach(() => {
    setCenterCalls.length = 0;
  });

  it("PinOverlay: clicking a pin far outside the default viewport calls setCenter with the pin's own coordinates", async () => {
    const user = userEvent.setup();
    const { store, stateListeners } = makeWiredStore();
    const FAR_X = 50000;
    const FAR_Y = 40000;

    render(
      <OverlayProvider>
        <ReactFlowProvider>
          <SceneCanvas store={store} onOpenDocumentView={() => {}} />
          <OpenSurfaceOnMount name="pins">
            <PinOverlay store={store} />
          </OpenSurfaceOnMount>
        </ReactFlowProvider>
      </OverlayProvider>,
    );

    publish(stateListeners, {
      // A node parked at the exact same far-away spot as the pin - the
      // scenario onlyRenderVisibleElements is meant to leave unmounted
      // until the viewport pans there.
      nodes: [chatRow("far-node", FAR_X, FAR_Y)],
      edges: [],
      pins: [{ id: "p1", title: "Far Away", note: "", x: FAR_X, y: FAR_Y }],
    });

    expect(setCenterCalls).toHaveLength(0);
    await user.click(screen.getByText("Far Away"));

    // Called exactly once, with the PIN's own scene coordinates - not
    // anything derived from the target node's DOM/mounted state (there is
    // none to derive from: the click fires setCenter unconditionally).
    expect(setCenterCalls).toHaveLength(1);
    const [x, y, options] = setCenterCalls[0];
    expect(x).toBe(FAR_X);
    expect(y).toBe(FAR_Y);
    expect(options?.zoom).toBe(1);
  });

  it("SearchOverlay: jumping to a match far outside the default viewport calls setCenter with the match's own coordinates", async () => {
    const user = userEvent.setup();
    const { store, stateListeners } = makeWiredStore();
    const FAR_X = -30000;
    const FAR_Y = 60000;

    render(
      <OverlayProvider>
        <ReactFlowProvider>
          <CanvasSearchProvider>
            <SceneCanvas store={store} onOpenDocumentView={() => {}} />
            <OpenSurfaceOnMount name="search">
              <SearchOverlay store={store} />
            </OpenSurfaceOnMount>
          </CanvasSearchProvider>
        </ReactFlowProvider>
      </OverlayProvider>,
    );

    publish(stateListeners, {
      nodes: [chatRow("far-node", FAR_X, FAR_Y, "UniqueSearchTarget")],
      edges: [],
    });

    expect(setCenterCalls).toHaveLength(0);
    await user.type(screen.getByLabelText("Search the canvas"), "UniqueSearchTarget");
    await user.click(screen.getByLabelText("Next match (Enter)"));

    expect(setCenterCalls).toHaveLength(1);
    const [x, y, options] = setCenterCalls[0];
    expect(x).toBe(FAR_X);
    expect(y).toBe(FAR_Y);
    expect(options?.zoom).toBe(1);
  });
});
