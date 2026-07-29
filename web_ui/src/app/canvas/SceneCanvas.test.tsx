import { describe, expect, it, vi } from "vitest";
import {
  applyGroupDragDelta,
  computeDimmedNodeIds,
  conversationHistoryToDocumentMarkdown,
  groupDragKindOf,
  handleSelectionChange,
  isOrthogonalEligible,
  makeDebouncedViewportReport,
  toFlowEdges,
  toFlowNodes,
  withPreservedSelection,
  type SceneFlowNode,
} from "./SceneCanvas";
import type { ConversationMessage } from "./ConversationNodeView";
import { SceneStore, initialSceneState } from "./sceneStore";
import type { WsTransport } from "../../lib/ws/transport";
import type { SceneNodeRow, SceneState } from "../../lib/bridge-core/generated/scene-state";

// toFlowNodes is exported standalone specifically so this doesn't need a
// full <ReactFlow> mount (same reasoning as sceneStore.test.ts's direct
// scaleDragPosition coverage) - see SceneCanvas.tsx's own comment on the
// export.

function makeStore(): SceneStore {
  const transport = { subscribe: vi.fn(), intent: vi.fn() } as unknown as WsTransport;
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
    codeSandboxPrompt: "",
    codeSandboxCode: "",
    codeSandboxOutput: "",
    codeSandboxAnalysis: "",
    codeSandboxAwaitingApproval: false,
    codeSandboxError: "",
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
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello there" },
    ];
    expect(conversationHistoryToDocumentMarkdown(history)).toBe(
      "## Conversation Transcript\n\n### 1. User\n\nhi\n\n### 2. Assistant\n\nhello there",
    );
  });

  it("skips a blank message but its number still counts (legacy enumerate-before-filter behavior)", () => {
    const history: ConversationMessage[] = [
      { role: "user", content: "first" },
      { role: "assistant", content: "   " },
      { role: "user", content: "third" },
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
      { role: "user", content: "" },
      { role: "assistant", content: "   " },
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
    expect(onOpenDocumentView).toHaveBeenCalledWith("Hello world");
  });

  it("a chat node with blank/whitespace-only content does NOT invoke the callback", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "chat-1", kind: "chat", content: "   " })],
      edges: [],
    });
    const store = makeStore();
    const onOpenDocumentView = vi.fn();

    const flowNodes = toFlowNodes(scene, store, onOpenDocumentView);
    const chatFlowNode = flowNodes.find((n) => n.id === "chat-1");
    expect(chatFlowNode).toBeDefined();

    (chatFlowNode!.data as { onOpenDocumentView: () => void }).onOpenDocumentView();
    expect(onOpenDocumentView).not.toHaveBeenCalled();
  });

  it("a conversation node's onOpenDocumentView invokes the callback with the properly formatted transcript", () => {
    const history: ConversationMessage[] = [
      { role: "user", content: "hi" },
      { role: "assistant", content: "hello there" },
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
    );
  });

  it("a conversation node with an empty history does NOT invoke the callback", () => {
    const scene = baseScene({
      nodes: [baseNode({ id: "conv-1", kind: "conversation", history: [] })],
      edges: [],
    });
    const store = makeStore();
    const onOpenDocumentView = vi.fn();

    const flowNodes = toFlowNodes(scene, store, onOpenDocumentView);
    const conversationFlowNode = flowNodes.find((n) => n.id === "conv-1");
    expect(conversationFlowNode).toBeDefined();

    (conversationFlowNode!.data as { onOpenDocumentView: () => void }).onOpenDocumentView();
    expect(onOpenDocumentView).not.toHaveBeenCalled();
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
      { role: "user" as const, content: "Draft a project proposal" },
      { role: "assistant" as const, content: "# Proposal\n\nHere is a draft." },
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

  it("onFetchRepositories/onLoadTree/onSetLocalRoot/onImportSnapshot/onBuildContext/onFetchContext/onRun/onApply all resolve to this node's id", () => {
    const scene = baseScene({ nodes: [baseNode({ id: "gl-1", kind: "gitlink" })], edges: [] });
    const store = makeStore();
    const fetchReposSpy = vi.spyOn(store, "fetchGitlinkRepositories").mockResolvedValue([]);
    const loadTreeSpy = vi.spyOn(store, "loadGitlinkRepoTree");
    const setRootSpy = vi.spyOn(store, "setGitlinkLocalRoot");
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

// R6.1: Notes/Frames/Containers. The generated SceneNodeRow type hasn't been
// regenerated yet to carry color/headerColor/isSystemPrompt/isSummaryNote/
// itemIds/isLocked/groupWidth/groupHeight (see SceneCanvas.tsx's own
// SceneNodeGroupFields comment) - this local helper builds a baseNode() with
// those extra fields layered on, the test-file equivalent of that same cast.
interface GroupTestFields {
  color: string | null;
  headerColor: string | null;
  isSystemPrompt: boolean;
  isSummaryNote: boolean;
  itemIds: string[];
  isLocked: boolean;
  groupWidth: number | null;
  groupHeight: number | null;
}

function groupNode(overrides: Partial<SceneNodeRow & GroupTestFields> = {}): SceneNodeRow & GroupTestFields {
  return {
    ...baseNode(),
    color: null,
    headerColor: null,
    isSystemPrompt: false,
    isSummaryNote: false,
    itemIds: [],
    isLocked: true,
    groupWidth: null,
    groupHeight: null,
    ...overrides,
  } as SceneNodeRow & GroupTestFields;
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

  it("an UNLOCKED frame gets draggable:false", () => {
    const scene = baseScene({
      nodes: [groupNode({ id: "frame-1", kind: "frame", isLocked: false, groupWidth: 300, groupHeight: 200 })],
      edges: [],
    });
    const store = makeStore();

    const flowNodes = toFlowNodes(scene, store);
    const frameFlowNode = flowNodes.find((n) => n.id === "frame-1");
    expect(frameFlowNode!.draggable).toBe(false);
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
});

// R6.2: Chart node. The generated SceneNodeRow type hasn't been regenerated
// yet to carry chartType/chartData/chartError/chartAssetId/
// chartAssetVersion/chartWidth/chartHeight/chartAspectLocked/
// chartSourceNodeId (see SceneCanvas.tsx's own SceneNodeChartFields
// comment) - same situation GroupTestFields above solves for R6.1.
interface ChartTestFields {
  chartType: string;
  chartData: Record<string, unknown>;
  chartError: string;
  chartAssetId: string;
  chartAssetVersion: number;
  chartWidth: number;
  chartHeight: number;
  chartAspectLocked: boolean;
  chartSourceNodeId: string;
}

function chartNode(overrides: Partial<SceneNodeRow & ChartTestFields> = {}): SceneNodeRow & ChartTestFields {
  return {
    ...baseNode(),
    kind: "chart",
    chartType: "bar",
    chartData: { type: "bar", title: "Revenue" },
    chartError: "",
    chartAssetId: "asset-chart-1",
    chartAssetVersion: 1,
    chartWidth: 680,
    chartHeight: 500,
    chartAspectLocked: true,
    chartSourceNodeId: "chat-1",
    ...overrides,
  } as SceneNodeRow & ChartTestFields;
}

describe("toFlowNodes (R6.2 chart node)", () => {
  it("maps all 9 chart wire fields onto the flow node's data, and chartWidth/chartHeight ALSO onto the flow node object itself (NodeResizer controlled-mode)", () => {
    const scene = baseScene({
      nodes: [
        chartNode({
          id: "chart-1",
          x: 10,
          y: 20,
          chartType: "sankey",
          chartData: { type: "sankey", title: "Flow" },
          chartError: "used a placeholder chart",
          chartAssetId: "asset-9",
          chartAssetVersion: 4,
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
      chartAssetId: "asset-9",
      chartAssetVersion: 4,
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

// R6.3: Scene-level serialization gaps. Same situation as ChartTestFields
// above - htmlSplitterState/chatScrollValue aren't in the generated
// SceneNodeRow type yet (see SceneCanvas.tsx's own SceneNodeR63Fields
// comment).
interface R63TestFields {
  htmlSplitterState: number | null;
  chatScrollValue: number;
}

function withR63Fields(node: SceneNodeRow, overrides: Partial<R63TestFields> = {}): SceneNodeRow & R63TestFields {
  return {
    ...node,
    htmlSplitterState: null,
    chatScrollValue: 0,
    ...overrides,
  } as SceneNodeRow & R63TestFields;
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

  it("defaults chatScrollValue to 0 when the field is absent (ahead of codegen regenerating SceneNodeRow)", () => {
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

  it("defaults htmlSplitterState to null when the field is absent (ahead of codegen regenerating SceneNodeRow)", () => {
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
// this, branch navigation worked for exactly one hop.
describe("withPreservedSelection (R7.5c snapshot-rebuild selection wipe)", () => {
  const node = (id: string, selected?: boolean) =>
    ({ id, selected, position: { x: 0, y: 0 }, data: {} }) as unknown as SceneFlowNode;

  it("re-applies the selection onto the freshly rebuilt nodes", () => {
    const rebuilt = [node("a"), node("b"), node("c")];
    const current = [node("a"), node("b", true), node("c")];
    const merged = withPreservedSelection(rebuilt, current);
    expect(merged.map((n) => [n.id, !!n.selected])).toEqual([
      ["a", false],
      ["b", true],
      ["c", false],
    ]);
  });

  it("preserves a multi-node selection, not just a single id", () => {
    const merged = withPreservedSelection(
      [node("a"), node("b"), node("c")],
      [node("a", true), node("b"), node("c", true)],
    );
    expect(merged.filter((n) => n.selected).map((n) => n.id)).toEqual(["a", "c"]);
  });

  it("returns the rebuilt array untouched when nothing was selected", () => {
    const rebuilt = [node("a"), node("b")];
    expect(withPreservedSelection(rebuilt, [node("a"), node("b")])).toBe(rebuilt);
  });

  it("cannot resurrect a node the backend deleted - it is simply absent from the rebuild", () => {
    const merged = withPreservedSelection([node("a")], [node("a"), node("gone", true)]);
    expect(merged.map((n) => n.id)).toEqual(["a"]);
    expect(merged.some((n) => n.selected)).toBe(false);
  });

  it("does not mutate the node objects it was handed", () => {
    const current = [node("a", true)];
    const rebuilt = [node("a")];
    withPreservedSelection(rebuilt, current);
    expect(rebuilt[0].selected).toBeUndefined();
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
