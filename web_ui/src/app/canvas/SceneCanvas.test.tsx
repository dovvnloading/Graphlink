import { describe, expect, it, vi } from "vitest";
import { toFlowNodes } from "./SceneCanvas";
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
