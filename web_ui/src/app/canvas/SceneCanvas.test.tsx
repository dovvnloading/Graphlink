import { ReactFlowProvider } from "@xyflow/react";
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  applyGroupDragDelta,
  buildDragSizeCache,
  computeDimmedNodeIds,
  computeFilteredOutNodeIds,
  computeNonAcceptedNodeIds,
  computeSmartGuideFrame,
  conversationHistoryToDocumentMarkdown,
  createToFlowNodesCache,
  FILTERABLE_NODE_KINDS,
  flowNodeOwnSize,
  groupDragKindOf,
  handleSelectionChange,
  isOrthogonalEligible,
  collectChangedNodeSizes,
  makeDebouncedViewportReport,
  SceneCanvas,
  toFlowEdges,
  toFlowNodes,
  withPreservedFlowState,
  type MeasuredSizeSource,
  type SceneFlowNode,
} from "./SceneCanvas";
import type { ConversationMessage } from "./ConversationNodeView";
import { SceneStore, initialSceneState } from "./sceneStore";
import type { WsTransport } from "../../lib/ws/transport";
import type { BridgeRejection } from "../../lib/bridge-core/islandState";
import type { SceneNodeRow, SceneState } from "../../lib/bridge-core/generated/scene-state";

// toFlowNodes is exported standalone specifically so this doesn't need a
// full <ReactFlow> mount (same reasoning as sceneStore.test.ts's direct
// drag coverage) - see SceneCanvas.tsx's own comment on the
// export.

function makeStore(): SceneStore {
  // ADR-003 stage 3.1: fireIntent is the transport method SceneStore's own
  // mutating intent call sites actually use now - see sceneStore.ts's own
  // module doc.
  const transport = { subscribe: vi.fn(), intent: vi.fn(), fireIntent: vi.fn() } as unknown as WsTransport;
  return new SceneStore(transport);
}

function baseNode(overrides: Partial<SceneNodeRow> = {}): SceneNodeRow {
  return {
    id: "n0",
    x: 0,
    y: 0,
    title: "",
    kind: "placeholder",
    content: "",
    isUser: false,
    isCollapsed: false,
    code: "",
    language: "",
    attachmentKind: "",
    filePath: "",
    mimeType: "",
    durationSeconds: null,
    byteSize: null,
    previewLabel: "",
    isDocked: false,
    imageAssetId: "",
    history: [],
    pendingRequestId: null,
    researchStage: "",
    researchCompleted: 0,
    researchTotal: 0,
    researchActiveSourceId: null,
    researchError: "",
    researchResult: null,
    researchRetainToKnowledge: false,
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
    gitlinkChangeFingerprint: null,
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
    provider: null,
    model: null,
    isBranchSynthesis: false,
    synthesisInstructions: "",
    branchStatus: "active",
    // ADR-006 stage 6.4
    responseIncomplete: false,
    isFinalDeliverable: false,
    color: null,
    headerColor: null,
    isSystemPrompt: false,
    isSummaryNote: false,
    isBranchComparison: false,
    itemIds: [],
    isLocked: true,
    groupWidth: null,
    groupHeight: null,
    chartType: "",
    chartData: {},
    chartError: "",
    chartWidth: 680.0,
    chartHeight: 500.0,
    chartAspectLocked: true,
    chartSourceNodeId: "",
    htmlSplitterState: null,
    chatScrollValue: 0.0,
    // ADR-007 stage 7.4
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
    ...overrides,
  };
}

function baseScene(overrides: Partial<SceneState> = {}): SceneState {
  return {
    ...initialSceneState,
    ...overrides,
  };
}

describe("toFlowNodes (R4.3c parentChatNodeId derivation)", () => {
  it("a code node with a parent edge yields the correct parentChatNodeId, and its onRegenerate calls regenerateResponse with that id", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "chat-1", kind: "chat", content: "Hello" }),
        baseNode({ id: "code-1", kind: "code", code: "print(1)", language: "python" }),
      ],
      edges: [{ id: "e1", source: "chat-1", target: "code-1" }],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "regenerateResponse");

    const flowNodes = toFlowNodes(scene, store);
    const codeFlowNode = flowNodes.find((n) => n.id === "code-1");
    expect(codeFlowNode).toBeDefined();
    expect((codeFlowNode!.data as { parentChatNodeId: string | null }).parentChatNodeId).toBe("chat-1");

    (codeFlowNode!.data as { onRegenerate: () => void }).onRegenerate();
    expect(intentSpy).toHaveBeenCalledWith("chat-1");
  });

  it("a code node with no parent edge yields parentChatNodeId: null, and its onRegenerate is a no-op", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "code-orphan", kind: "code", code: "print(1)", language: "python" })],
      edges: [],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "regenerateResponse");

    const flowNodes = toFlowNodes(scene, store);
    const codeFlowNode = flowNodes.find((n) => n.id === "code-orphan");
    expect(codeFlowNode).toBeDefined();
    expect((codeFlowNode!.data as { parentChatNodeId: string | null }).parentChatNodeId).toBeNull();

    (codeFlowNode!.data as { onRegenerate: () => void }).onRegenerate();
    expect(intentSpy).not.toHaveBeenCalled();
  });

  it("a chat node's onRegenerate calls regenerateResponse with its own id", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello", isUser: false })],
      edges: [],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "regenerateResponse");

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode).toBeDefined();

    (chatFlowNode!.data as { onRegenerate: () => void }).onRegenerate();
    expect(intentSpy).toHaveBeenCalledWith("chat-1");
  });
});

describe("toFlowNodes (R4.4a Generate/Regenerate Image wiring)", () => {
  it("a chat node's onGenerateImage calls generateImage with its own id", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello", isUser: true })],
      edges: [],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "generateImage");

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode).toBeDefined();

    (chatFlowNode!.data as { onGenerateImage: () => void }).onGenerateImage();
    expect(intentSpy).toHaveBeenCalledWith("chat-1");
  });

  it("a chat node's onGenerateChart calls generateChart with [its own id, the chartType argument] (R6.2)", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello", isUser: true })],
      edges: [],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "generateChart");

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode).toBeDefined();

    (chatFlowNode!.data as { onGenerateChart: (chartType: string) => void }).onGenerateChart("sankey");
    expect(intentSpy).toHaveBeenCalledWith("chat-1", "sankey");
  });

  it("an image node's onRegenerate calls regenerateImage with its own id - no client-side parent lookup, unlike CodeNode's onRegenerate", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "chat-1", kind: "chat", content: "Hello" }),
        baseNode({ id: "image-1", kind: "image", imageAssetId: "asset-1", content: "a red fox" }),
      ],
      edges: [{ id: "e1", source: "chat-1", target: "image-1" }],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "regenerateImage");

    const flowNodes = toFlowNodes(scene, store);
    const imageFlowNode = flowNodes.find((n) => n.id === "image-1");
    expect(imageFlowNode).toBeDefined();

    (imageFlowNode!.data as { onRegenerate: () => void }).onRegenerate();
    expect(intentSpy).toHaveBeenCalledWith("image-1");
  });
});

describe("conversationHistoryToDocumentMarkdown (R8a Open Document View transcript formatter)", () => {
  it("formats two messages with 1-based numbered headings, joined by a blank line", () => {
    const history: ConversationMessage[] = [
      { role: "user", content: "hi", incomplete: false },
      { role: "assistant", content: "hello there", incomplete: false },
    ];
    expect(conversationHistoryToDocumentMarkdown(history)).toBe(
      "## Conversation Transcript\n\n### 1. User\n\nhi\n\n### 2. Assistant\n\nhello there",
    );
  });

  it("skips a blank message but its number still counts (legacy enumerate-before-filter behavior)", () => {
    const history: ConversationMessage[] = [
      { role: "user", content: "first", incomplete: false },
      { role: "assistant", content: "   ", incomplete: false },
      { role: "user", content: "third", incomplete: false },
    ];
    const result = conversationHistoryToDocumentMarkdown(history);
    expect(result).toBe("## Conversation Transcript\n\n### 1. User\n\nfirst\n\n### 3. User\n\nthird");
    expect(result).not.toContain("### 2.");
  });

  it("returns an empty string for an empty history", () => {
    expect(conversationHistoryToDocumentMarkdown([])).toBe("");
  });

  it("returns an empty string when every message is blank", () => {
    const history: ConversationMessage[] = [
      { role: "user", content: "", incomplete: false },
      { role: "assistant", content: "   ", incomplete: false },
    ];
    expect(conversationHistoryToDocumentMarkdown(history)).toBe("");
  });
});

describe("toFlowNodes (R8a Open Document View wiring)", () => {
  it("a chat node's onOpenDocumentView invokes the third-argument callback with its own content", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello world" })],
      edges: [],
    });
    const store = makeStore();
    const onOpenDocumentView = vi.fn();

    const flowNodes = toFlowNodes(scene, store, onOpenDocumentView);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode).toBeDefined();

    (chatFlowNode!.data as { onOpenDocumentView: () => void }).onOpenDocumentView();
    expect(onOpenDocumentView).toHaveBeenCalledWith("Hello world", "Assistant message");
  });

  it("a chat node's onBranchFromHere calls store.setReplyTargetNodeId with its own id (ADR-002 Workstream 1)", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello world" })],
      edges: [],
    });
    const store = makeStore();
    const setReplyTargetSpy = vi.spyOn(store, "setReplyTargetNodeId");
    const onOpenDocumentView = vi.fn();

    const flowNodes = toFlowNodes(scene, store, onOpenDocumentView);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode).toBeDefined();

    (chatFlowNode!.data as { onBranchFromHere: () => void }).onBranchFromHere();
    expect(setReplyTargetSpy).toHaveBeenCalledWith("chat-1");
  });

  it(
    "a chat node's onPinToCurrentModel reads getComposerRoute at click time and calls " +
      "store.setModelOverride (ADR-018 stage 18.3)",
    () => {
      const scene = baseScene({
        nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello world" })],
        edges: [],
      });
      const store = makeStore();
      const setModelOverrideSpy = vi.spyOn(store, "setModelOverride");
      const getComposerRoute = vi.fn(() => ({ provider: "Anthropic Claude", modelId: "claude-opus-5" }));

      const flowNodes = toFlowNodes(
        scene, store, () => {}, null, () => {}, false, createToFlowNodesCache(), getComposerRoute,
      );
      const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");

      (chatFlowNode!.data as { onPinToCurrentModel: () => void }).onPinToCurrentModel();
      expect(getComposerRoute).toHaveBeenCalledOnce();
      expect(setModelOverrideSpy).toHaveBeenCalledWith("chat-1", "Anthropic Claude", "claude-opus-5");
    },
  );

  it("onPinToCurrentModel is a genuine no-op when the composer route has no provider/model resolved yet", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello world" })],
      edges: [],
    });
    const store = makeStore();
    const setModelOverrideSpy = vi.spyOn(store, "setModelOverride");
    const getComposerRoute = vi.fn(() => ({ provider: "", modelId: "" }));

    const flowNodes = toFlowNodes(
      scene, store, () => {}, null, () => {}, false, createToFlowNodesCache(), getComposerRoute,
    );
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");

    (chatFlowNode!.data as { onPinToCurrentModel: () => void }).onPinToCurrentModel();
    expect(setModelOverrideSpy).not.toHaveBeenCalled();
  });

  it("a chat node's onClearModelOverride calls store.clearModelOverride with its own id (ADR-018 stage 18.3)", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello world" })],
      edges: [],
    });
    const store = makeStore();
    const clearModelOverrideSpy = vi.spyOn(store, "clearModelOverride");

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");

    (chatFlowNode!.data as { onClearModelOverride: () => void }).onClearModelOverride();
    expect(clearModelOverrideSpy).toHaveBeenCalledWith("chat-1");
  });

  it("a chat node's onOpenDocumentView labels the source as the user's own message when isUser is true", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello world", isUser: true })],
      edges: [],
    });
    const store = makeStore();
    const onOpenDocumentView = vi.fn();

    const flowNodes = toFlowNodes(scene, store, onOpenDocumentView);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");

    (chatFlowNode!.data as { onOpenDocumentView: () => void }).onOpenDocumentView();
    expect(onOpenDocumentView).toHaveBeenCalledWith("Hello world", "Your message");
  });

  it("a chat node with blank/whitespace-only content does NOT invoke the callback, and shows a notification instead", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "   " })],
      edges: [],
    });
    const store = makeStore();
    const notifySpy = vi.spyOn(store, "showInfoNotification");
    const onOpenDocumentView = vi.fn();

    const flowNodes = toFlowNodes(scene, store, onOpenDocumentView);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode).toBeDefined();

    (chatFlowNode!.data as { onOpenDocumentView: () => void }).onOpenDocumentView();
    expect(onOpenDocumentView).not.toHaveBeenCalled();
    expect(notifySpy).toHaveBeenCalledWith("No document view content is available for this node yet.");
  });

  it("a conversation node's onOpenDocumentView invokes the callback with the properly formatted transcript", () => {
    const history: ConversationMessage[] = [
      { role: "user", content: "hi", incomplete: false },
      { role: "assistant", content: "hello there", incomplete: false },
    ];
    const scene = baseScene({
      nodes: [baseNode({ id: "conv-1", kind: "conversation", history })],
      edges: [],
    });
    const store = makeStore();
    const onOpenDocumentView = vi.fn();

    const flowNodes = toFlowNodes(scene, store, onOpenDocumentView);
    const conversationFlowNode = flowNodes.find((n) => n.id === "conv-1");
    expect(conversationFlowNode).toBeDefined();

    (conversationFlowNode!.data as { onOpenDocumentView: () => void }).onOpenDocumentView();
    expect(onOpenDocumentView).toHaveBeenCalledWith(
      "## Conversation Transcript\n\n### 1. User\n\nhi\n\n### 2. Assistant\n\nhello there",
      "Conversation transcript",
    );
  });

  it("a conversation node with an empty history does NOT invoke the callback, and shows a notification instead", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "conv-1", kind: "conversation", history: [] })],
      edges: [],
    });
    const store = makeStore();
    const notifySpy = vi.spyOn(store, "showInfoNotification");
    const onOpenDocumentView = vi.fn();

    const flowNodes = toFlowNodes(scene, store, onOpenDocumentView);
    const conversationFlowNode = flowNodes.find((n) => n.id === "conv-1");
    expect(conversationFlowNode).toBeDefined();

    (conversationFlowNode!.data as { onOpenDocumentView: () => void }).onOpenDocumentView();
    expect(onOpenDocumentView).not.toHaveBeenCalled();
    expect(notifySpy).toHaveBeenCalledWith("No document view content is available for this node yet.");
  });

  it("toFlowNodes called with only two arguments (the existing ~50 call sites in this file) still compiles and does not throw when onOpenDocumentView would otherwise fire", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello world" })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode).toBeDefined();

    expect(() => (chatFlowNode!.data as { onOpenDocumentView: () => void }).onOpenDocumentView()).not.toThrow();
  });
});

