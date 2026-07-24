import { describe, expect, it, vi } from "vitest";
import { SceneStore, initialSceneState, scaleDragPosition } from "./sceneStore";
import type { WsTransport } from "../../lib/ws/transport";

type StateListener = (payload: Record<string, unknown>) => void;

function makeFakeTransport() {
  const listeners = new Map<string, StateListener>();
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
    request: requestImpl,
  } as unknown as WsTransport;
  return { transport, listeners, intents, requests, requestImpl, requestResults };
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
        codeSandboxPrompt: "",
        codeSandboxCode: "",
        codeSandboxOutput: "",
        codeSandboxAnalysis: "",
        codeSandboxAwaitingApproval: false,
        codeSandboxError: "",
      },
    ],
    edges: [],
    pins: [],
    snapToGrid: true,
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
