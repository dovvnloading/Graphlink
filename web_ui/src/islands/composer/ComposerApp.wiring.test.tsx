import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// A fully mocked "./bridge" module, letting these tests (a) spy on the exact
// bridge methods a click should invoke, and (b) inject arbitrary ComposerState
// shapes (e.g. an active anchor with zero items) that the real MockComposerBridge
// can't produce, since its requestAttachment()/reviewContext() are no-ops and its
// initial state always starts from bridgeTypes' fixture. vi.hoisted() is required
// here because vi.mock() factories run before this file's own top-level const/let
// bindings would otherwise be initialized.
const { bridgeMethods, stateHolder } = vi.hoisted(() => ({
  bridgeMethods: {
    ready: vi.fn(),
    updateDraft: vi.fn(),
    send: vi.fn(),
    cancel: vi.fn(),
    reviewContext: vi.fn(),
    requestAttachment: vi.fn(),
    stageTextAttachment: vi.fn(),
    removeContextItem: vi.fn(),
    selectModel: vi.fn(),
    setReasoningLevel: vi.fn(),
    openSettings: vi.fn(),
    openModelSelector: vi.fn(),
    openReasoningSelector: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  },
  stateHolder: { current: null as unknown },
}));

vi.mock("./bridge", () => ({
  createComposerBridge: (listener: (state: unknown) => void) => ({
    ...bridgeMethods,
    ready: () => {
      bridgeMethods.ready();
      listener(stateHolder.current);
    },
  }),
}));

import ComposerApp from "./ComposerApp";
import { initialComposerState } from "./bridgeTypes";
import type { ComposerAttachment, ComposerContextAnchor, ComposerState } from "./bridgeTypes";

function withContext(overrides: Partial<ComposerState["context"]>): ComposerState {
  return { ...initialComposerState, context: { ...initialComposerState.context, ...overrides } };
}

const anAttachment: ComposerAttachment = {
  id: "a1",
  name: "notes.txt",
  kind: "document",
  tokenCount: 12,
  preparationState: "ready",
  contextLabel: "Text",
};

const anAnchor: ComposerContextAnchor = { id: "n1", label: "Graph Node", type: "node" };

beforeEach(() => {
  Object.values(bridgeMethods).forEach((fn) => fn.mockClear());
  stateHolder.current = initialComposerState;
});

describe("ComposerApp against a fully mocked bridge", () => {
  it("never renders a context bar, title, or summary element - only the compact attachment control", () => {
    stateHolder.current = withContext({ anchor: anAnchor, items: [anAttachment] });
    const { container } = render(<ComposerApp />);

    expect(container.querySelector(".context-bar")).toBeNull();
    expect(container.querySelector(".context-summary")).toBeNull();
    expect(container.querySelector(".context-label")).toBeNull();
  });

  it("the attachment count badge is absent with zero items, even with an active anchor", () => {
    stateHolder.current = withContext({ anchor: anAnchor, items: [] });
    render(<ComposerApp />);

    expect(screen.queryByLabelText(/Review .* attached/)).not.toBeInTheDocument();
  });

  it("the attachment count badge appears and is driven by items.length, independent of the anchor", () => {
    stateHolder.current = withContext({ anchor: null, items: [anAttachment] });
    render(<ComposerApp />);

    expect(screen.getByLabelText("Review 1 attached item")).toBeInTheDocument();
  });

  it("clicking Attach context calls the bridge's requestAttachment()", async () => {
    const user = userEvent.setup();
    render(<ComposerApp />);

    await user.click(screen.getByLabelText("Attach context"));

    expect(bridgeMethods.requestAttachment).toHaveBeenCalledTimes(1);
  });

  it("clicking the attachment count badge calls the bridge's reviewContext(), not requestAttachment()", async () => {
    stateHolder.current = withContext({ items: [anAttachment] });
    const user = userEvent.setup();
    render(<ComposerApp />);

    await user.click(screen.getByLabelText("Review 1 attached item"));

    expect(bridgeMethods.reviewContext).toHaveBeenCalledTimes(1);
    expect(bridgeMethods.requestAttachment).not.toHaveBeenCalled();
  });

  it("a large paste calls the bridge's stageTextAttachment() with the exact pasted text, and is never inserted into the draft", async () => {
    const user = userEvent.setup();
    render(<ComposerApp />);
    const input = screen.getByLabelText("Message composer") as HTMLTextAreaElement;
    const bigText = Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n");

    await user.click(input);
    await user.paste(bigText);

    expect(input.value).toBe("");
    expect(bridgeMethods.stageTextAttachment).toHaveBeenCalledExactlyOnceWith(bigText);
    expect(bridgeMethods.updateDraft).not.toHaveBeenCalled();
  });
});