describe("toFlowNodes (R5.1 web_research node)", () => {
  it("maps a web_research scene node's all 6 new fields onto the flow node's data", () => {
    const researchResult = {
      requestId: "req-1",
      originalQuery: "who won the 2019 world series",
      effectiveQuery: "2019 world series winner",
      answerMarkdown: "The **Washington Nationals** won.",
      sources: [
        {
          sourceId: "src-1",
          title: "2019 World Series",
          url: "https://example.com/2019-ws",
          canonicalUrl: "https://example.com/2019-ws",
          snippet: "...",
          rank: 1,
          provider: "search",
          finalUrl: "https://example.com/2019-ws",
          status: "accepted",
          errorCode: "",
          errorMessage: "",
          truncated: false,
          contentHash: "abc",
          citationCount: 1,
        },
      ],
      citations: [{ sourceId: "src-1", marker: "[1]", claimContext: "won the series" }],
      warnings: ["One source was truncated."],
      providerSnapshot: {},
    };
    const scene = baseScene({
      nodes: [
        baseNode({
          id: "wr-1",
          kind: "web_research",
          content: "who won the 2019 world series",
          isCollapsed: true,
          pendingRequestId: "req-1",
          researchStage: "fetching",
          researchCompleted: 2,
          researchTotal: 5,
          researchActiveSourceId: "src-2",
          researchError: "",
          researchResult,
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const wrFlowNode = flowNodes.find((n) => n.id === "wr-1");
    expect(wrFlowNode).toBeDefined();
    expect(wrFlowNode!.type).toBe("web_research");
    expect(wrFlowNode!.data).toMatchObject({
      query: "who won the 2019 world series",
      isCollapsed: true,
      pendingRequestId: "req-1",
      researchStage: "fetching",
      researchCompleted: 2,
      researchTotal: 5,
      researchActiveSourceId: "src-2",
      researchError: "",
      researchResult,
    });
  });

  it("coalesces null-ish optional fields (pendingRequestId/researchActiveSourceId/researchResult) to null", () => {
    const scene = baseScene({
      nodes: [
        baseNode({
          id: "wr-2",
          kind: "web_research",
          content: "a fresh query",
          researchStage: "",
          researchCompleted: 0,
          researchTotal: 0,
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const wrFlowNode = flowNodes.find((n) => n.id === "wr-2");
    expect(wrFlowNode).toBeDefined();
    expect(wrFlowNode!.data).toMatchObject({
      pendingRequestId: null,
      researchActiveSourceId: null,
      researchResult: null,
    researchRetainToKnowledge: false,
    });
  });

  it("onRun calls store.runWebResearch with this node's id and the given query", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "wr-1", kind: "web_research" })], edges: [] });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "runWebResearch");

    const flowNodes = toFlowNodes(scene, store);
    const wrFlowNode = flowNodes.find((n) => n.id === "wr-1");

    (wrFlowNode!.data as { onRun: (query: string) => void }).onRun("a new question");
    expect(intentSpy).toHaveBeenCalledWith("wr-1", "a new question");
  });

  it("onCancel fires cancelWebResearchRequest with pendingRequestId when set, and is a no-op otherwise", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "wr-pending", kind: "web_research", pendingRequestId: "req-77" }),
        baseNode({ id: "wr-idle", kind: "web_research", pendingRequestId: null }),
      ],
      edges: [],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "cancelWebResearchRequest");

    const flowNodes = toFlowNodes(scene, store);
    const pendingNode = flowNodes.find((n) => n.id === "wr-pending");
    const idleNode = flowNodes.find((n) => n.id === "wr-idle");

    (pendingNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledWith("req-77");

    (idleNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledTimes(1);
  });

  it("onToggleCollapse/onDelete reuse the generic setChatCollapsed/removeNodes intents", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "wr-1", kind: "web_research", isCollapsed: false })],
      edges: [],
    });
    const store = makeStore();
    const collapseSpy = vi.spyOn(store, "setChatCollapsed");
    const removeSpy = vi.spyOn(store, "removeNodes");

    const flowNodes = toFlowNodes(scene, store);
    const wrFlowNode = flowNodes.find((n) => n.id === "wr-1");

    (wrFlowNode!.data as { onToggleCollapse: () => void }).onToggleCollapse();
    expect(collapseSpy).toHaveBeenCalledWith("wr-1", true);

    (wrFlowNode!.data as { onDelete: () => void }).onDelete();
    expect(removeSpy).toHaveBeenCalledWith(["wr-1"]);
  });
});

describe("toFlowNodes (R5.2 artifact node)", () => {
  it("maps an artifact scene node's artifactContent/history/isCollapsed onto the flow node's data", () => {
    const history = [
      { role: "user" as const, content: "Draft a project proposal", incomplete: false },
      { role: "assistant" as const, content: "# Proposal\n\nHere is a draft.", incomplete: false },
    ];
    const scene = baseScene({
      nodes: [
        baseNode({
          id: "art-1",
          kind: "artifact",
          artifactContent: "# Proposal\n\nHere is a draft.",
          history,
          isCollapsed: true,
          pendingRequestId: "req-1",
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const artifactFlowNode = flowNodes.find((n) => n.id === "art-1");
    expect(artifactFlowNode).toBeDefined();
    expect(artifactFlowNode!.type).toBe("artifact");
    expect(artifactFlowNode!.data).toMatchObject({
      artifactContent: "# Proposal\n\nHere is a draft.",
      history,
      isCollapsed: true,
      pendingRequestId: "req-1",
    });
  });

  it("coalesces a null-ish pendingRequestId to null", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "art-2", kind: "artifact", artifactContent: "", history: [] })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const artifactFlowNode = flowNodes.find((n) => n.id === "art-2");
    expect(artifactFlowNode).toBeDefined();
    expect(artifactFlowNode!.data).toMatchObject({ pendingRequestId: null });
  });

  it("onSubmit calls store.sendArtifactMessage with this node's id and the given text", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "art-1", kind: "artifact" })], edges: [] });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "sendArtifactMessage");

    const flowNodes = toFlowNodes(scene, store);
    const artifactFlowNode = flowNodes.find((n) => n.id === "art-1");

    (artifactFlowNode!.data as { onSubmit: (text: string) => void }).onSubmit("Refine the intro");
    expect(intentSpy).toHaveBeenCalledWith("art-1", "Refine the intro");
  });

  it("onCancel fires cancelArtifactRequest with pendingRequestId when set, and is a no-op otherwise", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "art-pending", kind: "artifact", pendingRequestId: "req-77" }),
        baseNode({ id: "art-idle", kind: "artifact", pendingRequestId: null }),
      ],
      edges: [],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "cancelArtifactRequest");

    const flowNodes = toFlowNodes(scene, store);
    const pendingNode = flowNodes.find((n) => n.id === "art-pending");
    const idleNode = flowNodes.find((n) => n.id === "art-idle");

    (pendingNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledWith("req-77");

    (idleNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledTimes(1);
  });

  it("onToggleCollapse/onDelete reuse the generic setChatCollapsed/removeNodes intents", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "art-1", kind: "artifact", isCollapsed: false })],
      edges: [],
    });
    const store = makeStore();
    const collapseSpy = vi.spyOn(store, "setChatCollapsed");
    const removeSpy = vi.spyOn(store, "removeNodes");

    const flowNodes = toFlowNodes(scene, store);
    const artifactFlowNode = flowNodes.find((n) => n.id === "art-1");

    (artifactFlowNode!.data as { onToggleCollapse: () => void }).onToggleCollapse();
    expect(collapseSpy).toHaveBeenCalledWith("art-1", true);

    (artifactFlowNode!.data as { onDelete: () => void }).onDelete();
    expect(removeSpy).toHaveBeenCalledWith(["art-1"]);
  });
});

describe("toFlowNodes (R5.3 gitlink node)", () => {
  it("maps a gitlink scene node's all 15 new fields onto the flow node's data - and gitlinkContextXml is never read (not part of the wire payload)", () => {
    const pendingChanges = [
      { path: "src/a.py", operation: "modify", reason: "add health check", content: "print(1)" },
    ];
    const scene = baseScene({
      nodes: [
        baseNode({
          id: "gl-1",
          kind: "gitlink",
          isCollapsed: true,
          pendingRequestId: "req-1",
          gitlinkRepo: "owner/repo",
          gitlinkBranch: "main",
          gitlinkScopeMode: "selected",
          gitlinkLocalRoot: "C:/repos/repo",
          gitlinkRepoFilePaths: ["src/a.py", "src/b.py"],
          gitlinkSelectedPaths: ["src/a.py"],
          gitlinkTaskPrompt: "Add a health-check endpoint",
          gitlinkContextStats: { files: "2", tokens: "512" },
          gitlinkContextSummary: "2 files, 512 tokens",
          gitlinkContextVersion: 3,
          gitlinkProposalMarkdown: "# Proposal",
          gitlinkPendingChanges: pendingChanges,
          gitlinkPreviewText: "--- a/src/a.py\n+++ b/src/a.py",
          gitlinkChangeFingerprint: "fp-1",
          gitlinkChangeState: "previewed",
          gitlinkError: "",
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const glFlowNode = flowNodes.find((n) => n.id === "gl-1");
    expect(glFlowNode).toBeDefined();
    expect(glFlowNode!.type).toBe("gitlink");
    expect(glFlowNode!.data).toMatchObject({
      gitlinkRepo: "owner/repo",
      gitlinkBranch: "main",
      gitlinkScopeMode: "selected",
      gitlinkLocalRoot: "C:/repos/repo",
      gitlinkRepoFilePaths: ["src/a.py", "src/b.py"],
      gitlinkSelectedPaths: ["src/a.py"],
      gitlinkTaskPrompt: "Add a health-check endpoint",
      gitlinkContextStats: { files: "2", tokens: "512" },
      gitlinkContextSummary: "2 files, 512 tokens",
      gitlinkContextVersion: 3,
      gitlinkProposalMarkdown: "# Proposal",
      gitlinkPendingChanges: pendingChanges,
      gitlinkPreviewText: "--- a/src/a.py\n+++ b/src/a.py",
      gitlinkChangeFingerprint: "fp-1",
      gitlinkChangeState: "previewed",
      gitlinkError: "",
      isCollapsed: true,
      pendingRequestId: "req-1",
    });
    // gitlinkContextXml genuinely is not part of SceneNodeRow at all - this
    // mapping (and the wire payload it reads from) never references it.
    expect("gitlinkContextXml" in (glFlowNode!.data as Record<string, unknown>)).toBe(false);
  });

  it("coalesces null-ish optional fields (pendingRequestId/gitlinkChangeFingerprint) to null", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "gl-2", kind: "gitlink" })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const glFlowNode = flowNodes.find((n) => n.id === "gl-2");
    expect(glFlowNode).toBeDefined();
    expect(glFlowNode!.data).toMatchObject({ pendingRequestId: null, gitlinkChangeFingerprint: null });
  });

  it("onFetchRepositories/onLoadTree/onSetLocalRoot/onBrowseLocalRoot/onImportSnapshot/onBuildContext/onFetchContext/onRun/onApply all resolve to this node's id", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "gl-1", kind: "gitlink" })], edges: [] });
    const store = makeStore();
    const fetchReposSpy = vi.spyOn(store, "fetchGitlinkRepositories").mockResolvedValue([]);
    const loadTreeSpy = vi.spyOn(store, "loadGitlinkRepoTree");
    const setRootSpy = vi.spyOn(store, "setGitlinkLocalRoot");
    const browseRootSpy = vi.spyOn(store, "pickGitlinkLocalRoot");
    const importSpy = vi.spyOn(store, "importGitlinkSnapshot");
    const buildContextSpy = vi.spyOn(store, "buildGitlinkContext");
    const fetchContextSpy = vi.spyOn(store, "fetchGitlinkContext").mockResolvedValue("");
    const runSpy = vi.spyOn(store, "runGitlinkChangeSet");
    const applySpy = vi.spyOn(store, "applyGitlinkChanges");

    const flowNodes = toFlowNodes(scene, store);
    const glFlowNode = flowNodes.find((n) => n.id === "gl-1");
    const data = glFlowNode!.data as unknown as {
      onFetchRepositories: () => Promise<string[]>;
      onLoadTree: (repo: string, branch: string) => void;
      onSetLocalRoot: (localRoot: string) => void;
      onBrowseLocalRoot: () => void;
      onImportSnapshot: (repo: string, branch: string) => void;
      onBuildContext: (scopeMode: string, selectedPaths: string[]) => void;
      onFetchContext: () => Promise<string>;
      onRun: (taskPrompt: string) => void;
      onApply: (fingerprint: string) => void;
    };

    data.onFetchRepositories();
    expect(fetchReposSpy).toHaveBeenCalledWith("gl-1");
    data.onLoadTree("owner/repo", "main");
    expect(loadTreeSpy).toHaveBeenCalledWith("gl-1", "owner/repo", "main");
    data.onSetLocalRoot("C:/repos/repo");
    expect(setRootSpy).toHaveBeenCalledWith("gl-1", "C:/repos/repo");
    data.onBrowseLocalRoot();
    expect(browseRootSpy).toHaveBeenCalledWith("gl-1");
    data.onImportSnapshot("owner/repo", "main");
    expect(importSpy).toHaveBeenCalledWith("gl-1", "owner/repo", "main");
    data.onBuildContext("full", ["a.py"]);
    expect(buildContextSpy).toHaveBeenCalledWith("gl-1", "full", ["a.py"]);
    data.onFetchContext();
    expect(fetchContextSpy).toHaveBeenCalledWith("gl-1");
    data.onRun("Add a health-check endpoint");
    expect(runSpy).toHaveBeenCalledWith("gl-1", "Add a health-check endpoint");
    data.onApply("fp-1");
    expect(applySpy).toHaveBeenCalledWith("gl-1", "fp-1");
  });

  it("onCancel fires cancelGitlinkRequest with pendingRequestId when set, and is a no-op otherwise", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "gl-pending", kind: "gitlink", pendingRequestId: "req-77" }),
        baseNode({ id: "gl-idle", kind: "gitlink", pendingRequestId: null }),
      ],
      edges: [],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "cancelGitlinkRequest");

    const flowNodes = toFlowNodes(scene, store);
    const pendingNode = flowNodes.find((n) => n.id === "gl-pending");
    const idleNode = flowNodes.find((n) => n.id === "gl-idle");

    (pendingNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledWith("req-77");

    (idleNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledTimes(1);
  });

  it("onToggleCollapse/onDelete reuse the generic setChatCollapsed/removeNodes intents", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "gl-1", kind: "gitlink", isCollapsed: false })],
      edges: [],
    });
    const store = makeStore();
    const collapseSpy = vi.spyOn(store, "setChatCollapsed");
    const removeSpy = vi.spyOn(store, "removeNodes");

    const flowNodes = toFlowNodes(scene, store);
    const glFlowNode = flowNodes.find((n) => n.id === "gl-1");

    (glFlowNode!.data as { onToggleCollapse: () => void }).onToggleCollapse();
    expect(collapseSpy).toHaveBeenCalledWith("gl-1", true);

    (glFlowNode!.data as { onDelete: () => void }).onDelete();
    expect(removeSpy).toHaveBeenCalledWith(["gl-1"]);
  });
});

