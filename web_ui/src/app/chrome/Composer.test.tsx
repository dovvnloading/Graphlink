import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";
import { TokenCounter } from "./TokenCounter";
import { NotificationBanner } from "./NotificationBanner";
import { initialComposerState, initialNotificationState, initialTokenCounterState } from "./composerStore";
import { OverlayProvider } from "../overlays/overlays";

function makeStore(
  overrides: { composer?: object; tokenCounter?: object; notification?: object; streamText?: string } = {},
) {
  const listeners = new Set<() => void>();
  const state = {
    composer: { ...initialComposerState, ...overrides.composer },
    tokenCounter: { ...initialTokenCounterState, ...overrides.tokenCounter },
    notification: { ...initialNotificationState, ...overrides.notification },
    streamText: overrides.streamText ?? "",
  };
  const updateDraft = vi.fn();
  const setReasoningLevel = vi.fn();
  const selectModel = vi.fn();
  const attachFile = vi.fn();
  const removeAttachment = vi.fn();
  const cancelChatRequest = vi.fn();
  const dismissNotification = vi.fn();
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
    attachFile,
    removeAttachment,
    cancelChatRequest,
    dismissNotification,
  };
  return {
    store, updateDraft, setReasoningLevel, selectModel, attachFile, removeAttachment,
    cancelChatRequest, dismissNotification,
  };
}

function makeSceneStore() {
  const sendMessage = vi.fn();
  return { sceneStore: { sendMessage }, sendMessage };
}

describe("Composer", () => {
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

  it("Send starts disabled on an empty draft; the model control is disabled ONLY when there is nothing to choose", () => {
    // R8a: this used to assert the model control "stays visibly disabled with
    // its deferred phase named" - i.e. it encoded a dead stub as the expected
    // behaviour. The control is real now, so the assertion is inverted: it is
    // disabled when the backend reports no models, and enabled when it does.
    const { store } = makeStore();
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByLabelText("Send message")).toBeDisabled();
    // modelSelection defaults false with an empty modelOptions list.
    expect(container.querySelector('[data-overlay-trigger="model"]')).toBeDisabled();
  });

  it("the model control enables when the backend reports real options, and picking one calls selectModel", async () => {
    const user = userEvent.setup();
    const { store, selectModel } = makeStore({
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
    await user.click(screen.getByText("nemotron-3-nano:4b"));

    expect(selectModel).toHaveBeenCalledWith("nemotron-3-nano:4b");
    // and the popover closes on choose, like the reasoning picker
    expect(screen.queryByText("nemotron-3-nano:4b")).toBeNull();
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
    expect(screen.queryByLabelText("Cancel response")).toBeNull();
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
    const cancelButton = screen.getByLabelText("Cancel response");
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
        <Composer store={store} />
      </OverlayProvider>,
    );
    await user.click(screen.getByText("Off"));
    await user.click(screen.getByText("High"));
    expect(setReasoningLevel).toHaveBeenCalledWith("high");
    expect(screen.queryByText("High")).toBeNull();
  });

  it("Reasoning trigger is disabled when reasoningSelection is false", () => {
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
    expect(container.querySelector('[data-overlay-trigger="reasoning"]')).toBeDisabled();
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

  it("R8a finding #11: Reasoning trigger explains itself via title when disabled for provider reasons", () => {
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
    expect(container.querySelector('[data-overlay-trigger="reasoning"]')).toHaveAttribute(
      "title",
      "Reasoning mode is only available for local Ollama and Llama.cpp providers",
    );
  });

  it("R8a finding #11: Reasoning trigger explains itself via title when disabled because a request is busy", () => {
    const { store } = makeStore();
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(container.querySelector('[data-overlay-trigger="reasoning"]')).toHaveAttribute(
      "title",
      "Wait for the current response to finish",
    );
  });

  it("R8a finding #11: Reasoning trigger has no title at all when it is enabled - nothing to explain", () => {
    const { store } = makeStore({
      composer: { request: { ...initialComposerState.request, canSend: true } },
    });
    const { container } = render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(container.querySelector('[data-overlay-trigger="reasoning"]')).not.toHaveAttribute("title");
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
});

describe("TokenCounter", () => {
  it("renders all four counts", () => {
    const { store } = makeStore({
      tokenCounter: { inputTokens: 3, outputTokens: 2, contextTokens: 1, totalTokens: 6 },
    });
    // @ts-expect-error - test double
    render(<TokenCounter store={store} />);
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
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
});
