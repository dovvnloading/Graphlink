import { describe, expect, it, vi } from "vitest";
import { handleSelectionChange, toFlowNodes } from "./SceneCanvas";
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