describe("toFlowNodes (R5.4 pycoder node)", () => {
  it("maps a pycoder scene node's all 8 new fields onto the flow node's data", () => {
    const scene = baseScene({
      nodes: [
        baseNode({
          id: "pc-1",
          kind: "pycoder",
          isCollapsed: true,
          pendingRequestId: "req-1",
          pycoderMode: "manual",
          pycoderPrompt: "write a fibonacci function",
          pycoderCode: "def fib(n): ...",
          pycoderOutput: "[1, 1, 2, 3]",
          pycoderAnalysis: "Computes Fibonacci numbers.",
          pycoderLastRunFailed: true,
          pycoderAwaitingApproval: true,
          pycoderError: "previous run timed out",
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const pcFlowNode = flowNodes.find((n) => n.id === "pc-1");
    expect(pcFlowNode).toBeDefined();
    expect(pcFlowNode!.type).toBe("pycoder");
    expect(pcFlowNode!.data).toMatchObject({
      pycoderMode: "manual",
      pycoderPrompt: "write a fibonacci function",
      pycoderCode: "def fib(n): ...",
      pycoderOutput: "[1, 1, 2, 3]",
      pycoderAnalysis: "Computes Fibonacci numbers.",
      pycoderLastRunFailed: true,
      pycoderAwaitingApproval: true,
      pycoderError: "previous run timed out",
      isCollapsed: true,
      pendingRequestId: "req-1",
    });
  });

  it("coalesces a null-ish pendingRequestId to null", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "pc-2", kind: "pycoder" })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const pcFlowNode = flowNodes.find((n) => n.id === "pc-2");
    expect(pcFlowNode).toBeDefined();
    expect(pcFlowNode!.data).toMatchObject({ pendingRequestId: null });
  });

  it("onSetMode/onRun resolve to this node's id", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "pc-1", kind: "pycoder" })], edges: [] });
    const store = makeStore();
    const setModeSpy = vi.spyOn(store, "setPyCoderMode");
    const runSpy = vi.spyOn(store, "runPyCoder");

    const flowNodes = toFlowNodes(scene, store);
    const pcFlowNode = flowNodes.find((n) => n.id === "pc-1");
    const data = pcFlowNode!.data as unknown as {
      onSetMode: (mode: string) => void;
      onRun: (inputText: string) => void;
    };

    data.onSetMode("manual");
    expect(setModeSpy).toHaveBeenCalledWith("pc-1", "manual");
    data.onRun("print('hi')");
    expect(runSpy).toHaveBeenCalledWith("pc-1", "print('hi')");
  });

  it("onCancel fires cancelPyCoderRequest with pendingRequestId when set, and is a no-op otherwise", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "pc-pending", kind: "pycoder", pendingRequestId: "req-77" }),
        baseNode({ id: "pc-idle", kind: "pycoder", pendingRequestId: null }),
      ],
      edges: [],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "cancelPyCoderRequest");

    const flowNodes = toFlowNodes(scene, store);
    const pendingNode = flowNodes.find((n) => n.id === "pc-pending");
    const idleNode = flowNodes.find((n) => n.id === "pc-idle");

    (pendingNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledWith("req-77");

    (idleNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledTimes(1);
  });

  it("onApprove/onDeny always resolve to the CURRENT scene snapshot's own pendingRequestId, never a UI-supplied id, and are no-ops when it is null", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "pc-pending", kind: "pycoder", pendingRequestId: "req-approve-1" }),
        baseNode({ id: "pc-idle", kind: "pycoder", pendingRequestId: null }),
      ],
      edges: [],
    });
    const store = makeStore();
    const approveSpy = vi.spyOn(store, "approveCodeExecution");
    const denySpy = vi.spyOn(store, "denyCodeExecution");

    const flowNodes = toFlowNodes(scene, store);
    const pendingNode = flowNodes.find((n) => n.id === "pc-pending");
    const idleNode = flowNodes.find((n) => n.id === "pc-idle");
    const pendingData = pendingNode!.data as unknown as { onApprove: () => void; onDeny: () => void };
    const idleData = idleNode!.data as unknown as { onApprove: () => void; onDeny: () => void };

    pendingData.onApprove();
    expect(approveSpy).toHaveBeenCalledWith("req-approve-1");
    pendingData.onDeny();
    expect(denySpy).toHaveBeenCalledWith("req-approve-1");

    idleData.onApprove();
    idleData.onDeny();
    expect(approveSpy).toHaveBeenCalledTimes(1);
    expect(denySpy).toHaveBeenCalledTimes(1);
  });

  it("onToggleCollapse/onDelete reuse the generic setChatCollapsed/removeNodes intents", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "pc-1", kind: "pycoder", isCollapsed: false })],
      edges: [],
    });
    const store = makeStore();
    const collapseSpy = vi.spyOn(store, "setChatCollapsed");
    const removeSpy = vi.spyOn(store, "removeNodes");

    const flowNodes = toFlowNodes(scene, store);
    const pcFlowNode = flowNodes.find((n) => n.id === "pc-1");

    (pcFlowNode!.data as { onToggleCollapse: () => void }).onToggleCollapse();
    expect(collapseSpy).toHaveBeenCalledWith("pc-1", true);

    (pcFlowNode!.data as { onDelete: () => void }).onDelete();
    expect(removeSpy).toHaveBeenCalledWith(["pc-1"]);
  });
});

describe("toFlowNodes (R5.4 code_sandbox node)", () => {
  it("maps a code_sandbox scene node's all 7 new fields onto the flow node's data", () => {
    const scene = baseScene({
      nodes: [
        baseNode({
          id: "cs-1",
          kind: "code_sandbox",
          isCollapsed: true,
          pendingRequestId: "req-1",
          codeSandboxRequirements: "numpy\npandas",
          codeSandboxPrompt: "plot a sine wave",
          codeSandboxCode: "import numpy as np\n...",
          codeSandboxOutput: "[plot saved]",
          codeSandboxAnalysis: "Generates and saves a sine wave plot.",
          codeSandboxAwaitingApproval: true,
          codeSandboxError: "previous run timed out",
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const csFlowNode = flowNodes.find((n) => n.id === "cs-1");
    expect(csFlowNode).toBeDefined();
    expect(csFlowNode!.type).toBe("code_sandbox");
    expect(csFlowNode!.data).toMatchObject({
      codeSandboxRequirements: "numpy\npandas",
      codeSandboxPrompt: "plot a sine wave",
      codeSandboxCode: "import numpy as np\n...",
      codeSandboxOutput: "[plot saved]",
      codeSandboxAnalysis: "Generates and saves a sine wave plot.",
      codeSandboxAwaitingApproval: true,
      codeSandboxError: "previous run timed out",
      isCollapsed: true,
      pendingRequestId: "req-1",
    });
    // code_sandbox_sandbox_id is pure internal server bookkeeping - it is not
    // part of SceneNodeRow at all, so this mapping never references it.
    expect("codeSandboxSandboxId" in (csFlowNode!.data as Record<string, unknown>)).toBe(false);
  });

  it("coalesces a null-ish pendingRequestId to null", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "cs-2", kind: "code_sandbox" })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const csFlowNode = flowNodes.find((n) => n.id === "cs-2");
    expect(csFlowNode).toBeDefined();
    expect(csFlowNode!.data).toMatchObject({ pendingRequestId: null });
  });

  it("onSetRequirements/onRun resolve to this node's id", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "cs-1", kind: "code_sandbox" })], edges: [] });
    const store = makeStore();
    const setReqSpy = vi.spyOn(store, "setCodeSandboxRequirements");
    const runSpy = vi.spyOn(store, "runCodeSandbox");

    const flowNodes = toFlowNodes(scene, store);
    const csFlowNode = flowNodes.find((n) => n.id === "cs-1");
    const data = csFlowNode!.data as unknown as {
      onSetRequirements: (requirementsText: string) => void;
      onRun: (inputText: string) => void;
    };

    data.onSetRequirements("numpy==1.24");
    expect(setReqSpy).toHaveBeenCalledWith("cs-1", "numpy==1.24");
    data.onRun("plot a sine wave");
    expect(runSpy).toHaveBeenCalledWith("cs-1", "plot a sine wave");
  });

  it("onToggleAllowSourceBuilds resolves to this node's id, not a different one in a multi-node scene", () => {
    // ADR-005 stage 5.5 test-coverage-gap fix: this closure is built inside
    // toFlowNodes's per-node loop - the exact shape of bug that would ship
    // silently without this test is accidentally capturing the wrong
    // node's id. Two code_sandbox nodes proves the right one is threaded,
    // not just "some" id.
    const scene = baseScene({
      nodes: [
        baseNode({ id: "cs-1", kind: "code_sandbox" }),
        baseNode({ id: "cs-2", kind: "code_sandbox" }),
      ],
      edges: [],
    });
    const store = makeStore();
    const toggleSpy = vi.spyOn(store, "setCodeSandboxAllowSourceBuilds");

    const flowNodes = toFlowNodes(scene, store);
    const cs1Data = flowNodes.find((n) => n.id === "cs-1")!.data as unknown as {
      onToggleAllowSourceBuilds: (allow: boolean) => void;
    };
    const cs2Data = flowNodes.find((n) => n.id === "cs-2")!.data as unknown as {
      onToggleAllowSourceBuilds: (allow: boolean) => void;
    };

    cs1Data.onToggleAllowSourceBuilds(true);
    expect(toggleSpy).toHaveBeenCalledExactlyOnceWith("cs-1", true);
    cs2Data.onToggleAllowSourceBuilds(false);
    expect(toggleSpy).toHaveBeenCalledWith("cs-2", false);
    expect(toggleSpy).toHaveBeenCalledTimes(2);
  });

  it("onCancel fires cancelCodeSandboxRequest with pendingRequestId when set, and is a no-op otherwise", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "cs-pending", kind: "code_sandbox", pendingRequestId: "req-77" }),
        baseNode({ id: "cs-idle", kind: "code_sandbox", pendingRequestId: null }),
      ],
      edges: [],
    });
    const store = makeStore();
    const intentSpy = vi.spyOn(store, "cancelCodeSandboxRequest");

    const flowNodes = toFlowNodes(scene, store);
    const pendingNode = flowNodes.find((n) => n.id === "cs-pending");
    const idleNode = flowNodes.find((n) => n.id === "cs-idle");

    (pendingNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledWith("req-77");

    (idleNode!.data as { onCancel: () => void }).onCancel();
    expect(intentSpy).toHaveBeenCalledTimes(1);
  });

  it("onApprove/onDeny always resolve to the CURRENT scene snapshot's own pendingRequestId, never a UI-supplied id, and are no-ops when it is null", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "cs-pending", kind: "code_sandbox", pendingRequestId: "req-approve-2" }),
        baseNode({ id: "cs-idle", kind: "code_sandbox", pendingRequestId: null }),
      ],
      edges: [],
    });
    const store = makeStore();
    const approveSpy = vi.spyOn(store, "approveCodeExecution");
    const denySpy = vi.spyOn(store, "denyCodeExecution");

    const flowNodes = toFlowNodes(scene, store);
    const pendingNode = flowNodes.find((n) => n.id === "cs-pending");
    const idleNode = flowNodes.find((n) => n.id === "cs-idle");
    const pendingData = pendingNode!.data as unknown as { onApprove: () => void; onDeny: () => void };
    const idleData = idleNode!.data as unknown as { onApprove: () => void; onDeny: () => void };

    pendingData.onApprove();
    expect(approveSpy).toHaveBeenCalledWith("req-approve-2");
    pendingData.onDeny();
    expect(denySpy).toHaveBeenCalledWith("req-approve-2");

    idleData.onApprove();
    idleData.onDeny();
    expect(approveSpy).toHaveBeenCalledTimes(1);
    expect(denySpy).toHaveBeenCalledTimes(1);
  });

  it("subscribeStream forwards directly to store.subscribeStream (generic transport passthrough, no scene-specific plumbing)", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "cs-1", kind: "code_sandbox" })], edges: [] });
    const store = makeStore();
    const unsubscribe = vi.fn();
    const subscribeSpy = vi.spyOn(store, "subscribeStream").mockReturnValue(unsubscribe);

    const flowNodes = toFlowNodes(scene, store);
    const csFlowNode = flowNodes.find((n) => n.id === "cs-1");
    const data = csFlowNode!.data as unknown as {
      subscribeStream: (requestId: string, listener: (...args: unknown[]) => void) => () => void;
    };
    const listener = vi.fn();

    const result = data.subscribeStream("req-1", listener);
    expect(subscribeSpy).toHaveBeenCalledWith("req-1", listener);
    expect(result).toBe(unsubscribe);
  });

  it("onToggleCollapse/onDelete reuse the generic setChatCollapsed/removeNodes intents", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "cs-1", kind: "code_sandbox", isCollapsed: false })],
      edges: [],
    });
    const store = makeStore();
    const collapseSpy = vi.spyOn(store, "setChatCollapsed");
    const removeSpy = vi.spyOn(store, "removeNodes");

    const flowNodes = toFlowNodes(scene, store);
    const csFlowNode = flowNodes.find((n) => n.id === "cs-1");

    (csFlowNode!.data as { onToggleCollapse: () => void }).onToggleCollapse();
    expect(collapseSpy).toHaveBeenCalledWith("cs-1", true);

    (csFlowNode!.data as { onDelete: () => void }).onDelete();
    expect(removeSpy).toHaveBeenCalledWith(["cs-1"]);
  });
});

describe("toFlowNodes (ADR-006 stage 6.4 universal streaming)", () => {
  it("the chat branch maps pendingRequestId/responseIncomplete and injects the same subscribeStream passthrough as code_sandbox", () => {
    const scene = baseScene({
      nodes: [
        baseNode({
          id: "chat-1",
          kind: "chat",
          content: "partial answer",
          pendingRequestId: "req-9",
          responseIncomplete: true,
        }),
      ],
      edges: [],
    });
    const store = makeStore();
    const unsubscribe = vi.fn();
    const subscribeSpy = vi.spyOn(store, "subscribeStream").mockReturnValue(unsubscribe);

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode!.data).toMatchObject({
      pendingRequestId: "req-9",
      responseIncomplete: true,
    });

    const data = chatFlowNode!.data as unknown as {
      subscribeStream: (requestId: string, listener: (...args: unknown[]) => void) => () => void;
    };
    const listener = vi.fn();
    const result = data.subscribeStream("req-9", listener);
    expect(subscribeSpy).toHaveBeenCalledWith("req-9", listener);
    expect(result).toBe(unsubscribe);
  });

  it("the chat branch's onCancelRegenerate fires the same cancel path the conversation node uses, guarded on a non-null request id", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "chat-pending", kind: "chat", pendingRequestId: "req-11" }),
        baseNode({ id: "chat-idle", kind: "chat", pendingRequestId: null }),
      ],
      edges: [],
    });
    const store = makeStore();
    const cancelSpy = vi.spyOn(store, "cancelConversationRequest");

    const flowNodes = toFlowNodes(scene, store);
    const pendingData = flowNodes.find((n) => n.id === "chat-pending")!.data as {
      onCancelRegenerate: () => void;
    };
    const idleData = flowNodes.find((n) => n.id === "chat-idle")!.data as {
      onCancelRegenerate: () => void;
    };

    pendingData.onCancelRegenerate();
    expect(cancelSpy).toHaveBeenCalledWith("req-11");

    idleData.onCancelRegenerate();
    expect(cancelSpy).toHaveBeenCalledTimes(1);
  });

  it("the conversation branch injects the same subscribeStream passthrough", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "conv-1", kind: "conversation", pendingRequestId: "req-10" })],
      edges: [],
    });
    const store = makeStore();
    const unsubscribe = vi.fn();
    const subscribeSpy = vi.spyOn(store, "subscribeStream").mockReturnValue(unsubscribe);

    const flowNodes = toFlowNodes(scene, store);
    const convFlowNode = flowNodes.find((n) => n.id === "conv-1");
    expect(convFlowNode!.data).toMatchObject({ pendingRequestId: "req-10" });

    const data = convFlowNode!.data as unknown as {
      subscribeStream: (requestId: string, listener: (...args: unknown[]) => void) => () => void;
    };
    const listener = vi.fn();
    const result = data.subscribeStream("req-10", listener);
    expect(subscribeSpy).toHaveBeenCalledWith("req-10", listener);
    expect(result).toBe(unsubscribe);
  });
});

