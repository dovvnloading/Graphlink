import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";
import { TokenCounter } from "./TokenCounter";
import { NotificationBanner, TRANSIENT_NOTIFICATION_MS } from "./NotificationBanner";
import { initialComposerState, initialNotificationState, initialTokenCounterState } from "./composerStore";
import { initialSceneState } from "../canvas/sceneStore";
import { OverlayProvider } from "../overlays/overlays";
import { ModelCapabilityContext } from "../context/ModelCapabilityContext";
import type { ModelCapabilityPreview } from "./composerStore";
import type { StateListener } from "../../lib/ws/transport";

function makeStore(
  overrides: { composer?: object; tokenCounter?: object; notification?: object; streamText?: string } = {},
) {
  const listeners = new Set<() => void>();
  const state = {
    composer: {
      ...initialComposerState,
      route: { ...initialComposerState.route, modelId: "qwen3:8b", modelLabel: "qwen3:8b" },
      ...overrides.composer,
    },
    tokenCounter: { ...initialTokenCounterState, ...overrides.tokenCounter },
    notification: { ...initialNotificationState, ...overrides.notification },
    streamText: overrides.streamText ?? "",
  };
  const updateDraft = vi.fn();
  const setReasoningLevel = vi.fn();
  const selectModel = vi.fn();
  const selectPickerModel = vi.fn();
  const openOllamaSettings = vi.fn();
  const loadPickerModels = vi.fn().mockResolvedValue(
    ["qwen3:8b", "nemotron-3-nano:4b"].map((modelId) => ({
      provider: "Ollama", modelId, label: `Ollama · ${modelId}`,
      source: "local", capabilities: ["chat", "tools"], sizeBytes: 1024 ** 3,
    })),
  );
  const attachFile = vi.fn();
  const removeAttachment = vi.fn();
  const addAttachmentToKnowledge = vi.fn().mockResolvedValue({ jobId: "import-1" });
  const knowledgeListeners = new Set<StateListener>();
  const subscribeKnowledgeIngest = (listener: StateListener) => {
    knowledgeListeners.add(listener);
    return () => { knowledgeListeners.delete(listener); };
  };
  const emitKnowledgeJobs = (jobs: unknown[]) => {
    for (const listener of knowledgeListeners) listener({ jobs });
  };
  const cancelChatRequest = vi.fn();
  const dismissNotification = vi.fn();
  const loadComparisonModels = vi.fn().mockResolvedValue(
    ["alpha", "beta", "gamma", "delta", "epsilon"].map((modelId) => ({
      provider: "Ollama", modelId, label: `Ollama · ${modelId}`,
    })),
  );
  const sendModelComparison = vi.fn();

  const store = {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getComposer: () => state.composer,
    getTokenCounter: () => state.tokenCounter,
    getNotification: () => state.notification,
    getStreamText: () => state.streamText,
    updateDraft,
    setReasoningLevel,
    selectModel,
    selectPickerModel,
    openOllamaSettings,
    loadPickerModels,
    attachFile,
    removeAttachment,
    addAttachmentToKnowledge,
    subscribeKnowledgeIngest,
    cancelChatRequest,
    dismissNotification,
    loadComparisonModels,
    sendModelComparison,
  };
  return {
    store, updateDraft, setReasoningLevel, selectModel, selectPickerModel, openOllamaSettings, loadPickerModels, attachFile, removeAttachment,
    addAttachmentToKnowledge, emitKnowledgeJobs,
    cancelChatRequest, dismissNotification, loadComparisonModels, sendModelComparison,
  };
}

function makeSceneStore(
  overrides: {
    replyTargetNodeId?: string | null;
    synthesizeTargetNodeIds?: string[] | null;
    scene?: object;
    selectedNodeId?: string | null;
    isolationEngine?: "docker" | "podman" | null;
    knowledgePreference?: { nodeId: string | null; hasDocuments: boolean; enabled: boolean; override: boolean | null };
  } = {},
) {
  const sendMessage = vi.fn();
  const sendComposerAgentMessage = vi.fn().mockResolvedValue(true);
  const getHarnessIsolationEngine = vi.fn().mockResolvedValue(
    overrides.isolationEngine === undefined ? "docker" : overrides.isolationEngine,
  );
  const setReplyTargetNodeId = vi.fn();
  const setSelectedNodeId = vi.fn();
  const setSynthesizeTargetNodeIds = vi.fn();
  const getChatKnowledgePreference = vi.fn().mockResolvedValue(
    overrides.knowledgePreference ?? { nodeId: null, hasDocuments: false, enabled: false, override: null },
  );
  const setChatUseKnowledge = vi.fn().mockResolvedValue(undefined);
  let scene = { ...initialSceneState, ...overrides.scene };
  const sceneListeners = new Set<() => void>();
  const libraryListeners = new Set<StateListener>();
  const emitScene = (changes: object = {}) => {
    // A first real snapshot can still have revision 0, just like the placeholder.
    scene = { ...scene, ...changes };
    for (const listener of sceneListeners) listener();
  };
  const emitLibraryRevision = (revision: number) => {
    for (const listener of libraryListeners) listener({ revision });
  };
  const replyTargetNodeId = overrides.replyTargetNodeId ?? null;
  const synthesizeTargetNodeIds = overrides.synthesizeTargetNodeIds ?? null;
  const sceneStore = {
    sendMessage,
    sendComposerAgentMessage,
    getHarnessIsolationEngine,
    setReplyTargetNodeId,
    setSelectedNodeId,
    setSynthesizeTargetNodeIds,
    getChatKnowledgePreference,
    setChatUseKnowledge,
    subscribe: (listener: () => void) => {
      sceneListeners.add(listener);
      return () => { sceneListeners.delete(listener); };
    },
    subscribeKnowledgeLibrary: (listener: StateListener) => {
      libraryListeners.add(listener);
      return () => { libraryListeners.delete(listener); };
    },
    getScene: () => scene,
    getSelectedNodeId: () => overrides.selectedNodeId ?? null,
    getReplyTargetNodeId: () => replyTargetNodeId,
    getSynthesizeTargetNodeIds: () => synthesizeTargetNodeIds,
  };
  return { sceneStore, sendMessage, sendComposerAgentMessage, getHarnessIsolationEngine,
    setReplyTargetNodeId, setSynthesizeTargetNodeIds, getChatKnowledgePreference, setChatUseKnowledge,
    emitScene, emitLibraryRevision };
}

