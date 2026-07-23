import { ReactFlowProvider, useStoreApi, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { ConversationNodeView, type ConversationFlowNode } from "./ConversationNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderConversationNode(overrides: Partial<ConversationFlowNode["data"]> = {}) {
  const onToggleCollapse = vi.fn();
  const onDelete = vi.fn();
  const onSend = vi.fn();
  const onDeleteMessage = vi.fn();
  const onCancel = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      history: [
        { role: "user" as const, content: "Hello **world**" },
        { role: "assistant" as const, content: "Hi there" },
      ],
      isCollapsed: false,
      pendingRequestId: null,
      onToggleCollapse,
      onDelete,
      onSend,
      onDeleteMessage,
      onCancel,
      ...overrides,
    },
  } as unknown as NodeProps<ConversationFlowNode>;

  render(
    <ReactFlowProvider>
      <ConversationNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onToggleCollapse, onDelete, onSend, onDeleteMessage, onCancel };
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
  const props = {
    id: "n0",
    selected: false,
    data: {
      history: [{ role: "user" as const, content: "Hello" }],
      isCollapsed: false,
      pendingRequestId: null,
      onToggleCollapse,
      onDelete,
      onSend,
      onDeleteMessage,
      onCancel,
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

    const writeText = vi.fn();
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
    const writeText = vi.fn();
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

  it("a bubble right-click does not also open the node-level menu", () => {
    renderConversationNode();
    const userBubble = screen.getByText("world").closest(".conversation-node-bubble") as HTMLElement;
    fireEvent.contextMenu(userBubble);
    expect(screen.getAllByRole("menu")).toHaveLength(1);
    expect(screen.queryByRole("menuitem", { name: "Delete Node" })).toBeNull();
  });

  it("the node-level right-click menu shows exactly 3 items: Open Document View (disabled, exact title), Collapse/Expand (real), Delete Node (real)", async () => {
    const user = userEvent.setup();
    const { onDelete, onToggleCollapse } = renderConversationNode();

    const header = screen.getByText("Conversation");
    fireEvent.contextMenu(header);
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(3);

    const docView = screen.getByRole("menuitem", { name: "Open Document View" });
    expect(docView).toBeDisabled();
    // Copied verbatim from ChatNodeView.tsx's own disabled "Open Document
    // View" menu item title string.
    expect(docView).toHaveAttribute("title", "Document view integration isn't wired into the SPA yet");

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