describe("handleSelectionChange (R5.1 onSelectionChange wiring)", () => {
  it("calls store.setSelectedNodeId with the first selected node's id", () => {
    const store = makeStore();
    const spy = vi.spyOn(store, "setSelectedNodeId");
    handleSelectionChange(store, [{ id: "n1" }, { id: "n2" }]);
    expect(spy).toHaveBeenCalledWith("n1");
  });

  it("calls store.setSelectedNodeId with null when nothing is selected", () => {
    const store = makeStore();
    const spy = vi.spyOn(store, "setSelectedNodeId");
    handleSelectionChange(store, []);
    expect(spy).toHaveBeenCalledWith(null);
  });
});

// R6.1: Notes/Frames/Containers. ADR-003 stage 3.3 (C9) put color/
// headerColor/isSystemPrompt/isSummaryNote/itemIds/isLocked/groupWidth/
// groupHeight directly on the generated SceneNodeRow type (baseNode()
// already sets all of them) - this helper only exists now for its
// convenient per-kind defaults, not to work around a missing field.
function groupNode(overrides: Partial<SceneNodeRow> = {}): SceneNodeRow {
  return {
    ...baseNode(),
    ...overrides,
  };
}

describe("toFlowNodes (R6.1 note node)", () => {
  it("maps a note scene node's content/color/headerColor/isSystemPrompt/isSummaryNote onto the flow node's data", () => {
    const scene = baseScene({
      nodes: [
        groupNode({
          id: "note-1",
          kind: "note",
          content: "Remember to follow up",
          color: "#3f8f5c",
          headerColor: "#3f7dc9",
          isSystemPrompt: true,
          isSummaryNote: false,
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const noteFlowNode = flowNodes.find((n) => n.id === "note-1");
    expect(noteFlowNode).toBeDefined();
    expect(noteFlowNode!.type).toBe("note");
    expect(noteFlowNode!.data).toMatchObject({
      content: "Remember to follow up",
      color: "#3f8f5c",
      headerColor: "#3f7dc9",
      isSystemPrompt: true,
      isSummaryNote: false,
    });
  });

  it("maps a note's isBranchComparison and reuses itemIds as compareSourceNodeIds (ADR-002 Workstream 1)", () => {
    const scene = baseScene({
      nodes: [
        groupNode({
          id: "note-1",
          kind: "note",
          isBranchComparison: true,
          itemIds: ["chat-1", "chat-2"],
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const noteFlowNode = flowNodes.find((n) => n.id === "note-1");
    expect(noteFlowNode!.data).toMatchObject({
      isBranchComparison: true,
      compareSourceNodeIds: ["chat-1", "chat-2"],
    });
  });

  // ADR-003 stage 3.3 (C9) review-fix: groupNode()/baseNode() always supply
  // a real `null` for color/headerColor, so no test above would fail if
  // SceneCanvas.tsx's `n.color ?? null`/`n.headerColor ?? null`
  // normalization were deleted - this proves it does something for a note
  // whose wire payload genuinely omits the field (`undefined`).
  it("normalizes an absent (undefined) color/headerColor to null, not undefined", () => {
    const scene = baseScene({
      nodes: [groupNode({ id: "note-1", kind: "note", color: undefined, headerColor: undefined })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const noteFlowNode = flowNodes.find((n) => n.id === "note-1");
    const data = noteFlowNode!.data as { color: string | null; headerColor: string | null };
    expect(data.color).toBeNull();
    expect(data.headerColor).toBeNull();
  });

  it("onSetContent/onSetColor/onDelete call the right store intents with this node's id", () => {
    const scene = baseScene({ nodes: [groupNode({ id: "note-1", kind: "note" })], edges: [] });
    const store = makeStore();
    const setContentSpy = vi.spyOn(store, "setNoteContent");
    const setColorSpy = vi.spyOn(store, "setGroupColor");
    const removeSpy = vi.spyOn(store, "removeNodes");

    const flowNodes = toFlowNodes(scene, store);
    const noteFlowNode = flowNodes.find((n) => n.id === "note-1");
    const data = noteFlowNode!.data as {
      onSetContent: (content: string) => void;
      onSetColor: (color: string | null, headerColor: string | null) => void;
      onDelete: () => void;
    };

    data.onSetContent("updated");
    expect(setContentSpy).toHaveBeenCalledWith("note-1", "updated");

    data.onSetColor("#cf5354", null);
    expect(setColorSpy).toHaveBeenCalledWith("note-1", "#cf5354", null);

    data.onDelete();
    expect(removeSpy).toHaveBeenCalledWith(["note-1"]);
  });
});

describe("toFlowNodes (R6.1 frame/container nodes)", () => {
  it("maps a frame's groupWidth/groupHeight onto the flow NODE's own width/height, sets zIndex:-1, and draggable:true when locked", () => {
    const scene = baseScene({
      nodes: [
        groupNode({
          id: "frame-1",
          kind: "frame",
          x: 10,
          y: 20,
          content: "My Frame",
          itemIds: ["n1", "n2"],
          isLocked: true,
          groupWidth: 400,
          groupHeight: 250,
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const frameFlowNode = flowNodes.find((n) => n.id === "frame-1");
    expect(frameFlowNode).toBeDefined();
    expect(frameFlowNode!.type).toBe("frame");
    expect(frameFlowNode!.position).toEqual({ x: 10, y: 20 });
    expect(frameFlowNode!.width).toBe(400);
    expect(frameFlowNode!.height).toBe(250);
    expect(frameFlowNode!.zIndex).toBe(-1);
    expect(frameFlowNode!.draggable).toBe(true);
    expect(frameFlowNode!.data).toMatchObject({
      groupKind: "frame",
      label: "My Frame",
      isLocked: true,
      itemIds: ["n1", "n2"],
    });
  });

  it("an UNLOCKED frame is STILL draggable:true (R6.1 follow-up: restores independent-of-members dragging)", () => {
    const scene = baseScene({
      nodes: [groupNode({ id: "frame-1", kind: "frame", isLocked: false, groupWidth: 300, groupHeight: 200 })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const frameFlowNode = flowNodes.find((n) => n.id === "frame-1");
    // Draggable regardless of lock state now - groupDragKindOf (tested
    // separately below) is what gates whether a drag carries members
    // along, not whether the frame can be dragged at all.
    expect(frameFlowNode!.draggable).toBe(true);
  });

  it("a container is ALWAYS draggable regardless of isLocked (containers have no lock concept)", () => {
    const scene = baseScene({
      nodes: [
        groupNode({ id: "container-1", kind: "container", isLocked: false, groupWidth: 300, groupHeight: 200 }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const containerFlowNode = flowNodes.find((n) => n.id === "container-1");
    expect(containerFlowNode!.type).toBe("container");
    expect(containerFlowNode!.draggable).toBe(true);
    expect((containerFlowNode!.data as { groupKind: string }).groupKind).toBe("container");
  });

  it("a container's zIndex sits BEHIND a frame's (-2 vs -1), so a nested frame-in-container stacks correctly", () => {
    const scene = baseScene({
      nodes: [
        groupNode({ id: "frame-1", kind: "frame", groupWidth: 300, groupHeight: 200 }),
        groupNode({ id: "container-1", kind: "container", groupWidth: 300, groupHeight: 200 }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    expect(flowNodes.find((n) => n.id === "frame-1")!.zIndex).toBe(-1);
    expect(flowNodes.find((n) => n.id === "container-1")!.zIndex).toBe(-2);
  });

  it("computes memberKinds from the current member nodes, skipping a stale/dangling item id", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "m1", kind: "chat" }),
        baseNode({ id: "m2", kind: "code" }),
        groupNode({ id: "container-1", kind: "container", itemIds: ["m1", "m2", "gone"] }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const data = flowNodes.find((n) => n.id === "container-1")!.data as { memberKinds: string[] };
    expect(data.memberKinds).toEqual(["chat", "code"]);
  });

  it("falls back to GROUP_FALLBACK_WIDTH/HEIGHT when groupWidth/groupHeight are null", () => {
    const scene = baseScene({
      nodes: [groupNode({ id: "frame-1", kind: "frame", groupWidth: null, groupHeight: null })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const frameFlowNode = flowNodes.find((n) => n.id === "frame-1");
    expect(frameFlowNode!.width).toBe(320);
    expect(frameFlowNode!.height).toBe(200);
  });

  it("onSetLabel/onToggleCollapsed/onToggleLock/onSetColor/onResize/onFitToContent/onUngroup call the right store intents", () => {
    const scene = baseScene({ nodes: [groupNode({ id: "frame-1", kind: "frame" })], edges: [] });
    const store = makeStore();
    const spies = {
      setGroupLabel: vi.spyOn(store, "setGroupLabel"),
      toggleGroupCollapsed: vi.spyOn(store, "toggleGroupCollapsed"),
      toggleFrameLock: vi.spyOn(store, "toggleFrameLock"),
      setGroupColor: vi.spyOn(store, "setGroupColor"),
      resizeFrame: vi.spyOn(store, "resizeFrame"),
      fitFrameToContent: vi.spyOn(store, "fitFrameToContent"),
      ungroup: vi.spyOn(store, "ungroup"),
    };

    const flowNodes = toFlowNodes(scene, store);
    const data = flowNodes.find((n) => n.id === "frame-1")!.data as {
      onSetLabel: (text: string) => void;
      onToggleCollapsed: () => void;
      onToggleLock: () => void;
      onSetColor: (color: string | null, headerColor: string | null) => void;
      onResize: (width: number, height: number) => void;
      onFitToContent: () => void;
      onUngroup: () => void;
    };

    data.onSetLabel("New Label");
    expect(spies.setGroupLabel).toHaveBeenCalledWith("frame-1", "New Label");
    data.onToggleCollapsed();
    expect(spies.toggleGroupCollapsed).toHaveBeenCalledWith("frame-1");
    data.onToggleLock();
    expect(spies.toggleFrameLock).toHaveBeenCalledWith("frame-1");
    data.onSetColor("#d98a3d", null);
    expect(spies.setGroupColor).toHaveBeenCalledWith("frame-1", "#d98a3d", null);
    data.onResize(500, 300);
    expect(spies.resizeFrame).toHaveBeenCalledWith("frame-1", 500, 300);
    data.onFitToContent();
    expect(spies.fitFrameToContent).toHaveBeenCalledWith("frame-1");
    data.onUngroup();
    expect(spies.ungroup).toHaveBeenCalledWith("frame-1");
  });

  // ADR-003 stage 3.3 (C9) review-fix: same "always-null-anyway" fixture
  // gap as the note-node block above - this proves SceneCanvas.tsx's
  // `n.color ?? null`/`n.headerColor ?? null` normalization in the frame/
  // container branch actually converts an absent (undefined) wire value to
  // null rather than leaking undefined through to GroupNodeData.
  it("normalizes an absent (undefined) color/headerColor to null, not undefined", () => {
    const scene = baseScene({
      nodes: [groupNode({ id: "frame-1", kind: "frame", color: undefined, headerColor: undefined })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const frameFlowNode = flowNodes.find((n) => n.id === "frame-1");
    const data = frameFlowNode!.data as { color: string | null; headerColor: string | null };
    expect(data.color).toBeNull();
    expect(data.headerColor).toBeNull();
  });
});

// R6.2: Chart node. ADR-003 stage 3.3 (C9) put chartType/chartData/
// chartError/chartWidth/chartHeight/chartAspectLocked/chartSourceNodeId
// directly on the generated SceneNodeRow type (chartAssetId/
// chartAssetVersion rode alongside them until ADR-013 stage 13.4 retired
// the backend-rendered display PNG they addressed) - this helper only
// exists now for its convenient chart-kind defaults, not to work around
// missing fields.
function chartNode(overrides: Partial<SceneNodeRow> = {}): SceneNodeRow {
  return {
    ...baseNode(),
    kind: "chart",
    chartType: "bar",
    chartData: { type: "bar", title: "Revenue" },
    chartError: "",
    chartWidth: 680,
    chartHeight: 500,
    chartAspectLocked: true,
    chartSourceNodeId: "chat-1",
    ...overrides,
  };
}

describe("toFlowNodes (R6.2 chart node)", () => {
  it("maps all 7 chart wire fields onto the flow node's data, and chartWidth/chartHeight ALSO onto the flow node object itself (NodeResizer controlled-mode)", () => {
    const scene = baseScene({
      nodes: [
        chartNode({
          id: "chart-1",
          x: 10,
          y: 20,
          chartType: "sankey",
          chartData: { type: "sankey", title: "Flow" },
          chartError: "used a placeholder chart",
          chartWidth: 900,
          chartHeight: 640,
          chartAspectLocked: false,
          chartSourceNodeId: "chat-9",
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const chartFlowNode = flowNodes.find((n) => n.id === "chart-1");
    expect(chartFlowNode).toBeDefined();
    expect(chartFlowNode!.type).toBe("chart");
    expect(chartFlowNode!.position).toEqual({ x: 10, y: 20 });
    expect(chartFlowNode!.width).toBe(900);
    expect(chartFlowNode!.height).toBe(640);
    expect(chartFlowNode!.data).toMatchObject({
      chartType: "sankey",
      chartData: { type: "sankey", title: "Flow" },
      chartError: "used a placeholder chart",
      chartWidth: 900,
      chartHeight: 640,
      chartAspectLocked: false,
      chartSourceNodeId: "chat-9",
    });
  });

  it("onToggleAspectLock/onResize call the right store intents with this node's id", () => {
    const scene = baseScene({ nodes: [chartNode({ id: "chart-1" })], edges: [] });
    const store = makeStore();
    const toggleSpy = vi.spyOn(store, "toggleChartAspectLock");
    const resizeSpy = vi.spyOn(store, "resizeChart");

    const flowNodes = toFlowNodes(scene, store);
    const data = flowNodes.find((n) => n.id === "chart-1")!.data as {
      onToggleAspectLock: () => void;
      onResize: (width: number, height: number) => void;
    };

    data.onToggleAspectLock();
    expect(toggleSpy).toHaveBeenCalledWith("chart-1");
    data.onResize(1200, 900);
    expect(resizeSpy).toHaveBeenCalledWith("chart-1", 1200, 900);
  });

  it("a docked chart node is omitted from the flow nodes array, same generic guard as every other kind", () => {
    const scene = baseScene({ nodes: [chartNode({ id: "chart-1", isDocked: true })], edges: [] });
    const store = makeStore();
    expect(toFlowNodes(scene, store).find((n) => n.id === "chart-1")).toBeUndefined();
  });
});

// R6.3: Scene-level serialization gaps. ADR-003 stage 3.3 (C9) put
// htmlSplitterState/chatScrollValue directly on the generated SceneNodeRow
// type - this helper only exists now to apply overrides on top of a
// baseNode(), not to work around missing fields.
function withR63Fields(node: SceneNodeRow, overrides: Partial<Pick<SceneNodeRow, "htmlSplitterState" | "chatScrollValue">> = {}): SceneNodeRow {
  return {
    ...node,
    htmlSplitterState: null,
    chatScrollValue: 0,
    ...overrides,
  };
}

describe("toFlowNodes (R6.3 chat node scroll value)", () => {
  it("maps a saved chatScrollValue onto the flow node's data", () => {
    const scene = baseScene({
      nodes: [withR63Fields(baseNode({ id: "chat-1", kind: "chat", content: "Hi" }), { chatScrollValue: 240 })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect((chatFlowNode!.data as { chatScrollValue: number }).chatScrollValue).toBe(240);
  });

  it("defaults chatScrollValue to 0 for an ordinary chat node", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hi" })], edges: [] });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect((chatFlowNode!.data as { chatScrollValue: number }).chatScrollValue).toBe(0);
  });

  it("onScrollChange calls setChatScrollValue with this node's own id and the given value", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hi" })], edges: [] });
    const store = makeStore();
    const spy = vi.spyOn(store, "setChatScrollValue");

    const flowNodes = toFlowNodes(scene, store);
    const data = flowNodes.find((n) => n.id === "chat-1")!.data as { onScrollChange: (value: number) => void };
    data.onScrollChange(180);
    expect(spy).toHaveBeenCalledWith("chat-1", 180);
  });
});

// ADR-002 Workstream 1 ("Synthesize Branches"). ADR-003 stage 3.3 (C9) put
// provider/model/isBranchSynthesis/synthesisInstructions/itemIds directly
// on the generated SceneNodeRow type - this helper only exists now to
// apply overrides on top of a baseNode(), not to work around missing
// fields.
function withSynthesisFields(
  node: SceneNodeRow,
  overrides: Partial<Pick<SceneNodeRow, "provider" | "model" | "isBranchSynthesis" | "synthesisInstructions" | "itemIds">> = {},
): SceneNodeRow {
  return {
    ...node,
    provider: null,
    model: null,
    isBranchSynthesis: false,
    synthesisInstructions: "",
    itemIds: [],
    ...overrides,
  };
}

describe("toFlowNodes (ADR-002 Workstream 1 - Synthesize Branches provenance)", () => {
  it("maps a synthesis result chat node's provider/model/instructions/source ids onto the flow node's data", () => {
    const scene = baseScene({
      nodes: [
        withSynthesisFields(baseNode({ id: "chat-1", kind: "chat", content: "Combined answer" }), {
          provider: "Anthropic Claude",
          model: "claude-sonnet-5",
          isBranchSynthesis: true,
          synthesisInstructions: "merge the best of both",
          itemIds: ["chat-a", "chat-b"],
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode!.data).toMatchObject({
      provider: "Anthropic Claude",
      model: "claude-sonnet-5",
      isBranchSynthesis: true,
      synthesisInstructions: "merge the best of both",
      synthesisSourceNodeIds: ["chat-a", "chat-b"],
    });
  });

  it("defaults to no provenance for an ordinary chat node (the vast majority)", () => {
    // ADR-003 stage 3.3 review-fix: this used to assert `undefined` for
    // every one of these fields - an artifact of the test fixture never
    // having included them at all (pre-C9, provider/model/etc. genuinely
    // weren't on the generated SceneNodeRow type), not a reflection of the
    // real backend, which has ALWAYS sent these fields for every node
    // (scene_payload() defaults them to null/""/false/[] for a non-
    // synthesis chat node, never omits the key). Now that baseNode()
    // matches that real shape, the correct expectation is those same real
    // defaults, not `undefined`.
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello" })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode!.data).toMatchObject({
      provider: null,
      model: null,
      isBranchSynthesis: false,
      synthesisInstructions: "",
      synthesisSourceNodeIds: [],
    });
  });

  // ADR-003 stage 3.3 (C9) review-fix: baseNode() always supplies a real
  // `null` (never `undefined`) for provider/model, so every test above
  // would pass identically even if SceneCanvas.tsx's `n.provider ?? null`/
  // `n.model ?? null` normalization were deleted outright - `null ?? null`
  // and plain `null` produce the same value. This proves the normalization
  // itself does something: an OPTIONAL wire field genuinely omitted by a
  // legacy/partial payload (`undefined`, not `null`) must still surface as
  // `null` in the flow node's data, never leak `undefined` through to
  // ChatNodeData's required-but-nullable `provider`/`model` props.
  it("normalizes an absent (undefined) provider/model to null, not undefined", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "Hello", provider: undefined, model: undefined })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    const data = chatFlowNode!.data as { provider: string | null; model: string | null };
    expect(data.provider).toBeNull();
    expect(data.model).toBeNull();
  });
});

// ADR-002 Workstream 1 ("Branch status and lifecycle"). ADR-003 stage 3.3
// (C9) put branchStatus/isFinalDeliverable directly on the generated
// SceneNodeRow type - this helper only exists now to apply overrides on
// top of a baseNode(), not to work around missing fields.
function withBranchLifecycleFields(
  node: SceneNodeRow,
  overrides: Partial<Pick<SceneNodeRow, "branchStatus" | "isFinalDeliverable">> = {},
): SceneNodeRow {
  return {
    ...node,
    branchStatus: "active",
    isFinalDeliverable: false,
    ...overrides,
  };
}

describe("toFlowNodes (ADR-002 Workstream 1 - Branch status and lifecycle)", () => {
  it("maps a chat node's branchStatus/isFinalDeliverable onto the flow node's data", () => {
    const scene = baseScene({
      nodes: [
        withBranchLifecycleFields(baseNode({ id: "chat-1", kind: "chat", content: "hi" }), {
          branchStatus: "accepted",
          isFinalDeliverable: true,
        }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode!.data).toMatchObject({ branchStatus: "accepted", isFinalDeliverable: true });
  });

  it("onSetBranchStatus calls store.setBranchStatus with this node's own id and the given status", () => {
    const scene = baseScene({
      nodes: [withBranchLifecycleFields(baseNode({ id: "chat-1", kind: "chat", content: "hi" }))],
      edges: [],
    });
    const store = makeStore();
    const spy = vi.spyOn(store, "setBranchStatus");

    const flowNodes = toFlowNodes(scene, store);
    const data = flowNodes.find((n) => n.id === "chat-1")!.data as { onSetBranchStatus: (status: string) => void };
    data.onSetBranchStatus("rejected");
    expect(spy).toHaveBeenCalledWith("chat-1", "rejected");
  });

  it("onSetFinalDeliverable calls store.setFinalDeliverable with this node's own id and the given flag", () => {
    const scene = baseScene({
      nodes: [withBranchLifecycleFields(baseNode({ id: "chat-1", kind: "chat", content: "hi" }))],
      edges: [],
    });
    const store = makeStore();
    const spy = vi.spyOn(store, "setFinalDeliverable");

    const flowNodes = toFlowNodes(scene, store);
    const data = flowNodes.find((n) => n.id === "chat-1")!.data as { onSetFinalDeliverable: (isFinal: boolean) => void };
    data.onSetFinalDeliverable(true);
    expect(spy).toHaveBeenCalledWith("chat-1", true);
  });

  it("onCollapseBranch calls store.collapseBranch with this node's own id and the given flag", () => {
    const scene = baseScene({
      nodes: [withBranchLifecycleFields(baseNode({ id: "chat-1", kind: "chat", content: "hi" }))],
      edges: [],
    });
    const store = makeStore();
    const spy = vi.spyOn(store, "collapseBranch");

    const flowNodes = toFlowNodes(scene, store);
    const data = flowNodes.find((n) => n.id === "chat-1")!.data as { onCollapseBranch: (collapsed: boolean) => void };
    data.onCollapseBranch(true);
    expect(spy).toHaveBeenCalledWith("chat-1", true);
  });
});

describe("computeNonAcceptedNodeIds (ADR-002 Workstream 1 - Focus Accepted Paths)", () => {
  function lifecycleNode(overrides: Partial<SceneNodeRow> = {}) {
    return withBranchLifecycleFields(baseNode(overrides), overrides);
  }

  it("returns an empty set when no chat node is rejected or superseded", () => {
    const scene = baseScene({
      nodes: [
        lifecycleNode({ id: "root", kind: "chat", branchStatus: "active" }),
        lifecycleNode({ id: "child", kind: "chat", branchStatus: "accepted" }),
      ],
      edges: [{ id: "e1", source: "root", target: "child" }],
    });
    expect(computeNonAcceptedNodeIds(scene)).toEqual(new Set());
  });

  it("excludes a rejected root and every one of its chat-kind descendants", () => {
    const scene = baseScene({
      nodes: [
        lifecycleNode({ id: "root", kind: "chat", branchStatus: "active" }),
        lifecycleNode({ id: "rejected", kind: "chat", branchStatus: "rejected" }),
        lifecycleNode({ id: "grandchild", kind: "chat", branchStatus: "active" }),
        lifecycleNode({ id: "other-branch", kind: "chat", branchStatus: "active" }),
      ],
      edges: [
        { id: "e1", source: "root", target: "rejected" },
        { id: "e2", source: "rejected", target: "grandchild" },
        { id: "e3", source: "root", target: "other-branch" },
      ],
    });
    const excluded = computeNonAcceptedNodeIds(scene);
    expect(excluded).toEqual(new Set(["rejected", "grandchild"]));
  });

  it("an explicit accepted override reactivates a sub-branch beneath a rejected ancestor", () => {
    const scene = baseScene({
      nodes: [
        lifecycleNode({ id: "root", kind: "chat", branchStatus: "active" }),
        lifecycleNode({ id: "rejected", kind: "chat", branchStatus: "rejected" }),
        lifecycleNode({ id: "reactivated", kind: "chat", branchStatus: "accepted" }),
        lifecycleNode({ id: "below-reactivated", kind: "chat", branchStatus: "active" }),
      ],
      edges: [
        { id: "e1", source: "root", target: "rejected" },
        { id: "e2", source: "rejected", target: "reactivated" },
        { id: "e3", source: "reactivated", target: "below-reactivated" },
      ],
    });
    const excluded = computeNonAcceptedNodeIds(scene);
    // "rejected" itself stays excluded, but its "accepted" child and
    // everything below that child is reactivated.
    expect(excluded).toEqual(new Set(["rejected"]));
  });

  it("pulls in non-chat content nodes via their chat anchor", () => {
    const scene = baseScene({
      nodes: [
        lifecycleNode({ id: "root", kind: "chat", branchStatus: "active" }),
        lifecycleNode({ id: "rejected", kind: "chat", branchStatus: "rejected" }),
        baseNode({ id: "code-child", kind: "code" }),
      ],
      edges: [
        { id: "e1", source: "root", target: "rejected" },
        { id: "e2", source: "rejected", target: "code-child" },
      ],
    });
    const excluded = computeNonAcceptedNodeIds(scene);
    expect(excluded.has("code-child")).toBe(true);
  });

  it("does not exclude a node whose branchStatus is superseded from touching an unrelated sibling branch", () => {
    const scene = baseScene({
      nodes: [
        lifecycleNode({ id: "root", kind: "chat", branchStatus: "active" }),
        lifecycleNode({ id: "superseded", kind: "chat", branchStatus: "superseded" }),
        lifecycleNode({ id: "sibling", kind: "chat", branchStatus: "active" }),
      ],
      edges: [
        { id: "e1", source: "root", target: "superseded" },
        { id: "e2", source: "root", target: "sibling" },
      ],
    });
    const excluded = computeNonAcceptedNodeIds(scene);
    expect(excluded).toEqual(new Set(["superseded"]));
  });

  // Found by adversarial review: a node with a genuinely healthy (canonical,
  // tie-break-winning) parent must never be excluded just because it ALSO
  // happens to receive a second, unrelated edge from a rejected node
  // elsewhere in the graph - SceneDocument.connect has no cycle/multi-parent
  // validation, so this is structurally reachable even though the UI has no
  // direct multi-parent-creation gesture (e.g. an unrelated manual edge).
  it("does not exclude a node via a second, non-canonical incoming edge from an unrelated rejected node", () => {
    const scene = baseScene({
      nodes: [
        lifecycleNode({ id: "healthy-root", kind: "chat", branchStatus: "active" }),
        lifecycleNode({ id: "shared", kind: "chat", branchStatus: "active" }),
        lifecycleNode({ id: "rejected", kind: "chat", branchStatus: "rejected" }),
      ],
      edges: [
        // "shared"'s canonical parent (first edge whose target is "shared").
        { id: "e1", source: "healthy-root", target: "shared" },
        // A second, unrelated incoming edge from a rejected node - must not
        // exclude "shared", since its real parent is healthy.
        { id: "e2", source: "rejected", target: "shared" },
      ],
    });
    const excluded = computeNonAcceptedNodeIds(scene);
    expect(excluded).toEqual(new Set(["rejected"]));
  });
});

describe("computeFilteredOutNodeIds (ADR-012 stage 12.5 - node filter-by-kind/status)", () => {
  it("returns an empty set when both filter sets are empty (feature off)", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "a", kind: "chat" }), baseNode({ id: "b", kind: "note" })],
    });
    expect(computeFilteredOutNodeIds(scene, new Set(), new Set())).toEqual(new Set());
  });

  it("excludes every node whose kind is not in a non-empty kind filter", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "chat-1", kind: "chat" }),
        baseNode({ id: "code-1", kind: "code" }),
        baseNode({ id: "note-1", kind: "note" }),
      ],
    });
    expect(computeFilteredOutNodeIds(scene, new Set(["chat", "note"]), new Set())).toEqual(new Set(["code-1"]));
  });

  it("excludes every node whose branchStatus is not in a non-empty status filter", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "active-1", kind: "chat", branchStatus: "active" }),
        baseNode({ id: "rejected-1", kind: "chat", branchStatus: "rejected" }),
        baseNode({ id: "accepted-1", kind: "code", branchStatus: "accepted" }),
      ],
    });
    expect(computeFilteredOutNodeIds(scene, new Set(), new Set(["active", "accepted"]))).toEqual(
      new Set(["rejected-1"]),
    );
  });

  it("ANDs both axes when both filters are active - a node must pass both to survive", () => {
    const scene = baseScene({
      nodes: [
        // Passes both: kind=code AND status=accepted.
        baseNode({ id: "keep", kind: "code", branchStatus: "accepted" }),
        // Fails kind only.
        baseNode({ id: "wrong-kind", kind: "note", branchStatus: "accepted" }),
        // Fails status only.
        baseNode({ id: "wrong-status", kind: "code", branchStatus: "rejected" }),
        // Fails both.
        baseNode({ id: "wrong-both", kind: "note", branchStatus: "rejected" }),
      ],
    });
    const excluded = computeFilteredOutNodeIds(scene, new Set(["code"]), new Set(["accepted"]));
    expect(excluded).toEqual(new Set(["wrong-kind", "wrong-status", "wrong-both"]));
  });

  it("never excludes frame/container nodes, even with an active kind filter that omits them", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "frame-1", kind: "frame" }),
        baseNode({ id: "container-1", kind: "container" }),
        baseNode({ id: "chat-1", kind: "chat" }),
      ],
    });
    const excluded = computeFilteredOutNodeIds(scene, new Set(["chat"]), new Set());
    expect(excluded).toEqual(new Set());
  });

  it("every kind FILTERABLE_NODE_KINDS lists round-trips through the filter unexcluded when selected alone", () => {
    for (const kind of FILTERABLE_NODE_KINDS) {
      const scene = baseScene({ nodes: [baseNode({ id: "n", kind })] });
      expect(computeFilteredOutNodeIds(scene, new Set([kind]), new Set())).toEqual(new Set());
    }
  });

  it("toFlowNodes' own filterKinds/filterStatuses params reach isDimmed's style output", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "chat-1", kind: "chat" }),
        baseNode({ id: "note-1", kind: "note" }),
      ],
    });
    const store = makeStore();

    // No filter active - neither node is dimmed.
    const unfiltered = toFlowNodes(
      scene, store, () => {}, null, () => {}, false, createToFlowNodesCache(), undefined, new Set(), new Set(),
    );
    expect(unfiltered.find((n) => n.id === "chat-1")?.style).toBeUndefined();
    expect(unfiltered.find((n) => n.id === "note-1")?.style).toBeUndefined();

    // Filtered to kind="chat" - the note node is dimmed, the chat node is not.
    const filtered = toFlowNodes(
      scene, store, () => {}, null, () => {}, false, createToFlowNodesCache(), undefined, new Set(["chat"]), new Set(),
    );
    expect(filtered.find((n) => n.id === "chat-1")?.style).toBeUndefined();
    expect(filtered.find((n) => n.id === "note-1")?.style).toEqual({ opacity: 0.18 });
  });
});

describe("toFlowNodes (R6.3 html node splitter state)", () => {
  it("maps a saved htmlSplitterState onto the flow node's data", () => {
    const scene = baseScene({
      nodes: [
        withR63Fields(baseNode({ id: "html-1", kind: "html", content: "<p>hi</p>" }), { htmlSplitterState: 0.35 }),
      ],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const htmlFlowNode = flowNodes.find((n) => n.id === "html-1");
    expect((htmlFlowNode!.data as { htmlSplitterState: number | null }).htmlSplitterState).toBe(0.35);
  });

  it("defaults htmlSplitterState to null for an ordinary html node", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "html-1", kind: "html", content: "<p>hi</p>" })], edges: [] });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const htmlFlowNode = flowNodes.find((n) => n.id === "html-1");
    expect((htmlFlowNode!.data as { htmlSplitterState: number | null }).htmlSplitterState).toBeNull();
  });

  it("onSplitterChange calls setHtmlSplitterState with this node's own id and the given value", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "html-1", kind: "html", content: "<p>hi</p>" })], edges: [] });
    const store = makeStore();
    const spy = vi.spyOn(store, "setHtmlSplitterState");

    const flowNodes = toFlowNodes(scene, store);
    const data = flowNodes.find((n) => n.id === "html-1")!.data as { onSplitterChange: (value: number) => void };
    data.onSplitterChange(0.6);
    expect(spy).toHaveBeenCalledWith("html-1", 0.6);
  });

  // ADR-003 stage 3.3 (C9) review-fix: the "defaults ... to null" test above
  // never actually exercises SceneCanvas.tsx's `n.htmlSplitterState ??
  // null` normalization - baseNode()'s own default is already `null`, so
  // that assertion would pass identically with the normalization deleted.
  // This proves it explicitly for a wire payload that omits the field
  // (`undefined`) rather than sending an explicit `null`.
  it("normalizes an absent (undefined) htmlSplitterState to null, not undefined", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "html-1", kind: "html", content: "<p>hi</p>", htmlSplitterState: undefined })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const htmlFlowNode = flowNodes.find((n) => n.id === "html-1");
    expect((htmlFlowNode!.data as { htmlSplitterState: number | null }).htmlSplitterState).toBeNull();
  });
});

