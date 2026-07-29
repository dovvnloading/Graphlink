import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatNodeView, makeDebouncedScrollReport, type ChatFlowNode } from "./ChatNodeView";

// R7.5a: jsdom implements neither URL.createObjectURL nor
// URL.revokeObjectURL - same hand-installed-fakes pattern
// ImageNodeView.test.tsx's own Export Image tests already established.
beforeEach(() => {
  URL.createObjectURL = vi.fn().mockReturnValue("blob:fake-object-url");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// Rendered directly (not through a real <ReactFlow nodes=.../> mount): RF's
// own node wrapper stays `visibility: hidden` in jsdom until its
// ResizeObserver-driven measurement pass completes, and forcing that pass
// hits jsdom gaps deep in RF's internals (missing DOMMatrixReadOnly). Since
// ChatNodeView only needs a ReactFlowProvider ancestor for its own
// useStore(zoom) read - not RF's node-mounting/measurement pipeline - a
// bare ReactFlowProvider is enough, and the component renders immediately
// visible with no jsdom polyfills required.
function renderChatNode(overrides: Partial<ChatFlowNode["data"]> = {}) {
  const onToggleCollapse = vi.fn();
  const onDelete = vi.fn();
  const onUndockChild = vi.fn();
  const onRegenerate = vi.fn();
  const onGenerateImage = vi.fn();
  const onGenerateChart = vi.fn();
  const onGenerateKeyTakeaway = vi.fn();
  const onGenerateExplainerNote = vi.fn();
  const onOpenDocumentView = vi.fn();
  const onScrollChange = vi.fn();
  const onToggleBranchFocus = vi.fn();
  const onBranchFromHere = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      content: "Hello **world**",
      isUser: true,
      isCollapsed: false,
      dockedChildren: [],
      chatScrollValue: 0,
      onToggleCollapse,
      onDelete,
      onUndockChild,
      onRegenerate,
      onGenerateImage,
      onGenerateChart,
      onGenerateKeyTakeaway,
      onGenerateExplainerNote,
      onOpenDocumentView,
      onScrollChange,
      isBranchFocusActive: false,
      onToggleBranchFocus,
      onBranchFromHere,
      ...overrides,
    },
  } as unknown as NodeProps<ChatFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <ChatNodeView {...props} />
    </ReactFlowProvider>,
  );
  return {
    onToggleCollapse, onDelete, onUndockChild, onRegenerate, onGenerateImage,
    onGenerateChart, onGenerateKeyTakeaway, onGenerateExplainerNote,
    onOpenDocumentView, onScrollChange, onToggleBranchFocus, onBranchFromHere, container,
  };
}

