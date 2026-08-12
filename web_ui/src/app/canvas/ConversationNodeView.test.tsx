import { ReactFlowProvider, useStoreApi, type NodeProps } from "@xyflow/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ADR-011 stage 11.1: wraps the real useLodVisibility so every ACTUAL
// invocation (mount or re-render) is countable - a React.memo bailout skips
// calling ConversationNodeView's function body entirely, so this hook
// (called unconditionally on every real render) never fires during a bailed
// re-render. Same "mock-wrap-and-delegate" technique ImageNodeView.test.tsx
// established.
const lodVisibilityCalls = { count: 0 };

vi.mock("./useLodVisibility", async (importOriginal) => {
  const original = await importOriginal<typeof import("./useLodVisibility")>();
  return {
    ...original,
    useLodVisibility: (...args: Parameters<typeof original.useLodVisibility>) => {
      lodVisibilityCalls.count += 1;
      return original.useLodVisibility(...args);
    },
  };
});

import { ConversationNodeView, type ConversationFlowNode } from "./ConversationNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderConversationNode(overrides: Partial<ConversationFlowNode["data"]> = {}) {
  const onToggleCollapse = vi.fn();
  const onDelete = vi.fn();
  const onSend = vi.fn();
  const onDeleteMessage = vi.fn();
  const onCancel = vi.fn();
  const onOpenDocumentView = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      history: [
        { role: "user" as const, content: "Hello **world**", incomplete: false },
        { role: "assistant" as const, content: "Hi there", incomplete: false },
      ],
      isCollapsed: false,
      pendingRequestId: null,
      onToggleCollapse,
      onDelete,
      onSend,
      onDeleteMessage,
      onCancel,
      onOpenDocumentView,
      // ADR-006 stage 6.4
      subscribeStream: vi.fn().mockReturnValue(vi.fn()),
      ...overrides,
    },
  } as unknown as NodeProps<ConversationFlowNode>;

  render(
    <ReactFlowProvider>
      <ConversationNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onToggleCollapse, onDelete, onSend, onDeleteMessage, onCancel, onOpenDocumentView };
}

// Directly sets the React Flow internal Zustand store's transform/zoom
// value - useReactFlow()'s own setViewport requires a mounted panZoom
// instance (a real <ReactFlow> viewport element), which doesn't exist in
// this direct-render test setup (see the comment above renderConversationNode
// / ChatNodeView.test.tsx). Writing directly to the store via useStoreApi()
// is the same store useStore(s => s.transform[2]) reads from, so this is a
// faithful way to drive the LOD threshold in a test.
function ZoomSetter({ zoom }: { zoom: number }) {
  const store = useStoreApi();
  useEffect(() => {
    store.setState({ transform: [0, 0, zoom] });
  }, [zoom, store]);
  return null;
}

function renderConversationNodeAtZoom(zoom: number, overrides: Partial<ConversationFlowNode["data"]> = {}) {
  const onToggleCollapse = vi.fn();
  const onDelete = vi.fn();
  const onSend = vi.fn();
  const onDeleteMessage = vi.fn();
  const onCancel = vi.fn();
  const onOpenDocumentView = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      history: [{ role: "user" as const, content: "Hello", incomplete: false }],
      isCollapsed: false,
      pendingRequestId: null,
      onToggleCollapse,
      onDelete,
      onSend,
      onDeleteMessage,
      onCancel,
      onOpenDocumentView,
      // ADR-006 stage 6.4
      subscribeStream: vi.fn().mockReturnValue(vi.fn()),
      ...overrides,
    },
  } as unknown as NodeProps<ConversationFlowNode>;

  render(
    <ReactFlowProvider>
      <ZoomSetter zoom={zoom} />
      <ConversationNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onToggleCollapse, onDelete, onSend, onDeleteMessage };
}