describe("collectChangedNodeSizes (node-size reporting for group bounds)", () => {
  const sized = (sizes: Record<string, { width: number; height: number } | null>) =>
    (id: string) => sizes[id] ?? null;

  it("reports every measurable node the first time it is seen", () => {
    const last = new Map<string, string>();
    const changed = collectChangedNodeSizes(
      ["a", "b"],
      sized({ a: { width: 422, height: 112 }, b: { width: 680, height: 500 } }),
      last,
    );
    expect(changed).toEqual([
      ["a", 422, 112],
      ["b", 680, 500],
    ]);
  });

  it("reports nothing on a re-measure that produced identical sizes - the steady state costs no intent at all", () => {
    const last = new Map<string, string>();
    const measure = sized({ a: { width: 422, height: 112 } });
    collectChangedNodeSizes(["a"], measure, last);

    expect(collectChangedNodeSizes(["a"], measure, last)).toEqual([]);
  });

  it("reports only the node whose size actually moved", () => {
    const last = new Map<string, string>();
    collectChangedNodeSizes(
      ["a", "b"],
      sized({ a: { width: 422, height: 112 }, b: { width: 422, height: 300 } }),
      last,
    );

    const changed = collectChangedNodeSizes(
      ["a", "b"],
      sized({ a: { width: 422, height: 112 }, b: { width: 422, height: 900 } }),
      last,
    );
    expect(changed).toEqual([["b", 422, 900]]);
  });

  it("skips an unmeasurable node rather than reporting it as zero - an off-viewport node is unmounted, not shrunk", () => {
    const last = new Map<string, string>();
    collectChangedNodeSizes(["a"], sized({ a: { width: 422, height: 112 } }), last);

    expect(collectChangedNodeSizes(["a"], sized({ a: null }), last)).toEqual([]);
    // The last known size is retained, so scrolling back does not re-report.
    expect(collectChangedNodeSizes(["a"], sized({ a: { width: 422, height: 112 } }), last)).toEqual([]);
  });

  it("skips non-positive measurements - a node mid-mount must never collapse its group's box", () => {
    const last = new Map<string, string>();
    const changed = collectChangedNodeSizes(
      ["a", "b"],
      sized({ a: { width: 0, height: 0 }, b: { width: 422, height: 0 } }),
      last,
    );
    expect(changed).toEqual([]);
  });
});