describe("ChatNodeView", () => {
  it("renders the role and markdown content", () => {
    renderChatNode();
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getByText("world")).toBeInTheDocument(); // bold text still renders as text
  });

  it("shows Assistant for a non-user message and hides content when collapsed", () => {
    renderChatNode({ isUser: false, isCollapsed: true });
    expect(screen.getByText("Assistant")).toBeInTheDocument();
    expect(screen.queryByText(/Hello/)).toBeNull();
  });

  it("the inline collapse button calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const { onToggleCollapse } = renderChatNode();
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("right-click opens a menu with real Copy/Collapse/Delete and every deferred item honestly disabled+titled", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderChatNode({ isUser: false });

    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const role = screen.getByText("Assistant");
    fireEvent.contextMenu(role);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    expect(screen.getByRole("menuitem", { name: "Export" })).not.toBeDisabled();
    // R8a: no longer a disabled stub - see the dedicated Hide Other Branches
    // describe block below for its data-driven-label behavior.
    const hideBranches = screen.getByRole("menuitem", { name: "Hide Other Branches" });
    expect(hideBranches).not.toBeDisabled();
    expect(hideBranches).not.toHaveAttribute("title");
    // R8a: these three were disabled stubs until their agents/dialog were
    // wired up (Open Document View: the shared DocumentViewDialog; Key
    // Takeaway/Explainer Note: ported back from the deleted Qt app). This
    // assertion is deliberately inverted rather than removed - it was the
    // guard that encoded the stub as correct, so it has to now encode the
    // opposite.
    const docView = screen.getByRole("menuitem", { name: "Open Document View" });
    expect(docView).not.toBeDisabled();
    expect(docView).not.toHaveAttribute("title");
    for (const name of ["Generate Key Takeaway", "Generate Explainer Note"]) {
      const item = screen.getByRole("menuitem", { name });
      expect(item).not.toBeDisabled();
      expect(item).not.toHaveAttribute("title");
    }
    expect(screen.getByRole("menuitem", { name: "Generate Image" })).not.toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Generate Chart" })).not.toBeDisabled();

    await user.click(screen.getByRole("menuitem", { name: "Copy Text" }));
    expect(writeText).toHaveBeenCalledWith("Hello **world**");

    fireEvent.contextMenu(role);
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("clicking Export downloads the raw content (not rendered markdown) as a .md file, then closes the menu (R7.5a)", async () => {
    const user = userEvent.setup();
    renderChatNode({ content: "Hello **world**" });

    const captured: { anchor?: HTMLAnchorElement } = {};
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        captured.anchor = this;
      });

    fireEvent.contextMenu(screen.getByText("You"));
    await user.click(screen.getByRole("menuitem", { name: "Export" }));

    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(URL.createObjectURL).toHaveBeenCalled();
    const blobArg = (URL.createObjectURL as ReturnType<typeof vi.fn>).mock.calls[0][0] as Blob;
    expect(await blobArg.text()).toBe("Hello **world**");
    expect(captured.anchor?.getAttribute("href")).toBe("blob:fake-object-url");
    expect(captured.anchor?.getAttribute("download")).toBe("chat-n0.md");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fake-object-url");
    expect(screen.queryByRole("menu")).toBeNull(); // onClose fires after Export
  });

  it("Regenerate Response only appears for assistant messages, matching the legacy is_user guard", () => {
    renderChatNode({ isUser: true });
    fireEvent.contextMenu(screen.getByText("You"));
    expect(screen.queryByRole("menuitem", { name: "Regenerate Response" })).toBeNull();
  });

  it("Regenerate Response is a real, enabled item for an assistant message that calls onRegenerate then closes the menu", async () => {
    const user = userEvent.setup();
    const { onRegenerate } = renderChatNode({ isUser: false });

    fireEvent.contextMenu(screen.getByText("Assistant"));
    const regenerate = screen.getByRole("menuitem", { name: "Regenerate Response" });
    expect(regenerate).not.toBeDisabled();

    await user.click(regenerate);
    expect(onRegenerate).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull(); // onClose fires after onRegenerate
  });

  it("Generate Image is a real, enabled item (unconditional, unlike Regenerate Response) that calls onGenerateImage then closes the menu", async () => {
    const user = userEvent.setup();
    const { onGenerateImage } = renderChatNode({ isUser: true });

    fireEvent.contextMenu(screen.getByText("You"));
    const generateImage = screen.getByRole("menuitem", { name: "Generate Image" });
    expect(generateImage).not.toBeDisabled();

    await user.click(generateImage);
    expect(onGenerateImage).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull(); // onClose fires after onGenerateImage
  });

  it("Open Document View calls onOpenDocumentView then closes the menu", async () => {
    const user = userEvent.setup();
    const { onOpenDocumentView } = renderChatNode({ isUser: false });

    fireEvent.contextMenu(screen.getByText("Assistant"));
    await user.click(screen.getByRole("menuitem", { name: "Open Document View" }));

    expect(onOpenDocumentView).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Generate Key Takeaway calls onGenerateKeyTakeaway then closes the menu", async () => {
    const user = userEvent.setup();
    const { onGenerateKeyTakeaway, onGenerateExplainerNote } = renderChatNode({ isUser: false });

    fireEvent.contextMenu(screen.getByText("Assistant"));
    await user.click(screen.getByRole("menuitem", { name: "Generate Key Takeaway" }));

    expect(onGenerateKeyTakeaway).toHaveBeenCalledOnce();
    // The two sit adjacent in the menu - assert the other one did NOT fire,
    // so a future edit can't silently wire both buttons to one handler.
    expect(onGenerateExplainerNote).not.toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Generate Explainer Note calls onGenerateExplainerNote then closes the menu", async () => {
    const user = userEvent.setup();
    const { onGenerateExplainerNote, onGenerateKeyTakeaway } = renderChatNode({ isUser: false });

    fireEvent.contextMenu(screen.getByText("Assistant"));
    await user.click(screen.getByRole("menuitem", { name: "Generate Explainer Note" }));

    expect(onGenerateExplainerNote).toHaveBeenCalledOnce();
    expect(onGenerateKeyTakeaway).not.toHaveBeenCalled();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("both note agents are offered on a USER node too, matching legacy's unconditional enablement", () => {
    renderChatNode({ isUser: true });
    fireEvent.contextMenu(screen.getByText("You"));
    expect(screen.getByRole("menuitem", { name: "Generate Key Takeaway" })).not.toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Generate Explainer Note" })).not.toBeDisabled();
  });

  it("Generate Chart is a real, enabled item with aria-haspopup that starts collapsed (no submenu items in the DOM yet)", () => {
    renderChatNode({ isUser: true });
    fireEvent.contextMenu(screen.getByText("You"));

    const generateChart = screen.getByRole("menuitem", { name: "Generate Chart" });
    expect(generateChart).not.toBeDisabled();
    expect(generateChart).toHaveAttribute("aria-haspopup", "true");
    expect(generateChart).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menuitem", { name: "Bar" })).toBeNull();
  });

  it("clicking Generate Chart expands a submenu offering all 5 chart types in legacy's own order", async () => {
    const user = userEvent.setup();
    renderChatNode({ isUser: true });
    fireEvent.contextMenu(screen.getByText("You"));

    await user.click(screen.getByRole("menuitem", { name: "Generate Chart" }));
    expect(screen.getByRole("menuitem", { name: "Generate Chart" })).toHaveAttribute("aria-expanded", "true");

    const submenu = screen.getByRole("menu", { name: "Chart type" });
    const optionNames = Array.from(submenu.querySelectorAll('[role="menuitem"]')).map((el) => el.textContent);
    expect(optionNames).toEqual(["Bar", "Line", "Histogram", "Pie", "Sankey"]);
  });

  it("clicking a chart-type submenu item calls onGenerateChart with the correct lowercase type and closes the whole menu", async () => {
    const user = userEvent.setup();
    const { onGenerateChart } = renderChatNode({ isUser: true });
    fireEvent.contextMenu(screen.getByText("You"));

    await user.click(screen.getByRole("menuitem", { name: "Generate Chart" }));
    await user.click(screen.getByRole("menuitem", { name: "Sankey" }));

    expect(onGenerateChart).toHaveBeenCalledOnce();
    expect(onGenerateChart).toHaveBeenCalledWith("sankey");
    expect(screen.queryByRole("menu", { name: "Chart type" })).toBeNull();
    // The outer context menu also closed (onClose fires after onGenerateChart,
    // same as every other real menu action in this file).
    expect(screen.queryByRole("menuitem", { name: "Copy Text" })).toBeNull();
  });

  it("Escape and outside-click both close the menu", async () => {
    const user = userEvent.setup();
    renderChatNode();
    const role = screen.getByText("You");

    fireEvent.contextMenu(role);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.contextMenu(role);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("hides the docked-count badge when dockedChildren is empty (the default)", () => {
    renderChatNode();
    expect(screen.queryByTitle("Docked items")).toBeNull();
  });

  it("shows the docked-count badge with the correct count when dockedChildren has entries", () => {
    renderChatNode({
      dockedChildren: [
        { id: "t1", label: "Thinking" },
        { id: "t2", label: "Thinking" },
      ],
    });
    expect(screen.getByTitle("Docked items")).toHaveTextContent("2");
  });

  it("omits the 'Reveal Docked Items' menu section entirely when dockedChildren is empty", () => {
    renderChatNode();
    fireEvent.contextMenu(screen.getByText("You"));
    expect(screen.queryByText("Reveal Docked Items")).toBeNull();
  });

  it("shows one labeled 'Reveal Docked Items' entry per docked child, and clicking one calls onUndockChild with its id", async () => {
    const user = userEvent.setup();
    const { onUndockChild } = renderChatNode({
      dockedChildren: [
        { id: "t1", label: "Thinking" },
        { id: "t2", label: "Thinking" },
      ],
    });

    fireEvent.contextMenu(screen.getByText("You"));
    expect(screen.getByText("Reveal Docked Items")).toBeInTheDocument();
    const entries = screen.getAllByRole("menuitem", { name: "Thinking" });
    expect(entries).toHaveLength(2);

    await user.click(entries[0]);
    expect(onUndockChild).toHaveBeenCalledOnce();
    expect(onUndockChild).toHaveBeenCalledWith("t1");
    expect(screen.queryByRole("menu")).toBeNull(); // the menu closes after any item fires
  });
});

// R8a: Hide Other Branches / Show All Branches - the one menu item in this
// file whose visible text is data-driven (isBranchFocusActive) rather than
// fixed. onToggleBranchFocus is the same callback either way - SceneCanvas.tsx
// (not this file) interprets what "toggle" means given current state.
describe("ChatNodeView Hide Other Branches / Show All Branches (R8a)", () => {
  it("labels the item 'Hide Other Branches' and calls onToggleBranchFocus then closes the menu when isBranchFocusActive is false", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus } = renderChatNode({ isBranchFocusActive: false });

    fireEvent.contextMenu(screen.getByText("You"));
    const item = screen.getByRole("menuitem", { name: "Hide Other Branches" });
    expect(item).not.toBeDisabled();

    await user.click(item);
    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("labels the item 'Show All Branches' and still calls the same onToggleBranchFocus when isBranchFocusActive is true", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus } = renderChatNode({ isBranchFocusActive: true });

    fireEvent.contextMenu(screen.getByText("You"));
    expect(screen.queryByRole("menuitem", { name: "Hide Other Branches" })).toBeNull();
    const item = screen.getByRole("menuitem", { name: "Show All Branches" });
    expect(item).not.toBeDisabled();

    await user.click(item);
    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

// ADR-002 Workstream 1: "Branch from Here" - stages this node as the
// composer's next reply target instead of creating anything itself; the
// actual fork happens server-side once the user sends a message.
describe("ChatNodeView Branch from Here (ADR-002 Workstream 1)", () => {
  it("calls onBranchFromHere and closes the menu when clicked, for a user node", async () => {
    const user = userEvent.setup();
    const { onBranchFromHere } = renderChatNode({ isUser: true });

    fireEvent.contextMenu(screen.getByText("You"));
    const item = screen.getByRole("menuitem", { name: "Branch from Here" });
    expect(item).not.toBeDisabled();

    await user.click(item);
    expect(onBranchFromHere).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("is also available (not gated on isUser) for an assistant node", async () => {
    const user = userEvent.setup();
    const { onBranchFromHere } = renderChatNode({ isUser: false });

    fireEvent.contextMenu(screen.getByText("Assistant"));
    const item = screen.getByRole("menuitem", { name: "Branch from Here" });

    await user.click(item);
    expect(onBranchFromHere).toHaveBeenCalledOnce();
  });
});

// ADR-002 Workstream 1 ("Synthesize Branches"): the result node's own
// provenance badge/label - both absent for every ordinary chat node (the
// vast majority), rendered only when the node actually carries them.
describe("ChatNodeView Synthesize Branches provenance (ADR-002 Workstream 1)", () => {
  it("renders no synthesis badge or model label for an ordinary chat node", () => {
    renderChatNode({
      isBranchSynthesis: false,
      synthesisInstructions: "",
      synthesisSourceNodeIds: [],
      provider: null,
      model: null,
    });
    expect(screen.queryByLabelText("Branch Synthesis")).toBeNull();
  });

  it("renders the synthesis badge with a tooltip naming the source count and instructions", () => {
    renderChatNode({
      isBranchSynthesis: true,
      synthesisInstructions: "merge the best of both",
      synthesisSourceNodeIds: ["chat-a", "chat-b"],
      provider: "Anthropic Claude",
      model: "claude-sonnet-5",
    });
    const badge = screen.getByLabelText("Branch Synthesis");
    expect(badge).toHaveTextContent("⇄");
    expect(badge).toHaveAttribute("title", "Branch Synthesis (2 sources): merge the best of both");
  });

  it("renders the model label with the provider as its tooltip, only when model is set", () => {
    renderChatNode({
      isBranchSynthesis: true,
      synthesisInstructions: "merge them",
      synthesisSourceNodeIds: ["chat-a", "chat-b"],
      provider: "Anthropic Claude",
      model: "claude-sonnet-5",
    });
    expect(screen.getByText("claude-sonnet-5")).toHaveAttribute("title", "Anthropic Claude");
  });

  it("renders no model label when model is null, even if isBranchSynthesis is true", () => {
    renderChatNode({
      isBranchSynthesis: true,
      synthesisInstructions: "merge them",
      synthesisSourceNodeIds: ["chat-a", "chat-b"],
      provider: null,
      model: null,
    });
    expect(screen.queryByText("claude-sonnet-5")).toBeNull();
  });
});

// R6.3: the node's own scroll position within .chat-node-content.
describe("ChatNodeView scroll position (R6.3)", () => {
  it("restores the saved chatScrollValue into .chat-node-content's scrollTop once on mount", () => {
    const { container } = renderChatNode({ chatScrollValue: 250 });
    const content = container.querySelector(".chat-node-content");
    expect(content).not.toBeNull();
    expect((content as HTMLDivElement).scrollTop).toBe(250);
  });

  it("defaults to scrollTop 0 when chatScrollValue is 0 (the default)", () => {
    const { container } = renderChatNode({ chatScrollValue: 0 });
    const content = container.querySelector(".chat-node-content") as HTMLDivElement;
    expect(content.scrollTop).toBe(0);
  });

  it("scrolling reports the new position (debounced) via onScrollChange", () => {
    vi.useFakeTimers();
    try {
      const { container, onScrollChange } = renderChatNode({ chatScrollValue: 0 });
      const content = container.querySelector(".chat-node-content") as HTMLDivElement;

      content.scrollTop = 120;
      fireEvent.scroll(content);
      expect(onScrollChange).not.toHaveBeenCalled(); // still debouncing

      vi.advanceTimersByTime(200);
      expect(onScrollChange).toHaveBeenCalledOnce();
      expect(onScrollChange).toHaveBeenCalledWith(120);
    } finally {
      vi.useRealTimers();
    }
  });

  it("collapsing hides .chat-node-content, so no scroll container exists to attach onScroll to", () => {
    const { container } = renderChatNode({ isCollapsed: true });
    expect(container.querySelector(".chat-node-content")).toBeNull();
  });
});

describe("makeDebouncedScrollReport", () => {
  it("does not call onScrollChange until debounceMs have elapsed with no further calls", () => {
    vi.useFakeTimers();
    try {
      const onScrollChange = vi.fn();
      const timerRef: { current: ReturnType<typeof setTimeout> | null } = { current: null };
      const debounced = makeDebouncedScrollReport(timerRef, onScrollChange, 200);

      debounced(75);
      expect(onScrollChange).not.toHaveBeenCalled();
      vi.advanceTimersByTime(199);
      expect(onScrollChange).not.toHaveBeenCalled();
      vi.advanceTimersByTime(1);
      expect(onScrollChange).toHaveBeenCalledOnce();
      expect(onScrollChange).toHaveBeenCalledWith(75);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a call before the debounce window elapses cancels the previous one - only the LAST scroll position fires", () => {
    vi.useFakeTimers();
    try {
      const onScrollChange = vi.fn();
      const timerRef: { current: ReturnType<typeof setTimeout> | null } = { current: null };
      const debounced = makeDebouncedScrollReport(timerRef, onScrollChange, 200);

      debounced(50);
      vi.advanceTimersByTime(150);
      debounced(90); // simulates continued scrolling before the debounce settled
      vi.advanceTimersByTime(150);
      expect(onScrollChange).not.toHaveBeenCalled();
      vi.advanceTimersByTime(50);
      expect(onScrollChange).toHaveBeenCalledOnce();
      expect(onScrollChange).toHaveBeenCalledWith(90);
    } finally {
      vi.useRealTimers();
    }
  });
});
