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
    dismissNotification,
  };
  return { store, updateDraft, setReasoningLevel, dismissNotification };
}

describe("Composer", () => {
  it("renders the draft text and forwards edits", async () => {
    const user = userEvent.setup();
    const { store, updateDraft } = makeStore({ composer: { draft: { ...initialComposerState.draft, text: "hi" } } });
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double, not the real ComposerStore class */}
        <Composer store={store} />
      </OverlayProvider>,
    );
    const input = screen.getByLabelText("Message composer") as HTMLTextAreaElement;
    expect(input.value).toBe("hi");
    await user.type(input, "!");
    expect(updateDraft).toHaveBeenCalledWith("hi!");
  });

  it("send/attach/model controls are visibly disabled with their deferred phase named", () => {
    const { store } = makeStore();
    render(
      <OverlayProvider>
        {/* @ts-expect-error - test double */}
        <Composer store={store} />
      </OverlayProvider>,
    );
    expect(screen.getByLabelText("Send message")).toBeDisabled();
    expect(screen.getByLabelText("Attach context")).toBeDisabled();
    expect(screen.getByTitle("Model/provider selection lands in R4")).toBeDisabled();
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