describe("makeDebouncedViewportReport (R6.3 canvas pan/zoom reporting)", () => {
  it("does not call onReport until debounceMs have elapsed with no further calls", () => {
    vi.useFakeTimers();
    try {
      const onReport = vi.fn();
      const timerRef: { current: ReturnType<typeof setTimeout> | null } = { current: null };
      const debounced = makeDebouncedViewportReport(timerRef, onReport, 250);

      debounced(1.5, 100, 200);
      expect(onReport).not.toHaveBeenCalled();
      vi.advanceTimersByTime(249);
      expect(onReport).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(onReport).toHaveBeenCalledOnce();
      expect(onReport).toHaveBeenCalledWith(1.5, 100, 200);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a call before the debounce window elapses cancels the previous one - only the LAST viewport fires, never every intermediate pan/zoom frame", () => {
    vi.useFakeTimers();
    try {
      const onReport = vi.fn();
      const timerRef: { current: ReturnType<typeof setTimeout> | null } = { current: null };
      const debounced = makeDebouncedViewportReport(timerRef, onReport, 250);

      debounced(1, 0, 0); // frame 1 of a pan/zoom gesture
      vi.advanceTimersByTime(100);
      debounced(1.2, 40, 60); // frame 2, still mid-gesture
      vi.advanceTimersByTime(100);
      debounced(1.4, 80, 120); // frame 3 (the settled end position)
      vi.advanceTimersByTime(200);
      expect(onReport).not.toHaveBeenCalled(); // only 200ms since the LAST frame
      vi.advanceTimersByTime(50);
      expect(onReport).toHaveBeenCalledOnce();
      expect(onReport).toHaveBeenCalledWith(1.4, 80, 120);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("groupDragKindOf (R6.1 group-drag eligibility)", () => {
  function flowNode(overrides: Partial<SceneFlowNode> = {}): SceneFlowNode {
    return {
      id: "g1",
      type: "frame",
      position: { x: 0, y: 0 },
      data: { itemIds: [] },
      ...overrides,
    } as unknown as SceneFlowNode;
  }

  it("returns 'frame' for a locked frame", () => {
    expect(groupDragKindOf(flowNode({ type: "frame", data: { isLocked: true } } as Partial<SceneFlowNode>))).toBe(
      "frame",
    );
  });

  it("returns null for an unlocked frame", () => {
    expect(groupDragKindOf(flowNode({ type: "frame", data: { isLocked: false } } as Partial<SceneFlowNode>))).toBeNull();
  });

  it("returns 'container' unconditionally (no lock concept)", () => {
    expect(groupDragKindOf(flowNode({ type: "container", data: {} } as Partial<SceneFlowNode>))).toBe("container");
  });

  it("returns null for every other node kind", () => {
    expect(groupDragKindOf(flowNode({ type: "chat" } as Partial<SceneFlowNode>))).toBeNull();
  });

  it("returns null for undefined", () => {
    expect(groupDragKindOf(undefined)).toBeNull();
  });
});

describe("applyGroupDragDelta (R6.1 group-drag)", () => {
  it("a locked frame's drag carries every member by the identical delta", () => {
    const nodes: SceneFlowNode[] = [
      {
        id: "frame-1",
        type: "frame",
        position: { x: 100, y: 100 },
        data: { isLocked: true, itemIds: ["member-1", "member-2"] },
      } as unknown as SceneFlowNode,
      { id: "member-1", type: "chat", position: { x: 120, y: 140 }, data: {} } as unknown as SceneFlowNode,
      { id: "member-2", type: "code", position: { x: 300, y: 160 }, data: {} } as unknown as SceneFlowNode,
      { id: "unrelated", type: "chat", position: { x: 999, y: 999 }, data: {} } as unknown as SceneFlowNode,
    ];

    // The frame itself moved from (100,100) to (140,130): delta (40,30).
    const changes = applyGroupDragDelta(nodes, "frame-1", { x: 140, y: 130 });

    expect(changes).toHaveLength(2);
    const typedChanges = changes as unknown as Array<{ id: string; position: { x: number; y: number } }>;
    const byId = new Map(typedChanges.map((c) => [c.id, c]));
    expect(byId.get("member-1")!.position).toEqual({ x: 160, y: 170 });
    expect(byId.get("member-2")!.position).toEqual({ x: 340, y: 190 });
    // The unrelated node never appears in the output.
    expect(byId.has("unrelated")).toBe(false);
    for (const change of changes) {
      expect(change.type).toBe("position");
      expect((change as { dragging: boolean }).dragging).toBe(true);
    }
  });

  it("an unlocked frame's drag carries no members", () => {
    const nodes: SceneFlowNode[] = [
      {
        id: "frame-1",
        type: "frame",
        position: { x: 100, y: 100 },
        data: { isLocked: false, itemIds: ["member-1"] },
      } as unknown as SceneFlowNode,
      { id: "member-1", type: "chat", position: { x: 120, y: 140 }, data: {} } as unknown as SceneFlowNode,
    ];
    expect(applyGroupDragDelta(nodes, "frame-1", { x: 140, y: 130 })).toEqual([]);
  });

  it("a container's drag carries every member by the identical delta unconditionally", () => {
    const nodes: SceneFlowNode[] = [
      {
        id: "container-1",
        type: "container",
        position: { x: 0, y: 0 },
        data: { itemIds: ["member-1"] },
      } as unknown as SceneFlowNode,
      { id: "member-1", type: "note", position: { x: 50, y: 50 }, data: {} } as unknown as SceneFlowNode,
    ];
    const changes = applyGroupDragDelta(nodes, "container-1", { x: -10, y: 5 });
    expect(changes).toEqual([
      { id: "member-1", type: "position", dragging: true, position: { x: 40, y: 55 } },
    ]);
  });

  it("a plain (non-group) node's drag carries no members", () => {
    const nodes: SceneFlowNode[] = [
      { id: "chat-1", type: "chat", position: { x: 0, y: 0 }, data: {} } as unknown as SceneFlowNode,
    ];
    expect(applyGroupDragDelta(nodes, "chat-1", { x: 10, y: 10 })).toEqual([]);
  });

  it("skips a member id that no longer resolves to a live node", () => {
    const nodes: SceneFlowNode[] = [
      {
        id: "frame-1",
        type: "frame",
        position: { x: 0, y: 0 },
        data: { isLocked: true, itemIds: ["gone"] },
      } as unknown as SceneFlowNode,
    ];
    expect(applyGroupDragDelta(nodes, "frame-1", { x: 10, y: 10 })).toEqual([]);
  });

  it("R6.1 follow-up: dragging an outer container cascades into a NESTED group's own members too", () => {
    // Regression test: applyGroupDragDelta used to only shift a dragged
    // group's DIRECT itemIds by a flat delta - if a member was itself a
    // group (container-of-container, or a frame nested inside a
    // container), that inner group's own members never moved, visibly
    // desyncing it from its own contents. Nesting is legitimately
    // possible (create_container has no kind restriction).
    const nodes: SceneFlowNode[] = [
      {
        id: "outer-container",
        type: "container",
        position: { x: 0, y: 0 },
        data: { itemIds: ["inner-frame"] },
      } as unknown as SceneFlowNode,
      {
        id: "inner-frame",
        type: "frame",
        position: { x: 50, y: 50 },
        data: { isLocked: true, itemIds: ["leaf-1"] },
      } as unknown as SceneFlowNode,
      { id: "leaf-1", type: "chat", position: { x: 70, y: 90 }, data: {} } as unknown as SceneFlowNode,
    ];

    // The outer container moved from (0,0) to (10,20): delta (10,20).
    const changes = applyGroupDragDelta(nodes, "outer-container", { x: 10, y: 20 });

    const byId = new Map(
      (changes as unknown as Array<{ id: string; position: { x: number; y: number } }>).map((c) => [c.id, c]),
    );
    expect(byId.size).toBe(2);
    expect(byId.get("inner-frame")!.position).toEqual({ x: 60, y: 70 });
    // The innermost leaf moved by the SAME delta too, not left behind.
    expect(byId.get("leaf-1")!.position).toEqual({ x: 80, y: 110 });
  });

  it("R6.1 follow-up: a cycle in group membership never infinite-loops (defensive - creation-time validation should prevent this)", () => {
    const nodes: SceneFlowNode[] = [
      {
        id: "container-a",
        type: "container",
        position: { x: 0, y: 0 },
        data: { itemIds: ["container-b"] },
      } as unknown as SceneFlowNode,
      {
        id: "container-b",
        type: "container",
        position: { x: 10, y: 10 },
        data: { itemIds: ["container-a"] },
      } as unknown as SceneFlowNode,
    ];

    expect(() => applyGroupDragDelta(nodes, "container-a", { x: 5, y: 5 })).not.toThrow();
  });
});

describe("flowNodeOwnSize (ADR-011 stage 11.2 virtualization audit)", () => {
  it("returns the flow-node-level width/height for a frame/container/chart-shaped node", () => {
    const node = {
      id: "frame-1",
      type: "frame",
      position: { x: 0, y: 0 },
      width: 260,
      height: 140,
      data: {},
    } as unknown as SceneFlowNode;
    expect(flowNodeOwnSize(node)).toEqual({ width: 260, height: 140 });
  });

  it("returns null for a plain content node with no flow-node-level width/height", () => {
    const node = { id: "chat-1", type: "chat", position: { x: 0, y: 0 }, data: {} } as unknown as SceneFlowNode;
    expect(flowNodeOwnSize(node)).toBeNull();
  });

  it("returns null when only one of width/height is set (defensive - should be unreachable via toFlowNodes)", () => {
    const node = {
      id: "odd-1",
      type: "chart",
      position: { x: 0, y: 0 },
      width: 100,
      data: {},
    } as unknown as SceneFlowNode;
    expect(flowNodeOwnSize(node)).toBeNull();
  });
});

describe("buildDragSizeCache (ADR-011 stage 11.3 drag-start batch read)", () => {
  function fakeReactFlow(measured: Record<string, { width?: number; height?: number } | undefined> = {}): MeasuredSizeSource {
    return {
      getInternalNode: (id: string) => {
        const m = measured[id];
        return m ? { measured: m } : undefined;
      },
    };
  }

  it("uses flowNodeOwnSize for a frame/container/chart node WITHOUT ever touching the DOM", () => {
    const querySelectorSpy = vi.spyOn(document, "querySelector");
    const nodes: SceneFlowNode[] = [
      { id: "frame-1", type: "frame", position: { x: 0, y: 0 }, width: 200, height: 100, data: {} } as unknown as SceneFlowNode,
    ];
    const cache = buildDragSizeCache(fakeReactFlow(), nodes);
    expect(cache.get("frame-1")).toEqual({ width: 200, height: 100 });
    expect(querySelectorSpy).not.toHaveBeenCalled();
    querySelectorSpy.mockRestore();
  });

  it("falls back to measuredNodeSize's DOM query for a plain content node, exactly once per node", () => {
    const querySelectorSpy = vi.spyOn(document, "querySelector").mockReturnValue(null);
    const nodes: SceneFlowNode[] = [
      { id: "chat-1", type: "chat", position: { x: 0, y: 0 }, data: {} } as unknown as SceneFlowNode,
      { id: "chat-2", type: "chat", position: { x: 100, y: 0 }, data: {} } as unknown as SceneFlowNode,
    ];
    buildDragSizeCache(fakeReactFlow(), nodes);
    expect(querySelectorSpy).toHaveBeenCalledTimes(2);
    querySelectorSpy.mockRestore();
  });

  it("prefers React Flow's own internal `measured` cache over the DOM fallback when populated", () => {
    const querySelectorSpy = vi.spyOn(document, "querySelector");
    const nodes: SceneFlowNode[] = [
      { id: "chat-1", type: "chat", position: { x: 0, y: 0 }, data: {} } as unknown as SceneFlowNode,
    ];
    const cache = buildDragSizeCache(fakeReactFlow({ "chat-1": { width: 320, height: 90 } }), nodes);
    expect(cache.get("chat-1")).toEqual({ width: 320, height: 90 });
    expect(querySelectorSpy).not.toHaveBeenCalled();
    querySelectorSpy.mockRestore();
  });

  it("omits a node whose size cannot be resolved at all (off-viewport, unmounted, no server-tracked size)", () => {
    vi.spyOn(document, "querySelector").mockReturnValue(null);
    const nodes: SceneFlowNode[] = [
      { id: "chat-1", type: "chat", position: { x: 0, y: 0 }, data: {} } as unknown as SceneFlowNode,
    ];
    const cache = buildDragSizeCache(fakeReactFlow(), nodes);
    expect(cache.has("chat-1")).toBe(false);
    vi.restoreAllMocks();
  });
});

describe("computeSmartGuideFrame (ADR-011 stage 11.3 per-frame smart-guide read)", () => {
  it("passes the position through unsnapped, with no guides, when the moving node is not in `nodes`", () => {
    const result = computeSmartGuideFrame([], "gone", { x: 10, y: 10 }, new Map());
    expect(result).toEqual({ position: { x: 10, y: 10 }, guides: [] });
  });

  it("passes the position through unsnapped, with no guides, when the moving node's size is not in the cache", () => {
    const nodes: SceneFlowNode[] = [
      { id: "chat-1", type: "chat", position: { x: 0, y: 0 }, data: {} } as unknown as SceneFlowNode,
    ];
    const result = computeSmartGuideFrame(nodes, "chat-1", { x: 10, y: 10 }, new Map());
    expect(result).toEqual({ position: { x: 10, y: 10 }, guides: [] });
  });

  it("snaps against a cached candidate's left edge, reading sizes purely from the cache", () => {
    const nodes: SceneFlowNode[] = [
      { id: "moving", type: "chat", position: { x: 0, y: 0 }, data: {} } as unknown as SceneFlowNode,
      { id: "candidate", type: "chat", position: { x: 103, y: 300 }, data: {} } as unknown as SceneFlowNode,
    ];
    const cache = new Map([
      ["moving", { width: 100, height: 50 }],
      ["candidate", { width: 100, height: 50 }],
    ]);
    // moving's left edge (100) is within 5px of candidate's left edge (103).
    const result = computeSmartGuideFrame(nodes, "moving", { x: 100, y: 10 }, cache);
    expect(result.position.x).toBe(103);
    expect(result.guides).toHaveLength(1);
    expect(result.guides[0].orientation).toBe("vertical");
  });

  it("skips a candidate that is absent from the cache (off-viewport, unmounted) instead of crashing or estimating", () => {
    const nodes: SceneFlowNode[] = [
      { id: "moving", type: "chat", position: { x: 0, y: 0 }, data: {} } as unknown as SceneFlowNode,
      { id: "unmeasured", type: "chat", position: { x: 103, y: 300 }, data: {} } as unknown as SceneFlowNode,
    ];
    // Only "moving" has a cached size - "unmeasured" is exactly the
    // off-viewport-under-onlyRenderVisibleElements case.
    const cache = new Map([["moving", { width: 100, height: 50 }]]);
    const result = computeSmartGuideFrame(nodes, "moving", { x: 100, y: 10 }, cache);
    // No crash, and no snap against the unmeasured candidate.
    expect(result).toEqual({ position: { x: 100, y: 10 }, guides: [] });
  });

  it("excludes a dragged group's own members from candidates, same as the pre-11.3 inline logic did", () => {
    const nodes: SceneFlowNode[] = [
      {
        id: "frame-1",
        type: "frame",
        position: { x: 0, y: 0 },
        data: { isLocked: true, itemIds: ["member-1"] },
      } as unknown as SceneFlowNode,
      { id: "member-1", type: "chat", position: { x: 103, y: 300 }, data: {} } as unknown as SceneFlowNode,
    ];
    const cache = new Map([
      ["frame-1", { width: 100, height: 50 }],
      ["member-1", { width: 100, height: 50 }],
    ]);
    // member-1's left edge (103) would otherwise be within tolerance of
    // frame-1's proposed left edge (100) - but a group's own members are
    // never valid alignment candidates for the group dragging them.
    const result = computeSmartGuideFrame(nodes, "frame-1", { x: 100, y: 10 }, cache);
    expect(result).toEqual({ position: { x: 100, y: 10 }, guides: [] });
  });
});

describe("toFlowEdges (R7.5b-1 faded connections)", () => {
  const scene = baseScene({
    nodes: [baseNode({ id: "a" }), baseNode({ id: "b" }), baseNode({ id: "c" })],
    edges: [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "a", target: "c" },
    ],
  });

  it("applies no opacity style to any edge when fadeConnectionsEnabled is false, regardless of hover", () => {
    const off = baseScene({ ...scene, fadeConnectionsEnabled: false });
    expect(toFlowEdges(off, null)).toEqual([
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "a", target: "c" },
    ]);
    expect(toFlowEdges(off, "e1")).toEqual([
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "a", target: "c" },
    ]);
  });

  it("fades every edge except the hovered one when fadeConnectionsEnabled is true", () => {
    const on = baseScene({ ...scene, fadeConnectionsEnabled: true });
    const edges = toFlowEdges(on, "e1");
    expect(edges.find((e) => e.id === "e1")).toEqual({ id: "e1", source: "a", target: "b" });
    expect(edges.find((e) => e.id === "e2")).toEqual({
      id: "e2",
      source: "a",
      target: "c",
      style: { opacity: 0.08 },
    });
  });

  it("fades every edge when nothing is hovered", () => {
    const on = baseScene({ ...scene, fadeConnectionsEnabled: true });
    const edges = toFlowEdges(on, null);
    for (const edge of edges) {
      expect(edge.style).toEqual({ opacity: 0.08 });
    }
  });

  it("still suppresses an edge pointing at a docked node, independent of fade state", () => {
    const docked = baseScene({
      nodes: [baseNode({ id: "a" }), baseNode({ id: "b", isDocked: true })],
      edges: [{ id: "e1", source: "a", target: "b" }],
      fadeConnectionsEnabled: true,
    });
    expect(toFlowEdges(docked, null)).toEqual([]);
  });
});

describe("isOrthogonalEligible (R7.5b-2 orthogonal routing node-kind classification)", () => {
  it("is never eligible when the source is a note (legacy SystemPromptConnectionItem: always a fixed Bezier)", () => {
    expect(isOrthogonalEligible("note", "chat")).toBe(false);
  });

  it.each(["code", "document", "image", "thinking"])(
    "is never eligible when the target kind is %s (legacy: always a straight line)",
    (targetKind) => {
      expect(isOrthogonalEligible("chat", targetKind)).toBe(false);
    },
  );

  it.each(["chat", "conversation", "html"])(
    "is eligible when the target kind is %s and the source isn't a note (legacy: shares the ortho-gated update_path)",
    (targetKind) => {
      expect(isOrthogonalEligible("chat", targetKind)).toBe(true);
    },
  );

  it.each(["web_research", "artifact", "gitlink", "pycoder", "code_sandbox", "frame", "container", "chart", "note"])(
    "defaults to NOT eligible for %s targets - no legacy connection-type precedent exists for these kinds",
    (targetKind) => {
      expect(isOrthogonalEligible("chat", targetKind)).toBe(false);
    },
  );

  it("is not eligible when the target kind is unknown/undefined", () => {
    expect(isOrthogonalEligible("chat", undefined)).toBe(false);
  });
});

describe("toFlowEdges (R7.5b-2 orthogonal routing)", () => {
  it("leaves type undefined when orthogonalRouting is off, even for an eligible pair", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "a", kind: "chat" }), baseNode({ id: "b", kind: "chat" })],
      edges: [{ id: "e1", source: "a", target: "b" }],
      orthogonalRouting: false,
    });
    expect(toFlowEdges(scene, null)[0].type).toBeUndefined();
  });

  it("sets type to 'orthogonal' when the toggle is on and the pair is eligible", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "a", kind: "chat" }), baseNode({ id: "b", kind: "chat" })],
      edges: [{ id: "e1", source: "a", target: "b" }],
      orthogonalRouting: true,
    });
    expect(toFlowEdges(scene, null)[0].type).toBe("orthogonal");
  });

  it("leaves type undefined when the toggle is on but the pair is ineligible (e.g. targeting a code node)", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "a", kind: "chat" }), baseNode({ id: "b", kind: "code" })],
      edges: [{ id: "e1", source: "a", target: "b" }],
      orthogonalRouting: true,
    });
    expect(toFlowEdges(scene, null)[0].type).toBeUndefined();
  });

  it("composes with faded connections - an orthogonal edge still dims when both toggles are on", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "a", kind: "chat" }), baseNode({ id: "b", kind: "chat" })],
      edges: [{ id: "e1", source: "a", target: "b" }],
      orthogonalRouting: true,
      fadeConnectionsEnabled: true,
    });
    const edge = toFlowEdges(scene, null)[0];
    expect(edge.type).toBe("orthogonal");
    expect(edge.style).toEqual({ opacity: 0.08 });
  });
});

