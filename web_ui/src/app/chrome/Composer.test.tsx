import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";
import { TokenCounter } from "./TokenCounter";
import { NotificationBanner } from "./NotificationBanner";
import { initialComposerState, initialNotificationState, initialTokenCounterState } from "./composerStore";
import { OverlayProvider } from "../overlays/overlays";

function makeStore(overrides: { composer?: object; tokenCounter?: object; notification?: object } = {}) {
  const listeners = new Set<() => void>();
  const state = {
    composer: { ...initialComposerState, ...overrides.composer },
    tokenCounter: { ...initialTokenCounterState, ...overrides.tokenCounter },
    notification: { ...initialNotificationState, ...overrides.notification },
  };
  const updateDraft = vi.fn();
  const setReasoningLevel = vi.fn();
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
    updateDraft,
    setReasoningLevel,
    cancelChatRequest,
    dismissNotification,
  };
  return { store, updateDraft, setReasoningLevel, cancelChatRequest, dismissNotification };
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

  it("attach/model controls stay visibly disabled with their deferred phase named; Send starts disabled on an empty draft", () => {
    const { store } = makeStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByLabelText("Send message")).toBeDisabled();
    expect(screen.getByLabelText("Attach context")).toBeDisabled();
    expect(screen.getByTitle("Model/provider selection lands in R4")).toBeDisabled();
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

  it("shows the generating indicator only when request.state is 'generating'", () => {
    const { store } = makeStore({
      composer: {
        request: { id: "req-1", state: "generating", message: "", canSend: false, canCancel: true, canRetry: false },
      },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.getByText("Generating…")).toBeInTheDocument();
  });

  it("hides the generating indicator when request.state is 'idle'", () => {
    const { store } = makeStore({
      composer: { request: { ...initialComposerState.request, state: "idle" } },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} sceneStore={makeSceneStore().sceneStore} />
      </OverlayProvider>,
    );
    expect(screen.queryByText("Generating…")).toBeNull();
  });

  it("opens the reasoning popover and selecting an option calls the intent and closes it", async () => {
    const user = userEvent.setup();
    const { store, setReasoningLevel } = makeStore({
      composer: {
        route: {
          ...initialComposerState.route,
          reasoning: {
            level: "quick",
            label: "Quick Mode (No CoT)",
            options: [
              { id: "thinking", label: "Thinking Mode (Enable CoT)", description: "Slower." },
              { id: "quick", label: "Quick Mode (No CoT)", description: "Faster." },
            ],
          },
        },
      },
    });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} />
      </OverlayProvider>,
    );
    await user.click(screen.getByText("Quick Mode (No CoT)"));
    await user.click(screen.getByText("Thinking Mode (Enable CoT)"));
    expect(setReasoningLevel).toHaveBeenCalledWith("thinking");
    expect(screen.queryByText("Thinking Mode (Enable CoT)")).toBeNull();
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