describe("Composer", () => {
  it("warns for image attachments only when the chosen model is confirmed not to support vision", () => {
    const image = { id: "img-1", name: "image.png", kind: "image", byteSize: 123, contextLabel: "Image", tokenCount: 0 };
    const route = { ...initialComposerState.route, modelId: "text-only", modelLabel: "Text only" };
    const { store } = makeStore({ composer: {
      route,
      context: { ...initialComposerState.context, items: [image] },
    } });
    const preview: ModelCapabilityPreview = {
      provider: route.provider, modelId: route.modelId,
      tools: null, vision: false, suggestedToolModel: null,
    };
    const { unmount } = render(
      <ModelCapabilityContext.Provider value={preview}>
        <OverlayProvider>
          {/* @ts-expect-error - minimal test store */}
          <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
        </OverlayProvider>
      </ModelCapabilityContext.Provider>,
    );
    expect(screen.getByText(/Text only does not support image input/)).toBeInTheDocument();
    unmount();

    for (const vision of [null, true]) {
      const result = render(
        <ModelCapabilityContext.Provider value={{ ...preview, vision }}>
          <OverlayProvider>
            {/* @ts-expect-error - minimal test store */}
            <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
          </OverlayProvider>
        </ModelCapabilityContext.Provider>,
      );
      expect(screen.queryByText(/does not support image input/)).toBeNull();
      result.unmount();
    }
  });

  it("does not warn for documents, a different model, or agent mode", () => {
    const item = { id: "file-1", name: "notes.txt", kind: "document", byteSize: 123, contextLabel: "Document", tokenCount: 2 };
    const route = { ...initialComposerState.route, modelId: "chosen" };
    const preview: ModelCapabilityPreview = {
      provider: route.provider, modelId: route.modelId, tools: false, vision: false, suggestedToolModel: null,
    };
    const documentStore = makeStore({ composer: {
      route,
      context: { ...initialComposerState.context, items: [item] },
    } });
    const docResult = render(
      <ModelCapabilityContext.Provider value={preview}>
        <OverlayProvider>
          {/* @ts-expect-error - minimal test store */}
          <Composer store={documentStore.store} sceneStore={makeSceneStore().sceneStore} />
        </OverlayProvider>
      </ModelCapabilityContext.Provider>,
    );
    expect(screen.queryByText(/does not support image input/)).toBeNull();
    docResult.unmount();

    const withImage = makeStore({ composer: {
      route,
      request: { ...initialComposerState.request, canSend: true },
      context: { ...initialComposerState.context, items: [{ ...item, kind: "image" }] },
    } });
    const otherModel = render(
      <ModelCapabilityContext.Provider value={{ ...preview, modelId: "different" }}>
        <OverlayProvider>
          {/* @ts-expect-error - minimal test store */}
          <Composer store={withImage.store} sceneStore={makeSceneStore().sceneStore} />
        </OverlayProvider>
      </ModelCapabilityContext.Provider>,
    );
    expect(screen.queryByText(/does not support image input/)).toBeNull();
    otherModel.unmount();

    render(
      <ModelCapabilityContext.Provider value={preview}>
        <OverlayProvider>
          {/* @ts-expect-error - minimal test store */}
          <Composer store={withImage.store} sceneStore={makeSceneStore().sceneStore} />
        </OverlayProvider>
      </ModelCapabilityContext.Provider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    expect(screen.queryByText(/does not support image input/)).toBeNull();
  });

  it("warns before the first Agent send and suggests a memory-fit installed model", async () => {
    const route = { ...initialComposerState.route, modelId: "translategemma:4b", modelLabel: "translategemma:4b" };
    const { store } = makeStore({ composer: {
      route,
      draft: { ...initialComposerState.draft, text: "Inspect this workspace" },
      request: { ...initialComposerState.request, canSend: true },
    } });
    const preview: ModelCapabilityPreview = {
      provider: route.provider, modelId: route.modelId, tools: false, vision: false,
      suggestedToolModel: { provider: "Ollama (Local)", modelId: "qwen3:8b" },
    };
    const { sceneStore, sendComposerAgentMessage } = makeSceneStore();
    render(
      <ModelCapabilityContext.Provider value={preview}>
        <OverlayProvider>
          {/* @ts-expect-error - minimal test store */}
          <Composer store={store} sceneStore={sceneStore} />
        </OverlayProvider>
      </ModelCapabilityContext.Provider>,
    );
    expect(screen.queryByText(/does not support tool calling/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    const warning = screen.getByText(/translategemma:4b does not support tool calling/);
    expect(warning).toHaveTextContent("qwen3:8b");
    expect(warning).toHaveTextContent("estimated to fit available system RAM");
    expect(sendComposerAgentMessage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(sendComposerAgentMessage).toHaveBeenCalledWith("Inspect this workspace", "host"));
  });

  it("warns in Agent mode without fabricating suggestions, but not for unknown or stale support", () => {
    const route = { ...initialComposerState.route, modelId: "text-only", modelLabel: "Text only" };
    const { store } = makeStore({ composer: {
      route, request: { ...initialComposerState.request, canSend: true },
    } });
    const preview: ModelCapabilityPreview = {
      provider: route.provider, modelId: route.modelId, tools: false, vision: null, suggestedToolModel: null,
    };
    const renderWith = (value: ModelCapabilityPreview | null) => render(
      <ModelCapabilityContext.Provider value={value}>
        <OverlayProvider>
          {/* @ts-expect-error - minimal test store */}
          <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
        </OverlayProvider>
      </ModelCapabilityContext.Provider>,
    );
    const first = renderWith(preview);
    fireEvent.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    expect(screen.getByText(/Text only does not support tool calling/))
      .toHaveTextContent("Choose an installed tool-capable model");
    expect(screen.queryByText(/Try installed Ollama model/)).toBeNull();
    first.unmount();

    for (const value of [null, { ...preview, tools: null }, { ...preview, tools: true },
      { ...preview, modelId: "old-route" }]) {
      const view = renderWith(value);
      fireEvent.click(screen.getByRole("button", { name: "Send to workspace agent" }));
      expect(screen.queryByText(/does not support tool calling/)).toBeNull();
      view.unmount();
    }
  });

  it("defaults Use knowledge on for a workspace with documents and can turn it off before first Send", async () => {
    const user = userEvent.setup();
    const { store } = makeStore({ composer: {
      draft: { ...initialComposerState.draft, text: "Find the runbook" },
      request: { ...initialComposerState.request, canSend: true },
    } });
    const { sceneStore, sendMessage } = makeSceneStore({ knowledgePreference: {
      nodeId: null, hasDocuments: true, enabled: true, override: null,
    } });
    render(<OverlayProvider>
      {/* @ts-expect-error - minimal test store */}
      <Composer store={store} sceneStore={sceneStore} />
    </OverlayProvider>);
    const toggle = screen.getByRole("button", { name: "Use knowledge" });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-pressed", "true"));
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(sendMessage).toHaveBeenCalledWith("Find the runbook", false);
  });

  it("persists a Use knowledge override on an existing branch", async () => {
    const user = userEvent.setup();
    const { store } = makeStore({ composer: {
      draft: { ...initialComposerState.draft, text: "Ask without retrieval" },
      request: { ...initialComposerState.request, canSend: true },
    } });
    const { sceneStore, sendMessage, setChatUseKnowledge } = makeSceneStore({ knowledgePreference: {
      nodeId: "n2", hasDocuments: true, enabled: true, override: null,
    } });
    render(<OverlayProvider>
      {/* @ts-expect-error - minimal test store */}
      <Composer store={store} sceneStore={sceneStore} />
    </OverlayProvider>);
    const toggle = screen.getByRole("button", { name: "Use knowledge" });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-pressed", "true"));
    await user.click(toggle);
    await waitFor(() => expect(setChatUseKnowledge).toHaveBeenCalledWith("n2", false));
    await waitFor(() => expect(screen.getByRole("button", { name: "Send message" })).not.toBeDisabled());
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(sendMessage).toHaveBeenCalledWith("Ask without retrieval");
  });


  it("retries a pre-connection knowledge check on the first scene snapshot even at revision zero", async () => {
    const { store } = makeStore({ composer: {
      request: { ...initialComposerState.request, canSend: true },
    } });
    const { sceneStore, getChatKnowledgePreference, emitScene } = makeSceneStore({
      knowledgePreference: { nodeId: null, hasDocuments: true, enabled: true, override: null },
    });
    getChatKnowledgePreference.mockRejectedValueOnce(new Error("not connected"));
    render(<OverlayProvider>
      {/* @ts-expect-error - minimal test store */}
      <Composer store={store} sceneStore={sceneStore} />
    </OverlayProvider>);

    const toggle = screen.getByRole("button", { name: "Use knowledge" });
    await waitFor(() => expect(toggle).toHaveAttribute("title", "Could not check workspace knowledge"));
    expect(toggle).toBeDisabled();
    act(() => emitScene({ revision: 0 }));
    await waitFor(() => expect(getChatKnowledgePreference).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(toggle).not.toBeDisabled());
    expect(toggle).toHaveAttribute("aria-pressed", "true");
  });

  it("rechecks the same empty scene as documents enter and leave the Knowledge Library", async () => {
    const { store } = makeStore({ composer: {
      request: { ...initialComposerState.request, canSend: true },
    } });
    const empty = { nodeId: null, hasDocuments: false, enabled: false, override: null };
    const populated = { nodeId: null, hasDocuments: true, enabled: true, override: null };
    const { sceneStore, getChatKnowledgePreference, emitLibraryRevision } = makeSceneStore();
    getChatKnowledgePreference
      .mockResolvedValueOnce(empty)
      .mockResolvedValueOnce(populated)
      .mockResolvedValueOnce(empty);
    render(<OverlayProvider>
      {/* @ts-expect-error - minimal test store */}
      <Composer store={store} sceneStore={sceneStore} />
    </OverlayProvider>);

    const toggle = screen.getByRole("button", { name: "Use knowledge" });
    await waitFor(() => expect(toggle).not.toBeDisabled());
    expect(toggle).toHaveAttribute("aria-pressed", "false");

    act(() => emitLibraryRevision(1)); // an import completed
    await waitFor(() => expect(toggle).toHaveAttribute("aria-pressed", "true"));
    act(() => emitLibraryRevision(2)); // the last document was removed
    await waitFor(() => expect(toggle).toHaveAttribute("aria-pressed", "false"));
    expect(getChatKnowledgePreference).toHaveBeenCalledTimes(3);
    expect(getChatKnowledgePreference).toHaveBeenCalledWith(null);
  });

  it("keeps an unsent first-root override across library notifications", async () => {
    const user = userEvent.setup();
    const { store } = makeStore({ composer: {
      draft: { ...initialComposerState.draft, text: "Ask with retrieval" },
      request: { ...initialComposerState.request, canSend: true },
    } });
    const { sceneStore, sendMessage, getChatKnowledgePreference, emitLibraryRevision } =
      makeSceneStore();
    render(<OverlayProvider>
      {/* @ts-expect-error - minimal test store */}
      <Composer store={store} sceneStore={sceneStore} />
    </OverlayProvider>);
    const toggle = screen.getByRole("button", { name: "Use knowledge" });
    await waitFor(() => expect(toggle).not.toBeDisabled());
    await user.click(toggle); // locally enabled until the first Send
    act(() => emitLibraryRevision(1));
    await waitFor(() => expect(getChatKnowledgePreference).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(toggle).not.toBeDisabled());
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(sendMessage).toHaveBeenCalledWith("Ask with retrieval", true);
  });

  it("ignores an obsolete library lookup completing after a newer revision", async () => {
    const { store } = makeStore({ composer: {
      request: { ...initialComposerState.request, canSend: true },
    } });
    const empty = { nodeId: null, hasDocuments: false, enabled: false, override: null };
    const populated = { nodeId: null, hasDocuments: true, enabled: true, override: null };
    const { sceneStore, getChatKnowledgePreference, emitLibraryRevision } = makeSceneStore();
    let finishOld: (preference: typeof empty) => void = () => {};
    getChatKnowledgePreference.mockResolvedValueOnce(empty)
      .mockImplementationOnce(() => new Promise((resolve) => { finishOld = resolve; }))
      .mockResolvedValueOnce(populated);
    render(<OverlayProvider>
      {/* @ts-expect-error - minimal test store */}
      <Composer store={store} sceneStore={sceneStore} />
    </OverlayProvider>);
    await waitFor(() => expect(getChatKnowledgePreference).toHaveBeenCalledTimes(1));
    act(() => emitLibraryRevision(1));
    await waitFor(() => expect(getChatKnowledgePreference).toHaveBeenCalledTimes(2));
    act(() => emitLibraryRevision(2));
    const toggle = screen.getByRole("button", { name: "Use knowledge" });
    await waitFor(() => expect(toggle).toHaveAttribute("aria-pressed", "true"));
    act(() => finishOld(empty));
    await waitFor(() => expect(getChatKnowledgePreference).toHaveBeenCalledTimes(3));
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(toggle).not.toBeDisabled();
  });


  it("agent toggle routes to the selected agent with the composer model instead of ordinary chat", async () => {
    const user = userEvent.setup();
    const { store, updateDraft } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "Review these changes" },
        route: { ...initialComposerState.route, modelId: "qwen3:8b", available: true },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendMessage, sendComposerAgentMessage } = makeSceneStore({
      selectedNodeId: "agent-1",
      scene: { nodes: [{ id: "agent-1", kind: "harness", title: "Code agent" }] },
    });
    let finishSend: (started: boolean) => void = () => {};
    sendComposerAgentMessage.mockReturnValue(new Promise<boolean>((resolve) => {
      finishSend = resolve;
    }));
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByRole("button", { name: "Send to workspace agent" })).toHaveAttribute("aria-pressed", "false");
    await user.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    expect(screen.getByRole("button", { name: "Send to workspace agent" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Agent destination")).toHaveTextContent("Selected: Code agent");
    expect(screen.getByPlaceholderText("Give the workspace agent a task…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compare models" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(sendComposerAgentMessage).toHaveBeenCalledWith("Review these changes", "host");
    expect(sendMessage).not.toHaveBeenCalled();
    expect(updateDraft).not.toHaveBeenCalledWith("");
    finishSend(true);
    await waitFor(() => expect(updateDraft).toHaveBeenCalledWith(""));
  });

  it("agent mode creates a new agent when the selected node is not an agent", async () => {
    const user = userEvent.setup();
    const { store } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "Inventory the workspace" },
        route: { ...initialComposerState.route, modelId: "local-chat", available: true },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendComposerAgentMessage } = makeSceneStore({
      selectedNodeId: "note-1",
      scene: { nodes: [{ id: "note-1", kind: "note", title: "Note" }] },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    expect(screen.getByLabelText("Agent destination")).toHaveTextContent("New agent · scratch workspace");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(sendComposerAgentMessage).toHaveBeenCalledWith("Inventory the workspace", "host");
  });

  it("can choose Isolated for a new agent's first composer task", async () => {
    const user = userEvent.setup();
    const { store } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "Run tests in isolation" },
        route: { ...initialComposerState.route, modelId: "local-chat", available: true },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendComposerAgentMessage } = makeSceneStore({ isolationEngine: "podman" });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    expect(await screen.findByText(/Podman detected on the backend/)).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "Execution profile for new agent" }), "isolated");
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(sendComposerAgentMessage).toHaveBeenCalledWith("Run tests in isolation", "isolated");
  });

  it("disables new-agent isolation when no container engine is detected", async () => {
    const user = userEvent.setup();
    const { store } = makeStore({
      composer: { request: { ...initialComposerState.request, canSend: true } },
    });
    const { sceneStore } = makeSceneStore({ isolationEngine: null });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    expect(await screen.findByText(/Docker or Podman is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Isolated/ })).toBeDisabled();
  });

  it("rejects oversized Agent tasks visibly without sending or clearing the draft", async () => {
    const user = userEvent.setup();
    const tooLong = "x".repeat(20_000) + "FINAL_INSTRUCTION_MUST_APPEAR";
    const { store, updateDraft } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: tooLong },
        route: { ...initialComposerState.route, modelId: "local-chat", available: true },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendComposerAgentMessage } = makeSceneStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(screen.getByRole("alert")).toHaveTextContent("20,000 characters");
    expect(screen.getByRole("textbox", { name: "Message composer" })).toHaveValue(tooLong);
    expect(sendComposerAgentMessage).not.toHaveBeenCalled();
    expect(updateDraft).not.toHaveBeenCalledWith("");
  });

  // Regression #768: JS .length would reject this valid 10,001-code-point task.
  it("sends supplementary Unicode Agent tasks through the main composer", async () => {
    const user = userEvent.setup();
    const text = "😀".repeat(10_001);
    const { store } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text },
        route: { ...initialComposerState.route, modelId: "local-chat", available: true },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendComposerAgentMessage } = makeSceneStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );

    await user.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(sendComposerAgentMessage).toHaveBeenCalledWith(text, "host");
  });

  it("keeps the composer text after a rejected agent dispatch and allows retry", async () => {
    const user = userEvent.setup();
    const { store, updateDraft } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "Don't lose this" },
        route: { ...initialComposerState.route, modelId: "local-chat", available: true },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendComposerAgentMessage } = makeSceneStore();
    sendComposerAgentMessage.mockResolvedValue(false);
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not start");
    expect(updateDraft).not.toHaveBeenCalledWith("");
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
  });

  it("does not send an agent task without an available composer model", async () => {
    const user = userEvent.setup();
    const { store } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "Task" },
        route: { ...initialComposerState.route, modelId: "", available: false },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendComposerAgentMessage } = makeSceneStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Send to workspace agent" }));
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    expect(sendComposerAgentMessage).not.toHaveBeenCalled();
  });


  it("comparison mode requires two models and sends provider-qualified siblings instead of a normal send", async () => {
    const user = userEvent.setup();
    const { store, sendModelComparison, updateDraft } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "compare this" },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendMessage } = makeSceneStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );

    await user.click(screen.getByRole("button", { name: /Compare/ }));
    expect(await screen.findByRole("checkbox", { name: "Ollama · alpha" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Ollama · alpha" }));
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Ollama · beta" }));
    await user.click(screen.getByRole("button", { name: "Done" }));
    await user.click(screen.getByRole("button", { name: "Send message" }));

    expect(sendMessage).not.toHaveBeenCalled();
    expect(sendModelComparison).toHaveBeenCalledWith(
      "compare this",
      [{ provider: "Ollama", modelId: "alpha" }, { provider: "Ollama", modelId: "beta" }],
      null,
      expect.any(Function),
    );
    expect(updateDraft).not.toHaveBeenCalled();
    const onSettled = sendModelComparison.mock.calls[0][3];
    onSettled(false);
    expect(updateDraft).not.toHaveBeenCalled();
    onSettled(true);
    expect(updateDraft).toHaveBeenCalledWith("");
  });

  it("limits comparison selection to four and can switch back to ordinary send", async () => {
    const user = userEvent.setup();
    const { store } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "one model" },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendMessage } = makeSceneStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - minimal test store */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    await user.click(screen.getByRole("button", { name: /Compare/ }));
    for (const name of ["alpha", "beta", "gamma", "delta"]) {
      await user.click(await screen.findByRole("checkbox", { name: new RegExp(`Ollama · ${name}`) }));
    }
    expect(screen.getByRole("checkbox", { name: /Ollama · epsilon/ })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /Ollama · delta/ }));
    expect(screen.getByRole("checkbox", { name: /Ollama · epsilon/ })).not.toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Use single-model send" }));
    await user.click(screen.getByRole("button", { name: "Send message" }));
    expect(sendMessage).toHaveBeenCalledWith("one model");
  });

  it("renders the draft text and forwards edits", async () => {
    const user = userEvent.setup();
    const { store, updateDraft } = makeStore({ composer: { draft: { ...initialComposerState.draft, text: "hi" } } });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double, not the real ComposerStore class */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    const input = screen.getByLabelText("Message composer") as HTMLTextAreaElement;
    expect(input.value).toBe("hi");
    await user.type(input, "!");
    expect(updateDraft).toHaveBeenCalledWith("hi!");
  });

  it("opens the model picker even when no model is configured", async () => {
    const user = userEvent.setup();
    const { store, loadPickerModels, openOllamaSettings } = makeStore({
      composer: {
        request: { ...initialComposerState.request, canSend: true },
        route: { ...initialComposerState.route, modelId: "", modelOptions: [] },
        capabilities: { ...initialComposerState.capabilities, modelSelection: false },
      },
    });
    loadPickerModels.mockResolvedValue([]);
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error Test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByLabelText("Send message")).toBeDisabled();
    const trigger = container.querySelector('[data-overlay-trigger="model"]') as HTMLButtonElement;
    expect(trigger).toBeEnabled();
    await user.click(trigger);
    expect(await screen.findByRole("menuitem", { name: "Manage models…" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Set up a local model" }));
    expect(openOllamaSettings).toHaveBeenCalledOnce();
  });

  it("the model control enables when the backend reports real options, and picking one calls selectModel", async () => {
    const user = userEvent.setup();
    const { store, selectPickerModel } = makeStore({
      composer: {
        route: {
          ...initialComposerState.route,
          provider: "Ollama (Local)",
          modelId: "qwen3:8b",
          modelLabel: "qwen3:8b",
          modelOptions: [
            { id: "qwen3:8b", label: "qwen3:8b" },
            { id: "nemotron-3-nano:4b", label: "nemotron-3-nano:4b" },
          ],
        },
        capabilities: { ...initialComposerState.capabilities, modelSelection: true },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );

    const trigger = container.querySelector('[data-overlay-trigger="model"]') as HTMLButtonElement;
    expect(trigger).not.toBeDisabled();

    await user.click(trigger);
    await user.click(await screen.findByRole("menuitemradio", { name: /Ollama · nemotron-3-nano:4b/ }));

    expect(selectPickerModel).toHaveBeenCalledWith("Ollama", "nemotron-3-nano:4b");
    // and the popover closes on choose, like the reasoning picker
    expect(screen.queryByText("nemotron-3-nano:4b")).toBeNull();
  });

  it("filters a large gateway catalog and clears the search when reopened", async () => {
    const user = userEvent.setup();
    const modelOptions = Array.from({ length: 12 }, (_, i) => ({ id: `lab/model-${i}`, label: `Model ${i}` }));
    const { store, selectPickerModel, loadPickerModels } = makeStore({
      composer: {
        route: { ...initialComposerState.route, provider: "OpenRouter", modelOptions },
        capabilities: { ...initialComposerState.capabilities, modelSelection: true },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    loadPickerModels.mockResolvedValue(modelOptions.map(({ id, label }) => ({
      provider: "OpenRouter", modelId: id, label,
      source: "cloud", capabilities: [], sizeBytes: null,
    })));
    render(
      <OverlayProvider>
        {/* @ts-expect-error Test doubles implement only the composer surface. */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    const trigger = screen.getByRole("button", { name: /OpenRouter/ });
    await user.click(trigger);
    const search = await screen.findByRole("searchbox", { name: "Search models" });
    expect(search).toHaveFocus();
    await user.type(search, "LAB/MODEL-11");
    expect(screen.queryByRole("menuitemradio", { name: "Model 0" })).toBeNull();
    // Enter while focus is still in the search input must choose the lone
    // match; previously this worked only after tabbing onto the result.
    await user.keyboard("{Enter}");
    expect(selectPickerModel).toHaveBeenCalledWith("OpenRouter", "lab/model-11");
    await user.click(trigger);
    expect(screen.getByRole("menuitemradio", { name: "Model 0" })).toBeVisible();
  });

  it("Send is enabled once there's text, calls sceneStore.sendMessage, and clears the draft", async () => {
    const user = userEvent.setup();
    const { store, updateDraft } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "hi" },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendMessage } = makeSceneStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    const sendButton = screen.getByLabelText("Send message");
    expect(sendButton).not.toBeDisabled();
    await user.click(sendButton);
    expect(sendMessage).toHaveBeenCalledWith("hi");
    expect(updateDraft).toHaveBeenCalledWith("");
  });

  it("Enter sends (and clears the draft); Shift+Enter does not", async () => {
    const user = userEvent.setup();
    const { store, updateDraft } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "hi" },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendMessage } = makeSceneStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    const input = screen.getByLabelText("Message composer");
    await user.type(input, "{Shift>}{Enter}{/Shift}");
    expect(sendMessage).not.toHaveBeenCalled();
    await user.type(input, "{Enter}");
    expect(sendMessage).toHaveBeenCalledWith("hi");
    expect(updateDraft).toHaveBeenCalledWith("");
  });

  it("Enter fired while an IME is still composing does not send", () => {
    const { store } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "半角" },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { sceneStore, sendMessage } = makeSceneStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    const input = screen.getByLabelText("Message composer");

    fireEvent.keyDown(input, { key: "Enter", isComposing: true });

    expect(sendMessage).not.toHaveBeenCalled();
  });

  it("whitespace-only text does not enable Send", () => {
    const { store } = makeStore({ composer: { draft: { ...initialComposerState.draft, text: "   " } } });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByLabelText("Send message")).toBeDisabled();
  });

  it("Send stays disabled when request.canSend is false even with non-empty draft text", () => {
    const { store } = makeStore({
      composer: {
        draft: { ...initialComposerState.draft, text: "hi" },
        request: { ...initialComposerState.request, canSend: false },
      },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByLabelText("Send message")).toBeDisabled();
  });

  it("Cancel control is absent when request.canCancel is false", () => {
    const { store } = makeStore({
      composer: { request: { ...initialComposerState.request, canCancel: false } },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.queryByLabelText("Stop generating")).toBeNull();
  });

  it("Cancel control is present when request.canCancel is true and calls cancelChatRequest with the request id", async () => {
    const user = userEvent.setup();
    const { store, cancelChatRequest } = makeStore({
      composer: {
        request: { id: "req-42", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false },
      },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    const cancelButton = screen.getByLabelText("Stop generating");
    expect(cancelButton).toBeInTheDocument();
    await user.click(cancelButton);
    expect(cancelChatRequest).toHaveBeenCalledWith("req-42");
  });

  it("shows 'Thinking…' while generating and streamText is still empty", () => {
    const { store } = makeStore({
      composer: {
        request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false },
      },
      streamText: "",
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByText("Thinking…")).toBeInTheDocument();
  });

  it("shows the raw growing stream text once streamText is non-empty", () => {
    const { store } = makeStore({
      composer: {
        request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false },
      },
      streamText: "Hello, wor",
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByText("Hello, wor")).toBeInTheDocument();
    expect(screen.queryByText("Thinking…")).toBeNull();
  });

  it("hides the stream preview when request.state is 'idle'", () => {
    const { store } = makeStore({
      composer: { request: { ...initialComposerState.request, state: "idle" } },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.queryByText("Thinking…")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("opens the reasoning popover and selecting an option calls the intent and closes it", async () => {
    const user = userEvent.setup();
    const { store, setReasoningLevel } = makeStore({
      composer: {
        route: {
          ...initialComposerState.route,
          reasoning: {
            level: "off",
            label: "Off",
            options: [
              { id: "off", label: "Off", description: "No extended reasoning - the fastest, most direct answers." },
              { id: "low", label: "Low", description: "A little reasoning before answering." },
              { id: "medium", label: "Medium", description: "A balanced amount of reasoning." },
              { id: "high", label: "High", description: "Thorough reasoning - slower, higher-quality answers." },
            ],
          },
        },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    await user.click(screen.getByText("Off"));
    await user.click(screen.getByText("High"));
    expect(setReasoningLevel).toHaveBeenCalledWith("high");
    expect(screen.getByRole("button", { name: "Reasoning: High" })).toBeInTheDocument();
  });

  it("hides the Reasoning chip when the provider and model expose no reasoning selection", () => {
    // An always-disabled chip was clutter; the model picker still names the
    // provider, so nothing is lost by leaving the control out.
    const { store } = makeStore({
      composer: {
        capabilities: { ...initialComposerState.capabilities, reasoningSelection: false },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(container.querySelector('[data-overlay-trigger="reasoning"]')).toBeNull();
  });

  it("Reasoning trigger is disabled while a request is busy even when reasoningSelection is true", () => {
    const { store } = makeStore();
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(container.querySelector('[data-overlay-trigger="reasoning"]')).toBeDisabled();
  });

  it("Reasoning trigger is enabled when reasoningSelection is true and canSend is true", () => {
    const { store } = makeStore({
      composer: { request: { ...initialComposerState.request, canSend: true } },
    });
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(container.querySelector('[data-overlay-trigger="reasoning"]')).not.toBeDisabled();
  });

  it("Reasoning uses a kit MenuButton with a disabled explanation and no title attribute", () => {
    const { store } = makeStore();
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    const trigger = container.querySelector('[data-overlay-trigger="reasoning"]') as HTMLButtonElement;
    expect(trigger).toBeDisabled();
    expect(trigger).not.toHaveAttribute("title");
    fireEvent.focus(trigger.parentElement!);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Wait for the current response to finish");
  });

  it("enabled Reasoning names its level for assistive tech without a title attribute", () => {
    const { store } = makeStore({
      composer: { request: { ...initialComposerState.request, canSend: true } },
    });
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    const trigger = container.querySelector('[data-overlay-trigger="reasoning"]');
    expect(trigger).not.toHaveAttribute("title");
    expect(trigger).toHaveAttribute("aria-label", `Reasoning: ${store.getComposer().route.reasoning.label}`);
  });

  it("the Attach button is disabled when capabilities.attachments is false, even with canSend true", () => {
    const { store } = makeStore({
      composer: {
        capabilities: { ...initialComposerState.capabilities, attachments: false },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByLabelText("Attach context")).toBeDisabled();
  });

  it("clicking the Attach button calls store.attachFile() when capable and idle", async () => {
    const user = userEvent.setup();
    const { store, attachFile } = makeStore({
      composer: {
        capabilities: { ...initialComposerState.capabilities, attachments: true },
        request: { ...initialComposerState.request, canSend: true },
      },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    const attachButton = screen.getByLabelText("Attach context");
    expect(attachButton).not.toBeDisabled();
    await user.click(attachButton);
    expect(attachFile).toHaveBeenCalledTimes(1);
  });

  it("renders no attachment chips when context.items is empty", () => {
    const { store } = makeStore();
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(container.querySelector(".composer-attachment-chips")).toBeNull();
  });

  it("renders a real chip per staged attachment, and removing one calls store.removeAttachment(id)", async () => {
    const user = userEvent.setup();
    const { store, removeAttachment } = makeStore({
      composer: {
        context: {
          ...initialComposerState.context,
          items: [
            { id: "att-1", name: "photo.png", kind: "image", byteSize: 2048, contextLabel: "Vision", tokenCount: 0 },
            { id: "att-2", name: "notes.txt", kind: "document", byteSize: 512, contextLabel: "Text", tokenCount: 12 },
          ],
        },
      },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );

    expect(screen.getByText("photo.png")).toBeInTheDocument();
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    expect(screen.getByText("Vision")).toBeInTheDocument();
    expect(screen.getByText("Text")).toBeInTheDocument();

    await user.click(screen.getByLabelText("Remove photo.png"));
    expect(removeAttachment).toHaveBeenCalledWith("att-1");
    // the OTHER chip must not have been touched
    expect(removeAttachment).not.toHaveBeenCalledWith("att-2");
  });

  it("adds only document attachments to knowledge and reports job progress and success", async () => {
    const user = userEvent.setup();
    const { store, addAttachmentToKnowledge, emitKnowledgeJobs } = makeStore({
      composer: {
        context: {
          ...initialComposerState.context,
          items: [
            { id: "image", name: "image.png", kind: "image", byteSize: 100, contextLabel: "Vision", tokenCount: 0 },
            { id: "doc", name: "research.md", kind: "document", byteSize: 100, contextLabel: "Text", tokenCount: 10 },
          ],
        },
      },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );

    expect(screen.queryByRole("button", { name: "Add image.png to knowledge" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add research.md to knowledge" }));
    expect(addAttachmentToKnowledge).toHaveBeenCalledWith("doc");

    act(() => emitKnowledgeJobs([{
      jobId: "import-1", status: "running", filesTotal: 1, filesProcessed: 0,
      added: 0, updated: 0, duplicates: 0, unchanged: 0, skipped: 0,
      failedFiles: 0, error: "", warnings: [],
    }]));
    expect(await screen.findByLabelText("Knowledge import progress")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add research.md to knowledge" })).toBeDisabled();

    act(() => emitKnowledgeJobs([{
      jobId: "import-1", status: "completed", filesTotal: 1, filesProcessed: 1,
      added: 1, updated: 0, duplicates: 0, unchanged: 0, skipped: 0,
      failedFiles: 0, error: "", warnings: [],
    }]));
    expect(await screen.findByText("Added research.md to this workspace's knowledge.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add research.md to knowledge" })).toBeEnabled();
  });

  it("surfaces a failed attachment import instead of claiming it succeeded", async () => {
    const user = userEvent.setup();
    const { store, addAttachmentToKnowledge } = makeStore({
      composer: {
        context: {
          ...initialComposerState.context,
          items: [{ id: "doc", name: "notes.txt", kind: "document",
            byteSize: 16, contextLabel: "Text", tokenCount: 3 }],
        },
      },
    });
    addAttachmentToKnowledge.mockRejectedValueOnce(new Error("Import is already running"));
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Add notes.txt to knowledge" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not add notes.txt to knowledge: Import is already running",
    );
  });
});

describe("TokenCounter", () => {
  it("shows only the compact total until hovered", () => {
    const { store } = makeStore({
      tokenCounter: { inputTokens: 3, outputTokens: 2, contextTokens: 1, totalTokens: 6 },
    });
    // @ts-expect-error - test double
    render(<TokenCounter store={store} />);

    expect(screen.getByRole("button", { name: "Token usage: 6 total" })).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
    // Input/Output/Context only exist inside the hover popout - not shown yet.
    expect(screen.queryByText("3")).toBeNull();
    expect(screen.queryByRole("status", { name: "Token usage breakdown" })).toBeNull();
  });

  it("reveals all four counts in a popout on hover, and hides them again on unhover", async () => {
    const user = userEvent.setup();
    const { store } = makeStore({
      tokenCounter: { inputTokens: 3, outputTokens: 2, contextTokens: 1, totalTokens: 6 },
    });
    // @ts-expect-error - test double
    render(<TokenCounter store={store} />);

    await user.hover(screen.getByRole("button", { name: "Token usage: 6 total" }));
    const popout = screen.getByRole("status", { name: "Token usage breakdown" });
    expect(popout).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();

    await user.unhover(screen.getByRole("button", { name: "Token usage: 6 total" }));
    expect(screen.queryByRole("status", { name: "Token usage breakdown" })).toBeNull();
  });
});

// ADR-002 Workstream 1: the "Branch from here" reply-target indicator.
describe("Composer reply target (ADR-002 Workstream 1)", () => {
  it("renders nothing when no reply target is pending", () => {
    const { store } = makeStore();
    const { sceneStore } = makeSceneStore({ replyTargetNodeId: null });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.queryByLabelText("Replying to")).toBeNull();
  });

  it("shows a preview of the target node's content when a reply target is pending", () => {
    const { store } = makeStore();
    const { sceneStore } = makeSceneStore({
      replyTargetNodeId: "n-root",
      scene: { nodes: [{ id: "n-root", content: "the original root message" }] },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    const indicator = screen.getByLabelText("Replying to");
    expect(indicator).toBeInTheDocument();
    expect(screen.getByText("the original root message")).toBeInTheDocument();
  });

  it("does not render an indicator for a stale target whose node no longer exists", () => {
    const { store } = makeStore();
    const { sceneStore } = makeSceneStore({ replyTargetNodeId: "deleted-node", scene: { nodes: [] } });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.queryByLabelText("Replying to")).toBeNull();
  });

  it("the clear button calls sceneStore.setReplyTargetNodeId(null)", async () => {
    const user = userEvent.setup();
    const { store } = makeStore();
    const { sceneStore, setReplyTargetNodeId } = makeSceneStore({
      replyTargetNodeId: "n-root",
      scene: { nodes: [{ id: "n-root", content: "root" }] },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    await user.click(screen.getByLabelText("Clear reply target"));
    expect(setReplyTargetNodeId).toHaveBeenCalledWith(null);
  });
});

describe("Composer synthesize target (ADR-002 Workstream 1)", () => {
  it("renders nothing when no synthesize target is pending", () => {
    const { store } = makeStore();
    const { sceneStore } = makeSceneStore({ synthesizeTargetNodeIds: null });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.queryByLabelText("Synthesizing branches")).toBeNull();
  });

  it("shows a count when a synthesize target is pending", () => {
    const { store } = makeStore();
    const { sceneStore } = makeSceneStore({ synthesizeTargetNodeIds: ["n1", "n2", "n3"] });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    const indicator = screen.getByLabelText("Synthesizing branches");
    expect(indicator).toBeInTheDocument();
    expect(screen.getByText("Synthesizing 3 branches")).toBeInTheDocument();
  });

  it("changes the composer placeholder to steer the user toward instructions, only while synthesizing", () => {
    const { store } = makeStore();
    const { sceneStore } = makeSceneStore({ synthesizeTargetNodeIds: ["n1", "n2"] });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByPlaceholderText("Describe how to combine these branches…")).toBeInTheDocument();
  });

  it("the clear button calls sceneStore.setSynthesizeTargetNodeIds(null)", async () => {
    const user = userEvent.setup();
    const { store } = makeStore();
    const { sceneStore, setSynthesizeTargetNodeIds } = makeSceneStore({
      synthesizeTargetNodeIds: ["n1", "n2"],
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={sceneStore} />
      </OverlayProvider>,
    );
    await user.click(screen.getByLabelText("Cancel synthesizing these branches"));
    expect(setSynthesizeTargetNodeIds).toHaveBeenCalledWith(null);
  });
});

describe("NotificationBanner", () => {
  it("renders nothing when not visible", () => {
    const { store } = makeStore();
    // @ts-expect-error - test double
    const { container } = render(<NotificationBanner store={store} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the message and dismiss calls the intent", async () => {
    const user = userEvent.setup();
    const { store, dismissNotification } = makeStore({
      notification: { visible: true, message: "Saved.", msgType: "success" },
    });
    // @ts-expect-error - test double
    render(<NotificationBanner store={store} />);
    expect(screen.getByText("Saved.")).toBeInTheDocument();
    await user.click(screen.getByLabelText("Dismiss notification"));
    expect(dismissNotification).toHaveBeenCalled();
  });

  it("clears a success confirmation by itself, through the dismiss intent", () => {
    vi.useFakeTimers();
    try {
      const { store, dismissNotification } = makeStore({
        notification: { visible: true, message: "Saved.", msgType: "success" },
      });
      // @ts-expect-error - test double
      render(<NotificationBanner store={store} />);
      vi.advanceTimersByTime(TRANSIENT_NOTIFICATION_MS - 1);
      expect(dismissNotification).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(dismissNotification).toHaveBeenCalledTimes(1);
    } finally { vi.useRealTimers(); }
  });

  it("keeps a warning until the user dismisses it", () => {
    vi.useFakeTimers();
    try {
      const { store, dismissNotification } = makeStore({
        notification: { visible: true, message: "No AI provider is configured yet.", msgType: "warning" },
      });
      // @ts-expect-error - test double
      render(<NotificationBanner store={store} />);
      vi.advanceTimersByTime(TRANSIENT_NOTIFICATION_MS * 4);
      expect(dismissNotification).not.toHaveBeenCalled();
    } finally { vi.useRealTimers(); }
  });
});