// R7.5c: found live, not by a test - Ctrl+Arrow's setCenter round-trips a
// viewport report through the backend, the echoed snapshot rebuilt every
// node, and the selection the keystroke had just made disappeared. Without
// this, branch navigation worked for exactly one hop. Extended later to
// also carry React Flow's measured node size across the same rebuild - see
// withPreservedFlowState's own doc comment for the node/edge blink that
// losing it caused.
describe("withPreservedFlowState (snapshot-rebuild selection + measurement wipe)", () => {
  const node = (id: string, selected?: boolean, measured?: { width: number; height: number }) =>
    ({ id, selected, measured, position: { x: 0, y: 0 }, data: {} }) as unknown as SceneFlowNode;

  it("re-applies the selection onto the freshly rebuilt nodes", () => {
    const rebuilt = [node("a"), node("b"), node("c")];
    const current = [node("a"), node("b", true), node("c")];
    const merged = withPreservedFlowState(rebuilt, current);
    expect(merged.map((n) => [n.id, !!n.selected])).toEqual([
      ["a", false],
      ["b", true],
      ["c", false],
    ]);
  });

  it("preserves a multi-node selection, not just a single id", () => {
    const merged = withPreservedFlowState(
      [node("a"), node("b"), node("c")],
      [node("a", true), node("b"), node("c", true)],
    );
    expect(merged.filter((n) => n.selected).map((n) => n.id)).toEqual(["a", "c"]);
  });

  it("returns the rebuilt array untouched when nothing needs carrying over", () => {
    const rebuilt = [node("a"), node("b")];
    expect(withPreservedFlowState(rebuilt, [node("a"), node("b")])).toBe(rebuilt);
  });

  it("cannot resurrect a node the backend deleted - it is simply absent from the rebuild", () => {
    const merged = withPreservedFlowState([node("a")], [node("a"), node("gone", true)]);
    expect(merged.map((n) => n.id)).toEqual(["a"]);
    expect(merged.some((n) => n.selected)).toBe(false);
  });

  it("does not mutate the node objects it was handed", () => {
    const current = [node("a", true)];
    const rebuilt = [node("a")];
    withPreservedFlowState(rebuilt, current);
    expect(rebuilt[0].selected).toBeUndefined();
  });

  it("carries the measured size from the current node onto a rebuilt replacement", () => {
    const measured = { width: 420, height: 180 };
    const merged = withPreservedFlowState([node("a")], [node("a", false, measured)]);
    expect(merged[0].measured).toEqual(measured);
  });

  it("carries selection and measured size together in one clone", () => {
    const measured = { width: 300, height: 120 };
    const merged = withPreservedFlowState([node("a")], [node("a", true, measured)]);
    expect(merged[0].selected).toBe(true);
    expect(merged[0].measured).toEqual(measured);
  });

  it("keeps a reference-identical node untouched instead of cloning it", () => {
    // A toFlowNodes cache hit hands back the exact object React Flow already
    // adopted - cloning it would defeat adoptUserNodes' reference-equality
    // fast path, so it must pass through by reference even when it carries
    // state worth preserving.
    const same = node("a", true, { width: 100, height: 50 });
    const merged = withPreservedFlowState([same], [same]);
    expect(merged[0]).toBe(same);
  });

  it("does not invent a measured size the current node never had", () => {
    const merged = withPreservedFlowState([node("a")], [node("a", true)]);
    expect(merged[0].measured).toBeUndefined();
  });

  it("prefers the rebuilt node's own measured size when it already has one", () => {
    const rebuiltMeasured = { width: 999, height: 999 };
    const merged = withPreservedFlowState(
      [node("a", false, rebuiltMeasured)],
      [node("a", true, { width: 1, height: 1 })],
    );
    expect(merged[0].measured).toEqual(rebuiltMeasured);
  });
});

// -- R8a: "Hide Other Branches" (computeDimmedNodeIds + its toFlowNodes wiring) --
//
// This is the one genuinely algorithmic piece of R8a's four-item deferred-
// menu-item cleanup - real ancestor/descendant graph traversal, not just
// callback threading - so it gets deliberately heavier coverage than a
// typical wiring test: every scenario below was hand-traced against the
// implementation before being written down here as an assertion, not
// reverse-engineered from whatever the code happened to produce.