describe("ConversationNodeView", () => {
  it("renders a bubble per history entry with correct per-role styling and content", () => {
    renderConversationNode();
    expect(screen.getByText("world")).toBeInTheDocument(); // bold text still renders as text
    expect(screen.getByText("Hi there")).toBeInTheDocument();

    const userBubble = screen.getByText("world").closest(".conversation-node-bubble");
    const assistantBubble = screen.getByText("Hi there").closest(".conversation-node-bubble");
    expect(userBubble).toHaveClass("conversation-node-bubble", "user");
    expect(assistantBubble).toHaveClass("conversation-node-bubble", "assistant");
  });

  it("manual collapse hides the body and shows only the header", () => {
    renderConversationNode({ isCollapsed: true });
    expect(screen.getByText("Conversation")).toBeInTheDocument();
    expect(screen.queryByText("Hi there")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("the inline collapse chevron calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const { onToggleCollapse } = renderConversationNode();
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("LOD auto-collapse (zoom below threshold) also hides the body, even when isCollapsed is false", () => {
    renderConversationNodeAtZoom(0.2, { isCollapsed: false });
    expect(screen.getByText("Conversation")).toBeInTheDocument();
    expect(screen.queryByText("Hello")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("stays expanded above the LOD threshold when isCollapsed is false", () => {
    renderConversationNodeAtZoom(1, { isCollapsed: false });
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  it("each bubble's right-click menu shows exactly Copy Message and Delete from History, both real", async () => {
    const user = userEvent.setup();
    const { onDeleteMessage } = renderConversationNode();

    // ADR-011 stage 11.1: the menu's Copy Message now chains a .catch() onto
    // the clipboard write (D11), so the mock must resolve like a real
    // Promise-returning clipboard API - a bare vi.fn() (no resolved value)
    // would make that .catch() call throw synchronously on `undefined`.
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const userBubble = screen.getByText("world").closest(".conversation-node-bubble") as HTMLElement;
    fireEvent.contextMenu(userBubble);
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Copy Message");
    expect(items[1]).toHaveTextContent("Delete from History");
    expect(items[0]).toBeEnabled();
    expect(items[1]).toBeEnabled();

    await user.click(screen.getByRole("menuitem", { name: "Copy Message" }));
    expect(writeText).toHaveBeenCalledWith("Hello **world**");

    fireEvent.contextMenu(userBubble);
    await user.click(screen.getByRole("menuitem", { name: "Delete from History" }));
    expect(onDeleteMessage).toHaveBeenCalledOnce();
    expect(onDeleteMessage).toHaveBeenCalledWith(0);
  });

  it("clicking Copy Message on the second bubble copies its own exact content", async () => {
    const user = userEvent.setup();
    renderConversationNode();
    // See the .mockResolvedValue comment above - the added .catch() needs a
    // real thenable, not a bare vi.fn().
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const assistantBubble = screen.getByText("Hi there").closest(".conversation-node-bubble") as HTMLElement;
    fireEvent.contextMenu(assistantBubble);
    await user.click(screen.getByRole("menuitem", { name: "Copy Message" }));
    expect(writeText).toHaveBeenCalledWith("Hi there");
  });

  it("clicking Delete from History on the second bubble calls onDeleteMessage with index 1", async () => {
    const user = userEvent.setup();
    const { onDeleteMessage } = renderConversationNode();

    const assistantBubble = screen.getByText("Hi there").closest(".conversation-node-bubble") as HTMLElement;
    fireEvent.contextMenu(assistantBubble);
    await user.click(screen.getByRole("menuitem", { name: "Delete from History" }));
    expect(onDeleteMessage).toHaveBeenCalledWith(1);
  });

  // Per-bubble chrome, extending node redesign stage 3's ChatNodeView
  // treatment here: an avatar chip + visible role label, and a
  // hover-revealed quick-action row surfacing the SAME 2 items
  // ConversationBubbleMenu already offers. Quick-action names are
  // disambiguated per-message ("Copy message N"/"Delete message N from
  // history") since, unlike ChatNodeView, N of these buttons can share one
  // node card - an adversarial review flagged the un-disambiguated static
  // names as a real accessibility gap.
  describe("per-bubble chrome: avatar + role label + quick actions", () => {
    it("renders an avatar chip with 'U'/'A' (aria-hidden) plus a visible 'You'/'Assistant' role label for each bubble", () => {
      renderConversationNode();
      const userBubble = screen.getByText("world").closest(".conversation-node-bubble") as HTMLElement;
      const assistantBubble = screen.getByText("Hi there").closest(".conversation-node-bubble") as HTMLElement;

      const userAvatar = userBubble.querySelector(".chat-node-avatar");
      expect(userAvatar).toHaveTextContent("U");
      expect(userAvatar).toHaveAttribute("aria-hidden", "true");
      expect(within(userBubble).getByText("You")).toBeInTheDocument();

      expect(assistantBubble.querySelector(".chat-node-avatar")).toHaveTextContent("A");
      expect(within(assistantBubble).getByText("Assistant")).toBeInTheDocument();
    });

    it("the quick-action Copy button on the FIRST (user, index 0) bubble copies its own content and is named for its own position", async () => {
      const user = userEvent.setup();
      renderConversationNode();
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

      const userBubble = screen.getByText("world").closest(".conversation-node-bubble") as HTMLElement;
      const copyBtn = within(userBubble).getByRole("button", { name: "Copy message 1" });
      expect(copyBtn.querySelector("path")).toBeNull(); // rest state: the copy glyph is two rects, no path

      await user.click(copyBtn);

      expect(writeText).toHaveBeenCalledWith("Hello **world**");
      await waitFor(() => expect(copyBtn.querySelector("path")).not.toBeNull());
    });

    it("the quick-action Delete button on the FIRST (user, index 0) bubble calls onDeleteMessage(0), without opening the right-click menu", async () => {
      const user = userEvent.setup();
      const { onDeleteMessage } = renderConversationNode();

      const userBubble = screen.getByText("world").closest(".conversation-node-bubble") as HTMLElement;
      await user.click(within(userBubble).getByRole("button", { name: "Delete message 1 from history" }));

      expect(onDeleteMessage).toHaveBeenCalledOnce();
      expect(onDeleteMessage).toHaveBeenCalledWith(0);
      expect(screen.queryByRole("menu")).toBeNull();
    });

    it("the quick-action buttons on the SECOND (assistant, index 1) bubble copy/delete its own content/index, named for its own position", async () => {
      const user = userEvent.setup();
      const { onDeleteMessage } = renderConversationNode();
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

      const assistantBubble = screen.getByText("Hi there").closest(".conversation-node-bubble") as HTMLElement;
      await user.click(within(assistantBubble).getByRole("button", { name: "Copy message 2" }));
      expect(writeText).toHaveBeenCalledWith("Hi there");

      await user.click(within(assistantBubble).getByRole("button", { name: "Delete message 2 from history" }));
      expect(onDeleteMessage).toHaveBeenCalledWith(1);
    });

    it("both quick-action buttons on every bubble carry the nodrag class", () => {
      renderConversationNode();
      const userBubble = screen.getByText("world").closest(".conversation-node-bubble") as HTMLElement;
      for (const name of ["Copy message 1", "Delete message 1 from history"]) {
        expect(within(userBubble).getByRole("button", { name })).toHaveClass("nodrag");
      }
    });

    // Regression guard for a real bug an adversarial review found: bubbles
    // are keyed by array index (see the render loop below), so deleting an
    // earlier message reuses the SAME component instance for whatever
    // message shifts into that slot - without a reset, a stale "copied"
    // check-glyph flash leaked onto a message nobody ever copied.
    it("deleting a copied-from bubble does not leak its 'copied' check glyph onto the message that shifts into its slot", async () => {
      const user = userEvent.setup();
      const writeText = vi.fn().mockResolvedValue(undefined);
      Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

      function Harness() {
        const [history, setHistory] = useState([
          { role: "user" as const, content: "Hello **world**", incomplete: false },
          { role: "assistant" as const, content: "Hi there", incomplete: false },
        ]);
        return (
          <ReactFlowProvider>
            <ConversationNodeView
              {...({
                id: "n0",
                selected: false,
                data: {
                  history,
                  isCollapsed: false,
                  pendingRequestId: null,
                  onToggleCollapse: () => {},
                  onDelete: () => {},
                  onSend: () => {},
                  onDeleteMessage: (index: number) => setHistory((h) => h.filter((_, i) => i !== index)),
                  onCancel: () => {},
                  onOpenDocumentView: () => {},
                },
              } as unknown as NodeProps<ConversationFlowNode>)}
            />
          </ReactFlowProvider>
        );
      }
      render(<Harness />);

      const firstBubble = screen.getByText("world").closest(".conversation-node-bubble") as HTMLElement;
      await user.click(within(firstBubble).getByRole("button", { name: "Copy message 1" }));
      await waitFor(() => expect(within(firstBubble).getByRole("button", { name: "Copy message 1" }).querySelector("path")).not.toBeNull());

      await user.click(within(firstBubble).getByRole("button", { name: "Delete message 1 from history" }));

      const survivor = screen.getByText("Hi there").closest(".conversation-node-bubble") as HTMLElement;
      const survivorCopyBtn = within(survivor).getByRole("button", { name: "Copy message 1" });
      expect(survivorCopyBtn.querySelector("path")).toBeNull();
    });
  });

  it("a single assistant-only message still renders the avatar chip, 'Assistant' role label, and quick actions", () => {
    renderConversationNode({ history: [{ role: "assistant", content: "Solo reply", incomplete: false }] });
    const bubble = screen.getByText("Solo reply").closest(".conversation-node-bubble") as HTMLElement;
    expect(bubble.querySelector(".chat-node-avatar")).toHaveTextContent("A");
    expect(within(bubble).getByText("Assistant")).toBeInTheDocument();
    expect(within(bubble).getByRole("button", { name: "Copy message 1" })).toBeInTheDocument();
  });

  it("a bubble right-click does not also open the node-level menu", () => {
    renderConversationNode();
    const userBubble = screen.getByText("world").closest(".conversation-node-bubble") as HTMLElement;
    fireEvent.contextMenu(userBubble);
    expect(screen.getAllByRole("menu")).toHaveLength(1);
    expect(screen.queryByRole("menuitem", { name: "Delete Node" })).toBeNull();
  });

  it("the node-level right-click menu shows exactly 3 items: Open Document View (real), Collapse/Expand (real), Delete Node (real)", async () => {
    const user = userEvent.setup();
    const { onDelete, onToggleCollapse } = renderConversationNode();

    const header = screen.getByText("Conversation");
    fireEvent.contextMenu(header);
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(3);

    const docView = screen.getByRole("menuitem", { name: "Open Document View" });
    expect(docView).toBeEnabled();

    const collapseItem = screen.getByRole("menuitem", { name: "Collapse" });
    expect(collapseItem).toBeEnabled();

    const deleteItem = screen.getByRole("menuitem", { name: "Delete Node" });
    expect(deleteItem).toBeEnabled();

    // Explicitly absent: neither of these belongs to this node kind's menu.
    expect(screen.queryByRole("menuitem", { name: "Include Previous Branch Context" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Hide Other Branches" })).toBeNull();
    expect(screen.queryByText("Include Previous Branch Context")).toBeNull();
    expect(screen.queryByText("Hide Other Branches")).toBeNull();

    await user.click(collapseItem);
    expect(onToggleCollapse).toHaveBeenCalledOnce();

    fireEvent.contextMenu(header);
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("Open Document View calls onOpenDocumentView then closes the menu", async () => {
    const user = userEvent.setup();
    const { onOpenDocumentView } = renderConversationNode();

    const header = screen.getByText("Conversation");
    fireEvent.contextMenu(header);
    await user.click(screen.getByRole("menuitem", { name: "Open Document View" }));

    expect(onOpenDocumentView).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("the node-level menu's Collapse/Expand label flips when isCollapsed is true", () => {
    renderConversationNode({ isCollapsed: true });
    // isCollapsed alone collapses the body, so use the header label to open
    // the node-level menu (still visible while collapsed).
    fireEvent.contextMenu(screen.getByText("Conversation"));
    expect(screen.getByRole("menuitem", { name: "Expand" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Collapse" })).toBeNull();
  });

  it("typing text and pressing Enter calls onSend with the trimmed text and clears the input", async () => {
    const user = userEvent.setup();
    const { onSend } = renderConversationNode();
    const input = screen.getByRole("textbox", { name: "Message" });

    await user.type(input, "  hello there  {Enter}");
    expect(onSend).toHaveBeenCalledWith("hello there");
    expect(input).toHaveValue("");
  });

  it("Shift+Enter does not send and instead allows a newline", async () => {
    const user = userEvent.setup();
    const { onSend } = renderConversationNode();
    const input = screen.getByRole("textbox", { name: "Message" });

    await user.type(input, "line one{Shift>}{Enter}{/Shift}line two");
    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue("line one\nline two");
  });

  it("Enter fired while an IME is still composing does not send", async () => {
    const { onSend } = renderConversationNode();
    const input = screen.getByRole("textbox", { name: "Message" });

    fireEvent.change(input, { target: { value: "半角" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });

    expect(onSend).not.toHaveBeenCalled();
    expect(input).toHaveValue("半角");
  });

  it("the Send button is disabled when the input is empty or whitespace-only", async () => {
    const user = userEvent.setup();
    renderConversationNode();
    const input = screen.getByRole("textbox", { name: "Message" });
    const sendButton = screen.getByRole("button", { name: "Send" });

    expect(sendButton).toBeDisabled();
    await user.type(input, "   ");
    expect(sendButton).toBeDisabled();
    await user.type(input, "real text");
    expect(sendButton).toBeEnabled();
  });

  it("clicking the Send button calls onSend and clears the input", async () => {
    const user = userEvent.setup();
    const { onSend } = renderConversationNode();
    const input = screen.getByRole("textbox", { name: "Message" });

    await user.type(input, "click to send");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("click to send");
    expect(input).toHaveValue("");
  });

  it("the Cancel button is absent when pendingRequestId is null", () => {
    renderConversationNode({ pendingRequestId: null });
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("the Cancel button is present and calls onCancel when pendingRequestId is set", async () => {
    const user = userEvent.setup();
    const { onCancel } = renderConversationNode({ pendingRequestId: "req-42" });
    const cancelButton = screen.getByRole("button", { name: "Cancel" });
    expect(cancelButton).toBeInTheDocument();
    await user.click(cancelButton);
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it("the Send button is disabled while pendingRequestId is set, even with non-empty draft text", async () => {
    const user = userEvent.setup();
    renderConversationNode({ pendingRequestId: "req-42" });
    const input = screen.getByRole("textbox", { name: "Message" });
    const sendButton = screen.getByRole("button", { name: "Send" });

    await user.type(input, "real text");
    expect(sendButton).toBeDisabled();
  });

  it("Escape and outside-click both close the node-level menu", async () => {
    const user = userEvent.setup();
    renderConversationNode();
    const header = screen.getByText("Conversation");

    fireEvent.contextMenu(header);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.contextMenu(header);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Escape and outside-click both close a bubble's menu", async () => {
    const user = userEvent.setup();
    renderConversationNode();
    const userBubble = screen.getByText("world").closest(".conversation-node-bubble") as HTMLElement;

    fireEvent.contextMenu(userBubble);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.contextMenu(userBubble);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

// ADR-006 stage 6.4: same listener-capturing mock shape as
// CodeSandboxNodeView.test.tsx's own makeSubscribeStreamMock.
type StreamListener = (delta: string, done: boolean, reset: boolean, seq: number) => void;

function makeSubscribeStreamMock() {
  const listeners = new Map<string, StreamListener>();
  const unsubscribe = vi.fn();
  const subscribeStream = vi.fn((requestId: string, listener: StreamListener) => {
    listeners.set(requestId, listener);
    return unsubscribe;
  });
  return { subscribeStream, listeners, unsubscribe };
}

describe("ConversationNodeView live reply streaming (ADR-006 stage 6.4)", () => {
  it("renders a live assistant bubble accumulating deltas after the persisted messages while a reply is in flight", () => {
    // ADR-011 stage 11.4 (extended to this view 2026-08-12): non-reset/
    // non-done deltas are throttled to a rAF-scheduled flush rather than
    // applied synchronously, so advance past one frame to let the
    // fake-timer-backed requestAnimationFrame fire. Same idiom as
    // ChatNodeView.test.tsx's equivalent streaming test.
    vi.useFakeTimers();
    try {
      const { subscribeStream, listeners } = makeSubscribeStreamMock();
      renderConversationNode({ pendingRequestId: "req-1", subscribeStream });
      expect(subscribeStream).toHaveBeenCalledWith("req-1", expect.any(Function));
      // Pre-first-delta placeholder, inside its own streaming bubble.
      expect(screen.getByText("Waiting for response…")).toBeInTheDocument();

      const listener = listeners.get("req-1")!;
      act(() => listener("Hello ", false, false, 1));
      act(() => listener("World", false, false, 2));
      act(() => {
        vi.advanceTimersByTime(20);
      });
      const streamingBubble = screen
        .getByText("Hello World")
        .closest(".conversation-node-bubble") as HTMLElement;
      expect(streamingBubble).toHaveClass("conversation-node-bubble-streaming", "assistant");
      // Persisted messages still render before it.
      expect(screen.getByText("Hi there")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("coalesces a burst of deltas into a single markdown re-parse (ADR-011 stage 11.4)", () => {
    // The point of the throttle: many deltas inside one frame must produce
    // ONE state commit, not one per delta. Without it this view re-ran the
    // full unified/remark/rehype/KaTeX/highlight pipeline on every token.
    vi.useFakeTimers();
    try {
      const { subscribeStream, listeners } = makeSubscribeStreamMock();
      renderConversationNode({ pendingRequestId: "req-1", subscribeStream });
      const listener = listeners.get("req-1")!;

      act(() => {
        for (const token of ["a", "b", "c", "d", "e"]) listener(token, false, false, 1);
      });
      // Nothing committed yet - all five are still buffered in the ref.
      expect(screen.queryByText("abcde")).toBeNull();
      expect(screen.getByText("Waiting for response…")).toBeInTheDocument();

      act(() => {
        vi.advanceTimersByTime(20);
      });
      // One flush, all five bytes, in order.
      expect(screen.getByText("abcde")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("flushes the final chunk synchronously on done, so no trailing text is stranded", () => {
    vi.useFakeTimers();
    try {
      const { subscribeStream, listeners } = makeSubscribeStreamMock();
      renderConversationNode({ pendingRequestId: "req-1", subscribeStream });
      const listener = listeners.get("req-1")!;

      act(() => listener("partial", false, false, 1));
      // done=true must not wait for the next frame - assert with timers held.
      act(() => listener(" and the rest", true, false, 2));
      expect(screen.getByText("partial and the rest")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a reset frame clears prior accumulated text before appending", () => {
    const { subscribeStream, listeners } = makeSubscribeStreamMock();
    renderConversationNode({ pendingRequestId: "req-1", subscribeStream });
    const listener = listeners.get("req-1")!;

    act(() => listener("stale first attempt", false, false, 1));
    act(() => listener("fresh start", false, true, 2));
    expect(screen.queryByText(/stale first attempt/)).toBeNull();
    expect(screen.getByText("fresh start")).toBeInTheDocument();
  });

  it("renders no streaming bubble and never subscribes when no reply is in flight", () => {
    const { subscribeStream } = makeSubscribeStreamMock();
    renderConversationNode({ pendingRequestId: null, subscribeStream });
    expect(subscribeStream).not.toHaveBeenCalled();
    expect(document.querySelector(".conversation-node-bubble-streaming")).toBeNull();
  });
});

describe("ConversationNodeView interrupted message marker (ADR-006 stage 6.4)", () => {
  it("renders the Interrupted badge on a history message with incomplete: true", () => {
    renderConversationNode({
      history: [
        { role: "user", content: "Hello", incomplete: false },
        { role: "assistant", content: "partial reply", incomplete: true },
      ],
    });
    const bubble = screen.getByText("partial reply").closest(".conversation-node-bubble") as HTMLElement;
    const badge = within(bubble).getByText("Interrupted");
    expect(badge).toHaveClass("conversation-node-incomplete-badge");
  });

  it("renders no Interrupted badge on complete messages", () => {
    renderConversationNode();
    expect(screen.queryByText("Interrupted")).toBeNull();
  });
});

// ADR-011 stage 11.1: React.memo comparator correctness. `lodVisibilityCalls`
// (see the mock above) fires exactly once per ACTUAL render of this view -
// never on a bailed-out one - so it's the oracle for both directions: it must
// stay flat across an irrelevant/equivalent prop change (too-tight would fail
// this) and must increment on a change to a prop the view actually reads
// (too-loose would fail this).
describe("ConversationNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  beforeEach(() => {
    lodVisibilityCalls.count = 0;
  });

  function baseConversationProps(overrides: Partial<ConversationFlowNode["data"]> = {}) {
    const data = {
      history: [{ role: "user" as const, content: "Hello", incomplete: false }],
      isCollapsed: false,
      pendingRequestId: null,
      onToggleCollapse: vi.fn(),
      onDelete: vi.fn(),
      onSend: vi.fn(),
      onDeleteMessage: vi.fn(),
      onCancel: vi.fn(),
      onOpenDocumentView: vi.fn(),
      subscribeStream: vi.fn().mockReturnValue(vi.fn()),
      ...overrides,
    };
    return { id: "n0", selected: false, data } as unknown as NodeProps<ConversationFlowNode>;
  }

  it("skips re-rendering when a fresh `data` object carries identical field values", () => {
    const props = baseConversationProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <ConversationNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    // A brand-new object, same primitives, same `history` array reference and
    // same callback references - exactly what toFlowNodes may mint on an
    // unrelated snapshot. A naive `data === nextData` reference compare (or
    // React.memo's default shallow-props compare) would wrongly re-render here.
    const sameValuesNewObject = { ...props.data };
    rerender(
      <ReactFlowProvider>
        <ConversationNodeView {...{ ...props, data: sameValuesNewObject }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("skips re-rendering when `history` is a brand-new array of byte-identical message objects", () => {
    // Proves the comparator does shape-aware element compare on `history`,
    // not a naive `===` (which would always miss here since this is a fresh
    // array instance) and not a naive "always unequal for arrays" shortcut
    // either (which would defeat memoization for every conversation node).
    const props = baseConversationProps({
      history: [{ role: "user", content: "Hello", incomplete: false }],
    });
    const { rerender } = render(
      <ReactFlowProvider>
        <ConversationNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    const freshHistory = [{ role: "user" as const, content: "Hello", incomplete: false }];
    rerender(
      <ReactFlowProvider>
        <ConversationNodeView {...{ ...props, data: { ...props.data, history: freshHistory } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("re-renders when `history` content actually changes", () => {
    const props = baseConversationProps({
      history: [{ role: "user", content: "Hello", incomplete: false }],
    });
    const { rerender } = render(
      <ReactFlowProvider>
        <ConversationNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    const changedHistory = [{ role: "user" as const, content: "Hello there", incomplete: false }];
    rerender(
      <ReactFlowProvider>
        <ConversationNodeView {...{ ...props, data: { ...props.data, history: changedHistory } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
    expect(screen.getByText("Hello there")).toBeInTheDocument();
  });

  it("re-renders when a callback prop is rebound to a new closure (e.g. onDelete)", () => {
    const props = baseConversationProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <ConversationNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <ConversationNodeView {...{ ...props, data: { ...props.data, onDelete: vi.fn() } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
  });

  it("re-renders when `selected` changes", () => {
    const props = baseConversationProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <ConversationNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <ConversationNodeView {...{ ...props, selected: true }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
  });
});