describe("computeDimmedNodeIds (R8a Hide Other Branches)", () => {
  it("returns an empty set when focus is off (originId is null)", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat" })],
      edges: [],
    });
    expect(computeDimmedNodeIds(scene, null)).toEqual(new Set());
  });

  it("returns an empty set when the origin node no longer exists (deleted while focus was active)", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat" })],
      edges: [],
    });
    expect(computeDimmedNodeIds(scene, "chat-gone")).toEqual(new Set());
  });

  it("dims nothing for a lone chat node with no siblings", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat" })],
      edges: [],
    });
    expect(computeDimmedNodeIds(scene, "chat-1")).toEqual(new Set());
  });

  it("isolates a sibling branch: two chats sharing one parent, focused from one, dims only the other", () => {
    // A -> B, A -> B2 (a real fork - B and B2 are independent children of A).
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "B", kind: "chat" }),
        baseNode({ id: "B2", kind: "chat" }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "A", target: "B2" },
      ],
    });
    expect(computeDimmedNodeIds(scene, "B")).toEqual(new Set(["B2"]));
  });

  it("a linear chain (A -> B -> C) focused from B keeps both the ancestor and the descendant visible", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "B", kind: "chat" }),
        baseNode({ id: "C", kind: "chat" }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "B", target: "C" },
      ],
    });
    expect(computeDimmedNodeIds(scene, "B")).toEqual(new Set());
  });

  it("a content node (code) attached to an active chat node is not dimmed", () => {
    // A -> B, B -> X (code, attached to B). Focused from B: B's own content
    // node must stay visible, matching legacy's parent_content_node anchor.
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "B", kind: "chat" }),
        baseNode({ id: "X", kind: "code" }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "B", target: "X" },
      ],
    });
    expect(computeDimmedNodeIds(scene, "B")).toEqual(new Set());
  });

  it("a content node attached to a DIMMED sibling branch is itself dimmed", () => {
    // A -> B, A -> B2, B2 -> X (code, attached to the sibling branch B2).
    // Focused from B: X must be dimmed along with its owner B2.
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "B", kind: "chat" }),
        baseNode({ id: "B2", kind: "chat" }),
        baseNode({ id: "X", kind: "code" }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "A", target: "B2" },
        { id: "e3", source: "B2", target: "X" },
      ],
    });
    expect(computeDimmedNodeIds(scene, "B")).toEqual(new Set(["B2", "X"]));
  });

  it("descends through the FULL downstream chain, including a content node several hops down", () => {
    // A -> B -> C, C -> D (document, attached to C). Focused from the root A.
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "B", kind: "chat" }),
        baseNode({ id: "C", kind: "chat" }),
        baseNode({ id: "D", kind: "document" }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "B", target: "C" },
        { id: "e3", source: "C", target: "D" },
      ],
    });
    expect(computeDimmedNodeIds(scene, "A")).toEqual(new Set());
  });

  it("focusing FROM a content node resolves to its parent chat node's branch, not just itself", () => {
    // A -> B, A -> B2, B -> X (code). Right-clicking X's own menu must
    // isolate B's branch (X's owner), dimming B2 exactly as focusing from B
    // directly would - matches legacy's _branch_anchor_nodes mapping a
    // content node to its parent_content_node.
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "B", kind: "chat" }),
        baseNode({ id: "B2", kind: "chat" }),
        baseNode({ id: "X", kind: "code" }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "A", target: "B2" },
        { id: "e3", source: "B", target: "X" },
      ],
    });
    expect(computeDimmedNodeIds(scene, "X")).toEqual(new Set(["B2"]));
  });

  it("an orphaned content node with no chat parent isolates only itself, without crashing", () => {
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "orphan", kind: "code" }),
      ],
      edges: [],
    });
    expect(computeDimmedNodeIds(scene, "orphan")).toEqual(new Set(["A"]));
  });

  it("never dims a node kind outside the five that carry this menu item, regardless of branch membership", () => {
    // A -> B, A -> B2 (dimmed sibling), plus a conversation node and a note
    // with no edges at all - both must be left alone by this feature
    // entirely (ConversationNodeView.tsx's own docstring documents the
    // conversation exclusion as deliberate).
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "B", kind: "chat" }),
        baseNode({ id: "B2", kind: "chat" }),
        baseNode({ id: "conv-1", kind: "conversation" }),
        baseNode({ id: "note-1", kind: "note" }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "A", target: "B2" },
      ],
    });
    expect(computeDimmedNodeIds(scene, "B")).toEqual(new Set(["B2"]));
  });

  it("terminates and produces a sane result on a manually-created cycle (A -> B -> A), rather than hanging", () => {
    // SceneDocument.connect() has no cycle prevention (backend/canvas.py),
    // unlike legacy's QGraphicsScene-constrained edges - this is a
    // deliberate hardening test, not a scenario legacy itself could reach.
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "B", kind: "chat" }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "B", target: "A" },
      ],
    });
    expect(computeDimmedNodeIds(scene, "A")).toEqual(new Set());
  });

  it("a docked node can still be dimmed by the algorithm itself (toFlowNodes filters docked nodes out separately)", () => {
    // computeDimmedNodeIds has no isDocked awareness of its own - docked-node
    // exclusion from the rendered canvas is toFlowNodes' own concern (the
    // top-of-loop `if (n.isDocked) continue` guard), same separation of
    // concerns as every other per-kind field in that function. This test
    // exists to pin that computeDimmedNodeIds does not silently duplicate
    // that filtering itself.
    const scene = baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat" }),
        baseNode({ id: "B", kind: "chat" }),
        baseNode({ id: "B2", kind: "chat", isDocked: true }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "A", target: "B2" },
      ],
    });
    expect(computeDimmedNodeIds(scene, "B")).toEqual(new Set(["B2"]));
  });
});

describe("toFlowNodes (R8a Hide Other Branches wiring)", () => {
  function sceneWithFork() {
    return baseScene({
      nodes: [
        baseNode({ id: "A", kind: "chat", x: 0, y: 0 }),
        baseNode({ id: "B", kind: "chat", x: 0, y: 0 }),
        baseNode({ id: "B2", kind: "chat", x: 0, y: 0 }),
        baseNode({ id: "X", kind: "code", x: 0, y: 0 }),
        baseNode({ id: "Y", kind: "document", x: 0, y: 0 }),
        baseNode({ id: "Z", kind: "thinking", x: 0, y: 0 }),
        baseNode({ id: "W", kind: "image", x: 0, y: 0 }),
      ],
      edges: [
        { id: "e1", source: "A", target: "B" },
        { id: "e2", source: "A", target: "B2" },
        { id: "e3", source: "B", target: "X" },
        { id: "e4", source: "B", target: "Y" },
        { id: "e5", source: "B", target: "Z" },
        { id: "e6", source: "B", target: "W" },
      ],
    });
  }

  it("applies the dim opacity style to a dimmed node of each of the five participating kinds, and no style to active ones", () => {
    const scene = sceneWithFork();
    const store = makeStore();
    const flowNodes = toFlowNodes(scene, store, () => {}, "B", () => {});

    // B2 is the dimmed sibling - the only dimmed node in this fixture.
    expect(flowNodes.find((n) => n.id === "B2")?.style).toEqual({ opacity: 0.18 });
    // Every kind attached to the active branch (B) stays undimmed - no
    // style override at all, not an explicit opacity: 1.
    for (const id of ["A", "B", "X", "Y", "Z", "W"]) {
      expect(flowNodes.find((n) => n.id === id)?.style).toBeUndefined();
    }
  });

  it("applies no style to anyone when branch focus is off (default/omitted argument)", () => {
    const scene = sceneWithFork();
    const store = makeStore();
    // Two-argument call - the pre-R8a call shape - must still work and dim
    // nothing, matching every other backward-compat check in this file.
    const flowNodes = toFlowNodes(scene, store);
    for (const n of flowNodes) expect(n.style).toBeUndefined();
  });

  it("isBranchFocusActive is true scene-wide for all five kinds once focus is active anywhere, regardless of which node is dimmed", () => {
    const scene = sceneWithFork();
    const store = makeStore();
    const flowNodes = toFlowNodes(scene, store, () => {}, "B", () => {});
    for (const id of ["A", "B", "B2", "X", "Y", "Z", "W"]) {
      const data = flowNodes.find((n) => n.id === id)?.data as { isBranchFocusActive: boolean };
      expect(data.isBranchFocusActive).toBe(true);
    }
  });

  it("isBranchFocusActive is false for all five kinds when focus is off", () => {
    const scene = sceneWithFork();
    const store = makeStore();
    const flowNodes = toFlowNodes(scene, store, () => {}, null, () => {});
    for (const id of ["A", "B", "B2", "X", "Y", "Z", "W"]) {
      const data = flowNodes.find((n) => n.id === id)?.data as { isBranchFocusActive: boolean };
      expect(data.isBranchFocusActive).toBe(false);
    }
  });

  it("each of the five kinds' onToggleBranchFocus calls the outer callback with its OWN node id, not some other node's", () => {
    const scene = sceneWithFork();
    const store = makeStore();
    const calls: string[] = [];
    const flowNodes = toFlowNodes(scene, store, () => {}, null, (nodeId) => calls.push(nodeId));

    for (const id of ["A", "B", "X", "Y", "Z", "W"]) {
      const data = flowNodes.find((n) => n.id === id)?.data as { onToggleBranchFocus: () => void };
      data.onToggleBranchFocus();
    }
    expect(calls).toEqual(["A", "B", "X", "Y", "Z", "W"]);
  });
});

// ADR-003 stage 3.5: the exported <SceneCanvas> wrapper - not <CanvasInner> -
// is where the version-rejection gate lives (see SceneCanvas.tsx's own
// comment on it), so this is the first test in this file to actually
// render a component rather than exercise a pure exported function. Kept
// to exactly that one behavior (rejected -> the error, not rejected -> the
// error is absent) rather than also re-proving CanvasInner's own rendering,
// which the rest of this file already covers indirectly through
// toFlowNodes/toFlowEdges.
describe("SceneCanvas version-rejection gate (ADR-003 stage 3.5)", () => {
  type VersionRejectionListener = (rejection: BridgeRejection | null) => void;

  type StateListener = (payload: Record<string, unknown>) => void;

  function makeStoreWithVersionControl() {
    const versionRejectionListeners = new Map<string, VersionRejectionListener>();
    const stateListeners = new Map<string, StateListener>();
    const transport = {
      subscribe: vi.fn((topic: string, listener: StateListener) => {
        stateListeners.set(topic, listener);
        return () => stateListeners.delete(topic);
      }),
      intent: vi.fn(),
      fireIntent: vi.fn(),
      subscribePatch: vi.fn(),
      onVersionRejection: vi.fn((topic: string, listener: VersionRejectionListener) => {
        versionRejectionListeners.set(topic, listener);
        listener(null);
        return () => versionRejectionListeners.delete(topic);
      }),
      // ADR-003 stage 3.5 review-fix: connect() calls this unconditionally.
      setTopicBlocked: vi.fn(),
    } as unknown as WsTransport;
    const store = new SceneStore(transport);
    store.connect();
    return { store, versionRejectionListeners, stateListeners };
  }

  it("renders BridgeErrorState, with the server's own reason, instead of the canvas when the scene topic is rejected", () => {
    const { store, versionRejectionListeners } = makeStoreWithVersionControl();
    versionRejectionListeners.get("scene")!({
      kind: "version",
      reason: "The desktop app requires an interface of at least schema version 2.",
      details: [],
    });

    render(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Canvas unavailable");
    expect(alert).toHaveTextContent("The desktop app requires an interface of at least schema version 2.");
    // The version-specific hint, not the generic bug one - see
    // BridgeErrorState.tsx's own kind-branch.
    expect(alert).toHaveTextContent("Rebuilding the app's interface assets usually resolves this.");
  });

  it("renders no alert when the scene topic has not been rejected", () => {
    const { store } = makeStoreWithVersionControl();

    render(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a real recovering snapshot clears the error and the canvas takes over", () => {
    // Review-fix: recovery is proven with a REAL scene snapshot landing
    // (the store's own bind("scene", ...) callback, which is what actually
    // confirms the client is caught up), not by firing the raw
    // version-rejection callback alone. The wire-level rejection clearing
    // by itself is deliberately NOT enough to unblock the canvas any more
    // - see the next test for exactly why.
    const { store, versionRejectionListeners, stateListeners } = makeStoreWithVersionControl();
    versionRejectionListeners.get("scene")!({ kind: "version", reason: "too old", details: [] });

    render(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();

    // The store's emit() fires synchronously from a plain callback, outside
    // any React event handler - act() is what makes React flush the
    // resulting re-render before the assertion below reads the DOM.
    act(() => {
      versionRejectionListeners.get("scene")!(null);
      stateListeners.get("scene")!(baseScene() as unknown as Record<string, unknown>);
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("review-fix: the blocking error stays up through the recovery window - clearing the wire-level rejection ALONE does not unblock the canvas", () => {
    // This is the exact race a 4-lens adversarial review found: WsTransport
    // clears the version rejection the instant a COMPATIBLE frame arrives,
    // synchronously and BEFORE dispatching that frame to any listener - so
    // if the canvas gated on the raw rejection signal alone, it would
    // briefly mount against whatever stale `scene` data the store was
    // still holding from before the outage, for the one tick between the
    // rejection clearing and the store actually catching up. Proven here
    // by clearing ONLY the rejection (never delivering a snapshot/patch
    // that would let the store confirm it's caught up) and asserting the
    // error is still shown, not silently swapped for a stale canvas.
    const { store, versionRejectionListeners } = makeStoreWithVersionControl();
    versionRejectionListeners.get("scene")!({ kind: "version", reason: "too old", details: [] });

    render(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();

    act(() => {
      versionRejectionListeners.get("scene")!(null);
    });

    // Still blocked - recovering, not yet confirmed caught up. If this
    // were null, CanvasInner would have just mounted against stale data.
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(store.getSceneBlockingRejection()).not.toBeNull();
  });
});

describe("SceneCanvas empty-canvas hint (ADR-012 stage 12.6)", () => {
  type StateListener = (payload: Record<string, unknown>) => void;

  // Same minimal transport shape as makeStoreWithVersionControl above (a
  // sibling, not reused, since it is scoped to that describe block) - just
  // enough of WsTransport's surface for SceneCanvas to mount and for a test
  // to push a real scene snapshot afterward.
  function makeStoreForEmptyHint() {
    const stateListeners = new Map<string, StateListener>();
    const transport = {
      subscribe: vi.fn((topic: string, listener: StateListener) => {
        stateListeners.set(topic, listener);
        return () => stateListeners.delete(topic);
      }),
      intent: vi.fn(),
      fireIntent: vi.fn(),
      subscribePatch: vi.fn(),
      onVersionRejection: vi.fn((_topic: string, listener: (r: BridgeRejection | null) => void) => {
        listener(null);
        return () => {};
      }),
      setTopicBlocked: vi.fn(),
    } as unknown as WsTransport;
    const store = new SceneStore(transport);
    store.connect();
    return { store, transport, stateListeners };
  }

  const HINT_TEXT = "Type a message to start, or load the sample workspace.";

  it("renders the empty-canvas hint when the scene has no nodes", () => {
    const { store } = makeStoreForEmptyHint();

    render(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );

    expect(screen.getByText(HINT_TEXT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load Sample Workspace" })).toBeInTheDocument();
  });

  it("hides the empty-canvas hint once the scene has a real node", () => {
    const { store, stateListeners } = makeStoreForEmptyHint();

    render(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );
    expect(screen.getByText(HINT_TEXT)).toBeInTheDocument();

    act(() => {
      stateListeners.get("scene")!(
        baseScene({ nodes: [baseNode({ id: "n1" })] }) as unknown as Record<string, unknown>,
      );
    });

    expect(screen.queryByText(HINT_TEXT)).toBeNull();
  });

  it("the Load Sample Workspace button fires the scene-topic loadSampleWorkspace intent", () => {
    const { store, transport } = makeStoreForEmptyHint();

    render(
      <ReactFlowProvider>
        <SceneCanvas store={store} onOpenDocumentView={() => {}} />
      </ReactFlowProvider>,
    );

    screen.getByRole("button", { name: "Load Sample Workspace" }).click();
    expect(transport.fireIntent).toHaveBeenCalledWith("scene", "loadSampleWorkspace", []);
  });
});
