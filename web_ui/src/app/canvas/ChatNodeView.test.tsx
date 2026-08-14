import { createElement } from "react";
import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CHAT_CONTENT_COLLAPSED_MAX_HEIGHT,
  ChatNodeView,
  chatNodePropsAreEqual,
  contentExceedsCollapsedHeight,
  makeDebouncedScrollReport,
  type ChatFlowNode,
} from "./ChatNodeView";
import { NodeMarkdown } from "./NodeMarkdown";
import { WsTransport } from "../../lib/ws/transport";

// ADR-011 stage 11.4: wraps the REAL NodeMarkdown implementation in a
// vi.fn() spy rather than replacing it - every other test in this file keeps
// exercising the genuine unified/remark/rehype/KaTeX/highlight pipeline (bold
// text, GFM tables, the SECURITY raw-HTML-passthrough guards, ...) exactly as
// before; this only adds call-count instrumentation on top, letting the
// throttled-streaming tests below assert "re-parsed fewer times than the
// delta count" without touching what gets rendered.
vi.mock("./NodeMarkdown", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./NodeMarkdown")>();
  // NodeMarkdown is React.memo(...) - a memo descriptor object, not a plain
  // callable function - so it can no longer be handed to vi.fn() directly as
  // the implementation (vi.fn calls implementation.apply(...) internally, and
  // a memo object has no .apply). Spy on a plain wrapper function instead
  // that renders an element of the REAL memoized component: call-count
  // instrumentation keeps working exactly as before (every ChatNodeView
  // re-render that reaches this JSX position still increments the spy,
  // same as when NodeMarkdown was an unmemoized plain function), while the
  // actual markdown parse still runs through the genuine memoized component.
  return {
    ...actual,
    NodeMarkdown: vi.fn((props: { content: string }) => createElement(actual.NodeMarkdown, props)),
  };
});

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
function renderChatNode(overrides: Partial<ChatFlowNode["data"]> = {}, selected: boolean = false) {
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
  const onSetBranchStatus = vi.fn();
  const onSetFinalDeliverable = vi.fn();
  const onCollapseBranch = vi.fn();
  const onCancelRegenerate = vi.fn();
  const onPinToCurrentModel = vi.fn();
  const onClearModelOverride = vi.fn();
  function buildProps(dataOverrides: Partial<ChatFlowNode["data"]>) {
    return {
      id: "n0",
      selected,
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
        branchStatus: "active",
        isFinalDeliverable: false,
        onSetBranchStatus,
        onSetFinalDeliverable,
        onCollapseBranch,
        // ADR-006 stage 6.4
        pendingRequestId: null,
        responseIncomplete: false,
        subscribeStream: vi.fn().mockReturnValue(vi.fn()),
        onCancelRegenerate,
        // ADR-007 stage 7.4
        toolInvocations: [],
        // ADR-016 stage 16.2
        promptTokens: null,
        completionTokens: null,
        estimatedCostUsd: null,
        // ADR-018 stage 18.3
        overrideProvider: "",
        overrideModelId: "",
        onPinToCurrentModel,
        onClearModelOverride,
        ...dataOverrides,
      },
    } as unknown as NodeProps<ChatFlowNode>;
  }

  const { container, rerender } = render(
    <ReactFlowProvider>
      <ChatNodeView {...buildProps(overrides)} />
    </ReactFlowProvider>,
  );
  // Re-renders with a fresh set of data overrides (merged over the same
  // defaults above) - only needed by tests that must trigger a NEW commit
  // (e.g. the content-overflow measurement effect below, keyed on
  // data.content) after first mutating the rendered DOM directly.
  function rerenderWithData(dataOverrides: Partial<ChatFlowNode["data"]>) {
    rerender(
      <ReactFlowProvider>
        <ChatNodeView {...buildProps(dataOverrides)} />
      </ReactFlowProvider>,
    );
  }
  return {
    onToggleCollapse, onDelete, onUndockChild, onRegenerate, onGenerateImage,
    onGenerateChart, onGenerateKeyTakeaway, onGenerateExplainerNote,
    onOpenDocumentView, onScrollChange, onToggleBranchFocus, onBranchFromHere,
    onSetBranchStatus, onSetFinalDeliverable, onCollapseBranch, onCancelRegenerate,
    onPinToCurrentModel, onClearModelOverride,
    container, rerenderWithData,
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

    // Resolves (not a bare vi.fn()) - matches the real Clipboard API's
    // writeText, which always returns a Promise; ChatNodeMenu now chains
    // .catch() onto this call (ADR-011 stage 11.1), which would throw on a
    // mock returning undefined.
    const writeText = vi.fn().mockResolvedValue(undefined);
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
    // R8a: these three were disabled stubs until their agents/panel were
    // wired up (Open Document View: the shared DocumentViewPanel; Key
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

// ADR-018 stage 18.3: the model-override PIN - opposite direction from the
// provenance badge above (an explicit input pin, not output provenance),
// so both must be able to render at once on the same node.
describe("ChatNodeView model-override pin (ADR-018 stage 18.3)", () => {
  it("renders no pin badge or Clear Model Pin item for an ordinary node", () => {
    renderChatNode({ overrideProvider: "", overrideModelId: "" });
    expect(screen.queryByText(/📌/)).toBeNull();
    fireEvent.contextMenu(screen.getByText("You"));
    expect(screen.queryByRole("menuitem", { name: /Clear Model Pin/ })).toBeNull();
    expect(screen.getByRole("menuitem", { name: "Pin to Current Model" })).toBeInTheDocument();
  });

  it("renders the pin badge with the full provider/model as its tooltip", () => {
    renderChatNode({ overrideProvider: "Anthropic Claude", overrideModelId: "claude-opus-5" });
    const badge = screen.getByText("📌 claude-opus-5");
    expect(badge).toHaveAttribute("title", "Pinned to Anthropic Claude - claude-opus-5");
  });

  it("co-renders the pin badge alongside the provenance model badge - they are distinct signals", () => {
    renderChatNode({
      provider: "OpenAI-Compatible", model: "gpt-4o",
      overrideProvider: "Anthropic Claude", overrideModelId: "claude-opus-5",
    });
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    expect(screen.getByText("📌 claude-opus-5")).toBeInTheDocument();
  });

  it("Pin to Current Model calls onPinToCurrentModel and closes the menu", async () => {
    const user = userEvent.setup();
    const { onPinToCurrentModel } = renderChatNode();

    fireEvent.contextMenu(screen.getByText("You"));
    await user.click(screen.getByRole("menuitem", { name: "Pin to Current Model" }));

    expect(onPinToCurrentModel).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Clear Model Pin names the pinned model, calls onClearModelOverride, and closes the menu", async () => {
    const user = userEvent.setup();
    const { onClearModelOverride } = renderChatNode({
      overrideProvider: "Anthropic Claude", overrideModelId: "claude-opus-5",
    });

    fireEvent.contextMenu(screen.getByText("You"));
    const item = screen.getByRole("menuitem", { name: "Clear Model Pin (claude-opus-5)" });
    await user.click(item);

    expect(onClearModelOverride).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

// ADR-002 Workstream 1 ("Branch status and lifecycle"): the final sequenced
// item after fork/compare/synthesize - status marking, Final Deliverable,
// and collapsing a whole branch.
describe("ChatNodeView branch status badge (ADR-002 Workstream 1)", () => {
  it("always renders the status dot, even for the default 'active' status", () => {
    renderChatNode({ branchStatus: "active" });
    const dot = screen.getByLabelText("Branch status: active");
    expect(dot).toHaveAttribute("title", "Branch status: active");
  });

  it("renders a distinct status dot for accepted/rejected/superseded", () => {
    renderChatNode({ branchStatus: "accepted" });
    expect(screen.getByLabelText("Branch status: accepted")).toBeInTheDocument();
  });

  it("renders no Final Deliverable badge when isFinalDeliverable is false", () => {
    renderChatNode({ isFinalDeliverable: false });
    expect(screen.queryByLabelText("Final Deliverable")).toBeNull();
  });

  it("renders the Final Deliverable badge when isFinalDeliverable is true", () => {
    renderChatNode({ isFinalDeliverable: true });
    const badge = screen.getByLabelText("Final Deliverable");
    expect(badge).toHaveTextContent("★");
  });
});

describe("ChatNodeView Mark Status menu (ADR-002 Workstream 1)", () => {
  it("opens the submenu and shows all 4 status options with the current one checked", async () => {
    const user = userEvent.setup();
    renderChatNode({ branchStatus: "rejected" });

    fireEvent.contextMenu(screen.getByText("You"));
    await user.click(screen.getByRole("menuitem", { name: "Mark Status" }));

    expect(screen.getByRole("menuitemradio", { name: "Active" })).toHaveAttribute("aria-checked", "false");
    const rejectedOption = screen.getByRole("menuitemradio", { name: "✓ Rejected" });
    expect(rejectedOption).toHaveAttribute("aria-checked", "true");
  });

  it("clicking a status option calls onSetBranchStatus with that value and closes the menu", async () => {
    const user = userEvent.setup();
    const { onSetBranchStatus } = renderChatNode({ branchStatus: "active" });

    fireEvent.contextMenu(screen.getByText("You"));
    await user.click(screen.getByRole("menuitem", { name: "Mark Status" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Accepted" }));

    expect(onSetBranchStatus).toHaveBeenCalledWith("accepted");
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

describe("ChatNodeView Mark as Final Deliverable (ADR-002 Workstream 1)", () => {
  it("shows 'Mark as Final Deliverable' and calls onSetFinalDeliverable(true) when not yet marked", async () => {
    const user = userEvent.setup();
    const { onSetFinalDeliverable } = renderChatNode({ isFinalDeliverable: false });

    fireEvent.contextMenu(screen.getByText("You"));
    const item = screen.getByRole("menuitem", { name: "Mark as Final Deliverable" });
    await user.click(item);

    expect(onSetFinalDeliverable).toHaveBeenCalledWith(true);
  });

  it("shows 'Unmark Final Deliverable' and calls onSetFinalDeliverable(false) when already marked", async () => {
    const user = userEvent.setup();
    const { onSetFinalDeliverable } = renderChatNode({ isFinalDeliverable: true });

    fireEvent.contextMenu(screen.getByText("You"));
    const item = screen.getByRole("menuitem", { name: "Unmark Final Deliverable" });
    await user.click(item);

    expect(onSetFinalDeliverable).toHaveBeenCalledWith(false);
  });
});

describe("ChatNodeView Collapse Branch (ADR-002 Workstream 1)", () => {
  it("shows 'Collapse Branch' and calls onCollapseBranch(true) when not collapsed", async () => {
    const user = userEvent.setup();
    const { onCollapseBranch } = renderChatNode({ isCollapsed: false });

    fireEvent.contextMenu(screen.getByText("You"));
    const item = screen.getByRole("menuitem", { name: "Collapse Branch" });
    await user.click(item);

    expect(onCollapseBranch).toHaveBeenCalledWith(true);
  });

  it("shows 'Expand Branch' and calls onCollapseBranch(false) when already collapsed", async () => {
    const user = userEvent.setup();
    const { onCollapseBranch } = renderChatNode({ isCollapsed: true });

    fireEvent.contextMenu(screen.getByText("You"));
    const item = screen.getByRole("menuitem", { name: "Expand Branch" });
    await user.click(item);

    expect(onCollapseBranch).toHaveBeenCalledWith(false);
  });
});

// Node redesign, stage 3 ("card chrome"): the avatar chip and the header's
// hover-revealed quick-action row (Copy/Branch from Here/Open Document
// View) - all 3 buttons deliberately call the SAME handlers the card menu
// below already uses, just reached without a right-click first.
describe("ChatNodeView Stage 3 card chrome: avatar + quick actions", () => {
  it("renders the avatar chip with 'U' for a user node and 'A' for an assistant node, both aria-hidden", () => {
    const { container: userContainer } = renderChatNode({ isUser: true });
    const userAvatar = userContainer.querySelector(".chat-node-avatar");
    expect(userAvatar).toHaveTextContent("U");
    expect(userAvatar).toHaveAttribute("aria-hidden", "true");

    const { container: assistantContainer } = renderChatNode({ isUser: false });
    expect(assistantContainer.querySelector(".chat-node-avatar")).toHaveTextContent("A");
  });

  it("the quick-action Copy button copies data.content directly (not routed through the card menu) and flashes a check glyph", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderChatNode({ content: "Hello **world**" });

    const copyBtn = screen.getByRole("button", { name: "Copy text" });
    expect(copyBtn.querySelector("path")).toBeNull(); // rest state: the copy glyph is two rects, no path

    await user.click(copyBtn);

    expect(writeText).toHaveBeenCalledWith("Hello **world**");
    await waitFor(() => expect(copyBtn.querySelector("path")).not.toBeNull()); // check glyph flashed
  });

  it("the quick-action Branch from Here button calls onBranchFromHere directly, without opening the card menu", async () => {
    const user = userEvent.setup();
    const { onBranchFromHere } = renderChatNode();

    await user.click(screen.getByRole("button", { name: "Branch from here" }));
    expect(onBranchFromHere).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("the quick-action Open Document View button calls onOpenDocumentView directly, without opening the card menu", async () => {
    const user = userEvent.setup();
    const { onOpenDocumentView } = renderChatNode();

    await user.click(screen.getByRole("button", { name: "Open Document View" }));
    expect(onOpenDocumentView).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("all three quick-action buttons carry the nodrag class", () => {
    renderChatNode();
    for (const name of ["Copy text", "Branch from here", "Open Document View"]) {
      expect(screen.getByRole("button", { name })).toHaveClass("nodrag");
    }
  });

  it("the check glyph reverts back to the copy glyph 1500ms after copying", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderChatNode();

    const copyBtn = screen.getByRole("button", { name: "Copy text" });
    await user.click(copyBtn);
    await waitFor(() => expect(copyBtn.querySelector("path")).not.toBeNull());

    await waitFor(() => expect(copyBtn.querySelector("path")).toBeNull(), { timeout: 2000 });
  });

  it("adds the selected class to the card when the selected prop is true", () => {
    const { container } = renderChatNode({}, true);
    expect(container.querySelector(".chat-node")).toHaveClass("selected");
  });

  // An adversarial review (node redesign stage 3) found that the avatar chip
  // and quick-actions row, stacked with the header's existing conditional
  // badges (model/synthesis/docked/final - which can legitimately all
  // co-occur on one node), overflowed the 290px collapsed pill and pushed
  // the collapse button itself outside .scene-node's overflow:hidden clip,
  // making it unclickable. Both are now suppressed while collapsed.
  describe("suppressed while collapsed (overflow fix)", () => {
    it("hides the avatar chip and the quick-actions row, but keeps the collapse button, when isCollapsed is true", () => {
      const { container } = renderChatNode({ isCollapsed: true, model: "claude-opus-4-1-20250805" });
      expect(container.querySelector(".chat-node-avatar")).toBeNull();
      expect(container.querySelector(".chat-node-quick-actions")).toBeNull();
      expect(screen.getByRole("button", { name: "Expand" })).toBeInTheDocument();
    });

    it("shows the avatar chip and the quick-actions row again once expanded", () => {
      const { container } = renderChatNode({ isCollapsed: false });
      expect(container.querySelector(".chat-node-avatar")).not.toBeNull();
      expect(container.querySelector(".chat-node-quick-actions")).not.toBeNull();
    });
  });
});

// Node redesign, stage 3's own deferred sub-item ("content fade + Show
// more"), implemented later: replaces .chat-node-content's hard 560px
// scroll-only cutoff with a fade + toggle. jsdom never lays out real
// content (scrollHeight is always 0), so these tests override scrollHeight
// directly on the rendered content div, then trigger a fresh measurement by
// re-rendering with a new data.content (the measurement effect's own
// dependency) - the override survives the re-render since React reuses the
// same DOM node.
describe("ChatNodeView content fade + Show more/Show less", () => {
  it("renders no fade or Show more button when content fits within the collapsed cap (jsdom's real, always-0 scrollHeight)", () => {
    const { container } = renderChatNode();
    expect(container.querySelector(".chat-node-content-fade")).toBeNull();
    expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  });

  it("shows a fade and a Show more button once the content's real scrollHeight exceeds the collapsed cap", () => {
    const { container, rerenderWithData } = renderChatNode({ content: "first" });
    const contentEl = container.querySelector(".chat-node-content") as HTMLDivElement;
    Object.defineProperty(contentEl, "scrollHeight", { value: 900, configurable: true });

    rerenderWithData({ content: "second" });

    expect(container.querySelector(".chat-node-content-fade")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Show more" })).toBeInTheDocument();
  });

  it("clicking Show more expands the content and hides the fade; clicking Show less collapses it back", async () => {
    const user = userEvent.setup();
    const { container, rerenderWithData } = renderChatNode({ content: "first" });
    const contentEl = container.querySelector(".chat-node-content") as HTMLDivElement;
    Object.defineProperty(contentEl, "scrollHeight", { value: 900, configurable: true });
    rerenderWithData({ content: "second" });

    await user.click(screen.getByRole("button", { name: "Show more" }));
    expect(contentEl).toHaveClass("expanded");
    expect(container.querySelector(".chat-node-content-fade")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Show less" }));
    expect(contentEl).not.toHaveClass("expanded");
    expect(container.querySelector(".chat-node-content-fade")).not.toBeNull();
  });

  it("the Show more/Show less toggle carries the nodrag class", () => {
    const { container, rerenderWithData } = renderChatNode({ content: "first" });
    const contentEl = container.querySelector(".chat-node-content") as HTMLDivElement;
    Object.defineProperty(contentEl, "scrollHeight", { value: 900, configurable: true });
    rerenderWithData({ content: "second" });

    expect(screen.getByRole("button", { name: "Show more" })).toHaveClass("nodrag");
  });
});

describe("contentExceedsCollapsedHeight", () => {
  it("is false at or under the collapsed cap", () => {
    expect(contentExceedsCollapsedHeight(0)).toBe(false);
    expect(contentExceedsCollapsedHeight(CHAT_CONTENT_COLLAPSED_MAX_HEIGHT)).toBe(false);
  });

  it("is true once over the collapsed cap", () => {
    expect(contentExceedsCollapsedHeight(CHAT_CONTENT_COLLAPSED_MAX_HEIGHT + 1)).toBe(true);
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

describe("ChatNodeView live regenerate streaming (ADR-006 stage 6.4)", () => {
  it("subscribes for pendingRequestId and renders accumulated deltas instead of the persisted content", () => {
    // ADR-011 stage 11.4: non-reset/non-done deltas are throttled to a
    // rAF-scheduled flush rather than applied synchronously - advance past
    // one frame so the fake-timer-backed requestAnimationFrame fires.
    vi.useFakeTimers();
    try {
      const { subscribeStream, listeners } = makeSubscribeStreamMock();
      renderChatNode({ content: "old persisted answer", pendingRequestId: "req-1", subscribeStream });
      expect(subscribeStream).toHaveBeenCalledWith("req-1", expect.any(Function));
      expect(screen.queryByText("old persisted answer")).toBeNull();

      const listener = listeners.get("req-1")!;
      act(() => listener("Hello ", false, false, 1));
      act(() => listener("World", false, false, 2));
      act(() => {
        vi.advanceTimersByTime(20);
      });
      expect(screen.getByText("Hello World")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a reset frame clears prior accumulated output before appending", () => {
    const { subscribeStream, listeners } = makeSubscribeStreamMock();
    renderChatNode({ pendingRequestId: "req-1", subscribeStream });
    const listener = listeners.get("req-1")!;

    act(() => listener("stale first attempt", false, false, 1));
    act(() => listener("fresh start", false, true, 2));
    expect(screen.queryByText(/stale first attempt/)).toBeNull();
    expect(screen.getByText("fresh start")).toBeInTheDocument();
  });

  it("falls back to the persisted content with no subscription at all when no regenerate is in flight", () => {
    const { subscribeStream } = makeSubscribeStreamMock();
    renderChatNode({ content: "persisted answer", pendingRequestId: null, subscribeStream });
    expect(subscribeStream).not.toHaveBeenCalled();
    expect(screen.getByText("persisted answer")).toBeInTheDocument();
  });

  it("unsubscribes the prior stream when pendingRequestId changes to a new request", () => {
    const { subscribeStream, unsubscribe } = makeSubscribeStreamMock();
    const { rerenderWithData } = renderChatNode({ pendingRequestId: "req-1", subscribeStream });
    rerenderWithData({ pendingRequestId: "req-2", subscribeStream });
    expect(unsubscribe).toHaveBeenCalled();
    expect(subscribeStream).toHaveBeenCalledWith("req-2", expect.any(Function));
  });

  it("shows a waiting placeholder (not a blank body) before the first delta arrives", () => {
    // ADR-011 stage 11.4: same rAF-throttled flush as above - the very
    // first delta is no exception, so the placeholder only yields once that
    // flush actually applies it to state.
    vi.useFakeTimers();
    try {
      const { subscribeStream, listeners } = makeSubscribeStreamMock();
      renderChatNode({ content: "old persisted answer", pendingRequestId: "req-1", subscribeStream });
      expect(screen.getByText("Waiting for response…")).toBeInTheDocument();
      expect(screen.queryByText("old persisted answer")).toBeNull();

      act(() => listeners.get("req-1")!("first token", false, false, 1));
      act(() => {
        vi.advanceTimersByTime(20);
      });
      expect(screen.queryByText("Waiting for response…")).toBeNull();
      expect(screen.getByText("first token")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders a Stop button only while a regenerate is in flight, firing onCancelRegenerate", async () => {
    const user = userEvent.setup();
    const { onCancelRegenerate } = renderChatNode({
      pendingRequestId: "req-1",
      subscribeStream: vi.fn().mockReturnValue(vi.fn()),
    });
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(onCancelRegenerate).toHaveBeenCalledOnce();
  });

  it("renders no Stop button when no regenerate is in flight", () => {
    renderChatNode({ pendingRequestId: null });
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });
});

// ADR-011 review-fix (HIGH): onlyRenderVisibleElements virtualization
// (SceneCanvas.tsx) genuinely UNMOUNTS a live-streaming node's component when
// it's panned off-screen, then mounts a BRAND NEW instance when panned back.
// streamedContent used to be plain component-local state seeded from "" with
// no fallback to any accumulated-so-far value, so every delta broadcast
// during the unmounted window was silently lost forever - the real fix lives
// in transport.ts's subscribeStream (a client-side replay buffer, since the
// server has no subscribe concept of its own to replay from), exercised here
// through ChatNodeView exactly as production wires it: sceneStore.
// subscribeStream is a pure passthrough to WsTransport.subscribeStream (see
// sceneStore.ts's own comment on that method), so this uses the real
// WsTransport class - not a hand-rolled listener-map stub like
// makeSubscribeStreamMock above - so the test fails if the fix regresses at
// either layer.
class FakeStreamSocket {
  static instances: FakeStreamSocket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(public url: string) {
    FakeStreamSocket.instances.push(this);
  }
  send() {}
  close() {
    this.onclose?.();
  }
  open() {
    this.onopen?.();
  }
  receive(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

describe("ChatNodeView live streaming survives virtualization unmount/remount (ADR-011 review-fix)", () => {
  function streamingProps(subscribeStream: ChatFlowNode["data"]["subscribeStream"]): NodeProps<ChatFlowNode> {
    return {
      id: "n0",
      selected: false,
      data: {
        content: "old persisted answer",
        isUser: false,
        isCollapsed: false,
        dockedChildren: [],
        chatScrollValue: 0,
        onToggleCollapse: vi.fn(),
        onDelete: vi.fn(),
        onUndockChild: vi.fn(),
        onRegenerate: vi.fn(),
        onGenerateImage: vi.fn(),
        onGenerateChart: vi.fn(),
        onGenerateKeyTakeaway: vi.fn(),
        onGenerateExplainerNote: vi.fn(),
        onOpenDocumentView: vi.fn(),
        onScrollChange: vi.fn(),
        isBranchFocusActive: false,
        onToggleBranchFocus: vi.fn(),
        onBranchFromHere: vi.fn(),
        branchStatus: "active",
        isFinalDeliverable: false,
        onSetBranchStatus: vi.fn(),
        onSetFinalDeliverable: vi.fn(),
        onCollapseBranch: vi.fn(),
        pendingRequestId: "req-1",
        responseIncomplete: false,
        subscribeStream,
        onCancelRegenerate: vi.fn(),
        toolInvocations: [],
        promptTokens: null,
        completionTokens: null,
        estimatedCostUsd: null,
        provider: null,
        model: null,
        isBranchSynthesis: false,
        synthesisInstructions: "",
        synthesisSourceNodeIds: [],
      },
    } as unknown as NodeProps<ChatFlowNode>;
  }

  it("a brand-new mounted instance recovers everything streamed while the previous instance was unmounted, through the real WsTransport", () => {
    vi.useFakeTimers();
    try {
      FakeStreamSocket.instances = [];
      const transport = new WsTransport("ws://test/ws", {
        webSocketFactory: (url) => new FakeStreamSocket(url),
      });
      transport.connect();
      const socket = FakeStreamSocket.instances[0];
      socket.open();
      const subscribeStream = transport.subscribeStream.bind(transport);

      // Mount #1 - the node is on-screen and a regenerate is streaming in.
      const { unmount } = render(
        <ReactFlowProvider>
          <ChatNodeView {...streamingProps(subscribeStream)} />
        </ReactFlowProvider>,
      );
      act(() => {
        socket.receive({ kind: "stream", topic: "chat", requestId: "req-1", seq: 0, delta: "Hello ", done: false, reset: false });
      });
      act(() => {
        vi.advanceTimersByTime(20);
      });
      expect(screen.getByText("Hello")).toBeInTheDocument();

      // The user pans the node off-screen: React Flow's virtualization
      // genuinely unmounts this component instance (its effect cleanup
      // unsubscribes from the stream).
      unmount();

      // More of the response streams in while the node is off-screen - the
      // exact window this fix exists for.
      act(() => {
        socket.receive({ kind: "stream", topic: "chat", requestId: "req-1", seq: 1, delta: "there, ", done: false, reset: false });
        socket.receive({ kind: "stream", topic: "chat", requestId: "req-1", seq: 2, delta: "friend!", done: false, reset: false });
      });

      // The user pans back: a BRAND NEW component instance mounts, with
      // fresh component-local state (streamedContent re-seeded from "").
      render(
        <ReactFlowProvider>
          <ChatNodeView {...streamingProps(subscribeStream)} />
        </ReactFlowProvider>,
      );

      // It must show the FULL text streamed so far - not an empty "Waiting
      // for response…" placeholder, and not a response that resumes
      // mid-sentence missing what streamed while off-screen.
      expect(screen.queryByText("Waiting for response…")).toBeNull();
      expect(screen.getByText("Hello there, friend!")).toBeInTheDocument();

      // And it keeps receiving live deltas normally afterward.
      act(() => {
        socket.receive({ kind: "stream", topic: "chat", requestId: "req-1", seq: 3, delta: " More.", done: true, reset: false });
      });
      expect(screen.getByText("Hello there, friend! More.")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("ChatNodeView interrupted banner (ADR-006 stage 6.4)", () => {
  it("renders the banner when responseIncomplete is true, with an inline Regenerate firing the existing intent callback", async () => {
    const user = userEvent.setup();
    const { onRegenerate } = renderChatNode({ isUser: false, responseIncomplete: true });
    expect(screen.getByText("Response interrupted — use Regenerate to retry.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(onRegenerate).toHaveBeenCalledOnce();
  });

  it("does not render the banner when responseIncomplete is false", () => {
    renderChatNode({ responseIncomplete: false });
    expect(screen.queryByText(/Response interrupted/)).toBeNull();
  });

  it("suppresses the banner while a new regenerate is already in flight (the live stream IS the retry)", () => {
    renderChatNode({
      isUser: false,
      responseIncomplete: true,
      pendingRequestId: "req-1",
      subscribeStream: vi.fn().mockReturnValue(vi.fn()),
    });
    expect(screen.queryByText(/Response interrupted/)).toBeNull();
  });
});

describe("ChatNodeView tool invocations (ADR-007 stage 7.4)", () => {
  it("renders nothing when the turn made no tool calls (the overwhelming majority of chat nodes)", () => {
    renderChatNode({ toolInvocations: [] });
    expect(screen.queryByText(/tool call/)).toBeNull();
  });

  it("renders a collapsible summary, closed by default, with each call's name/arguments/result inside", async () => {
    const user = userEvent.setup();
    const { container } = renderChatNode({
      isUser: false,
      toolInvocations: [
        {
          id: "call_1",
          name: "echo",
          argumentsJson: '{"message": "hi"}',
          result: "hi",
          isError: false,
        },
      ],
    });
    const summary = screen.getByText("1 tool call");
    const details = container.querySelector("details.chat-node-tool-invocations") as HTMLDetailsElement;
    expect(details.open).toBe(false); // native <details> closed by default

    await user.click(summary);
    expect(details.open).toBe(true);
    expect(screen.getByText("echo")).toBeInTheDocument();
    expect(screen.getByText('{"message": "hi"}')).toBeInTheDocument();
    expect(screen.getByText("hi")).toBeInTheDocument();
  });

  it("pluralizes the summary count for more than one call", () => {
    renderChatNode({
      isUser: false,
      toolInvocations: [
        { id: "call_1", name: "echo", argumentsJson: "{}", result: "one", isError: false },
        { id: "call_2", name: "echo", argumentsJson: "{}", result: "two", isError: false },
      ],
    });
    expect(screen.getByText("2 tool calls")).toBeInTheDocument();
  });

  it("marks a failed call visually distinct from a successful one", async () => {
    const user = userEvent.setup();
    const { container } = renderChatNode({
      isUser: false,
      toolInvocations: [
        { id: "call_1", name: "broken_tool", argumentsJson: "{}", result: "boom", isError: true },
      ],
    });
    await user.click(screen.getByText("1 tool call"));
    expect(container.querySelector(".chat-node-tool-invocation.error")).not.toBeNull();
  });
});

describe("ChatNodeView usage badge (ADR-016 stage 16.2)", () => {
  it("renders nothing when neither promptTokens nor completionTokens is present", () => {
    const { container } = renderChatNode({ promptTokens: null, completionTokens: null });
    expect(container.querySelector(".chat-node-usage-badge")).toBeNull();
  });

  it("renders prompt/completion counts and the estimated cost when present", () => {
    renderChatNode({ promptTokens: 111, completionTokens: 22, estimatedCostUsd: 0.0042 });
    expect(screen.getByText("111→22 · $0.0042")).toBeInTheDocument();
  });

  it("renders the token counts without a cost suffix when estimatedCostUsd is null (unpriced model)", () => {
    renderChatNode({ promptTokens: 111, completionTokens: 22, estimatedCostUsd: null });
    expect(screen.getByText("111→22")).toBeInTheDocument();
  });

  it("still renders when only one of promptTokens/completionTokens is present", () => {
    renderChatNode({ promptTokens: 50, completionTokens: null, estimatedCostUsd: null });
    expect(screen.getByText("50→?")).toBeInTheDocument();
  });
});

// ADR-011 stage 11.1: the React.memo comparator. Direct unit tests of the
// exported pure function (the same function reference wired into
// `memo(ChatNodeView, chatNodePropsAreEqual)`) plus one real-render
// integration test proving the wiring itself is correct.
describe("ChatNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  function chatBaseData(overrides: Partial<ChatFlowNode["data"]> = {}): ChatFlowNode["data"] {
    return {
      content: "Hello",
      isUser: true,
      isCollapsed: false,
      dockedChildren: [],
      chatScrollValue: 0,
      onToggleCollapse: vi.fn(),
      onDelete: vi.fn(),
      onUndockChild: vi.fn(),
      onRegenerate: vi.fn(),
      onGenerateImage: vi.fn(),
      onGenerateChart: vi.fn(),
      onGenerateKeyTakeaway: vi.fn(),
      onGenerateExplainerNote: vi.fn(),
      onOpenDocumentView: vi.fn(),
      onScrollChange: vi.fn(),
      isBranchFocusActive: false,
      onToggleBranchFocus: vi.fn(),
      onBranchFromHere: vi.fn(),
      branchStatus: "active",
      isFinalDeliverable: false,
      onSetBranchStatus: vi.fn(),
      onSetFinalDeliverable: vi.fn(),
      onCollapseBranch: vi.fn(),
      pendingRequestId: null,
      responseIncomplete: false,
      subscribeStream: vi.fn().mockReturnValue(vi.fn()),
      onCancelRegenerate: vi.fn(),
      toolInvocations: [],
      promptTokens: null,
      completionTokens: null,
      estimatedCostUsd: null,
      provider: null,
      model: null,
      overrideProvider: "",
      overrideModelId: "",
      onPinToCurrentModel: vi.fn(),
      onClearModelOverride: vi.fn(),
      isBranchSynthesis: false,
      synthesisInstructions: "",
      synthesisSourceNodeIds: [],
      ...overrides,
    };
  }

  function props(overrides: Partial<ChatFlowNode["data"]> = {}, propOverrides: Record<string, unknown> = {}) {
    return {
      id: "n0",
      selected: false,
      data: chatBaseData(overrides),
      ...propOverrides,
    } as unknown as NodeProps<ChatFlowNode>;
  }

  it("treats identical props as equal", () => {
    const p = props();
    expect(chatNodePropsAreEqual(p, { ...p })).toBe(true);
  });

  it("is unaffected by chatScrollValue (read only once, on mount) or unread NodeProps fields (dragging, zIndex)", () => {
    const a = props({ chatScrollValue: 0 }, { dragging: false, zIndex: 0 });
    const b = { ...a, data: { ...a.data, chatScrollValue: 900 }, dragging: true, zIndex: 9 };
    expect(chatNodePropsAreEqual(a, b)).toBe(true);
  });

  it("returns false when id changes", () => {
    const a = props({}, { id: "n0" });
    const b = { ...a, id: "n1" };
    expect(chatNodePropsAreEqual(a, b)).toBe(false);
  });

  it("returns false when selected changes", () => {
    const a = props({}, { selected: false });
    const b = { ...a, selected: true };
    expect(chatNodePropsAreEqual(a, b)).toBe(false);
  });

  it.each([
    ["content", { content: "changed" }],
    ["isUser", { isUser: false }],
    ["isCollapsed", { isCollapsed: true }],
    ["isBranchFocusActive", { isBranchFocusActive: true }],
    ["branchStatus", { branchStatus: "accepted" }],
    ["isFinalDeliverable", { isFinalDeliverable: true }],
    ["provider", { provider: "anthropic" }],
    ["model", { model: "claude-opus-4-1" }],
    ["isBranchSynthesis", { isBranchSynthesis: true }],
    ["synthesisInstructions", { synthesisInstructions: "merge these" }],
    ["pendingRequestId", { pendingRequestId: "req-1" }],
    ["responseIncomplete", { responseIncomplete: true }],
    ["promptTokens", { promptTokens: 10 }],
    ["completionTokens", { completionTokens: 10 }],
    ["estimatedCostUsd", { estimatedCostUsd: 0.01 }],
    ["onToggleCollapse", { onToggleCollapse: vi.fn() }],
    ["onDelete", { onDelete: vi.fn() }],
    ["onUndockChild", { onUndockChild: vi.fn() }],
    ["onRegenerate", { onRegenerate: vi.fn() }],
    ["onGenerateImage", { onGenerateImage: vi.fn() }],
    ["onGenerateChart", { onGenerateChart: vi.fn() }],
    ["onGenerateKeyTakeaway", { onGenerateKeyTakeaway: vi.fn() }],
    ["onGenerateExplainerNote", { onGenerateExplainerNote: vi.fn() }],
    ["onOpenDocumentView", { onOpenDocumentView: vi.fn() }],
    ["onScrollChange", { onScrollChange: vi.fn() }],
    ["onToggleBranchFocus", { onToggleBranchFocus: vi.fn() }],
    ["onBranchFromHere", { onBranchFromHere: vi.fn() }],
    ["onSetBranchStatus", { onSetBranchStatus: vi.fn() }],
    ["onSetFinalDeliverable", { onSetFinalDeliverable: vi.fn() }],
    ["onCollapseBranch", { onCollapseBranch: vi.fn() }],
    ["onCancelRegenerate", { onCancelRegenerate: vi.fn() }],
    ["subscribeStream", { subscribeStream: vi.fn() }],
  ] as const)("returns false when data.%s changes and nothing else does", (_name, override) => {
    const a = props();
    const b = { ...a, data: { ...a.data, ...override } };
    expect(chatNodePropsAreEqual(a, b)).toBe(false);
  });

  describe("array-shaped fields get an element-by-element compare, not a bare reference check", () => {
    it("dockedChildren: fresh-but-identical array is equal; differing contents or length are not", () => {
      const a = props({ dockedChildren: [{ id: "d1", label: "Thinking" }] });
      const bEqual = { ...a, data: { ...a.data, dockedChildren: [{ id: "d1", label: "Thinking" }] } };
      expect(a.data.dockedChildren).not.toBe(bEqual.data.dockedChildren);
      expect(chatNodePropsAreEqual(a, bEqual)).toBe(true);

      const bContentDiff = { ...a, data: { ...a.data, dockedChildren: [{ id: "d1", label: "Different" }] } };
      expect(chatNodePropsAreEqual(a, bContentDiff)).toBe(false);

      const bLengthDiff = {
        ...a,
        data: { ...a.data, dockedChildren: [{ id: "d1", label: "Thinking" }, { id: "d2", label: "Other" }] },
      };
      expect(chatNodePropsAreEqual(a, bLengthDiff)).toBe(false);
    });

    it("toolInvocations: fresh-but-identical array is equal; differing contents or length are not", () => {
      const invocation = { id: "call_1", name: "search", argumentsJson: "{}", result: "ok", isError: false };
      const a = props({ toolInvocations: [invocation] });
      const bEqual = { ...a, data: { ...a.data, toolInvocations: [{ ...invocation }] } };
      expect(a.data.toolInvocations).not.toBe(bEqual.data.toolInvocations);
      expect(chatNodePropsAreEqual(a, bEqual)).toBe(true);

      const bContentDiff = { ...a, data: { ...a.data, toolInvocations: [{ ...invocation, isError: true }] } };
      expect(chatNodePropsAreEqual(a, bContentDiff)).toBe(false);

      const bLengthDiff = { ...a, data: { ...a.data, toolInvocations: [invocation, invocation] } };
      expect(chatNodePropsAreEqual(a, bLengthDiff)).toBe(false);
    });

    it("synthesisSourceNodeIds: fresh-but-identical array is equal; differing contents or length are not", () => {
      const a = props({ synthesisSourceNodeIds: ["n1", "n2"] });
      const bEqual = { ...a, data: { ...a.data, synthesisSourceNodeIds: ["n1", "n2"] } };
      expect(a.data.synthesisSourceNodeIds).not.toBe(bEqual.data.synthesisSourceNodeIds);
      expect(chatNodePropsAreEqual(a, bEqual)).toBe(true);

      const bContentDiff = { ...a, data: { ...a.data, synthesisSourceNodeIds: ["n1", "n3"] } };
      expect(chatNodePropsAreEqual(a, bContentDiff)).toBe(false);

      const bLengthDiff = { ...a, data: { ...a.data, synthesisSourceNodeIds: ["n1", "n2", "n3"] } };
      expect(chatNodePropsAreEqual(a, bLengthDiff)).toBe(false);
    });
  });

  it("real render: skipped when only an unread field (chatScrollValue) changes", () => {
    const p = props({ content: "v1" }, { selected: false });
    const { container, rerender } = render(
      <ReactFlowProvider>
        <ChatNodeView {...p} />
      </ReactFlowProvider>,
    );
    const root = container.querySelector(".scene-node") as HTMLElement;
    expect(root).not.toBeNull();

    root.className = "CORRUPTED";

    // chatScrollValue is read only inside the mount-only scroll-restore
    // effect - the comparator must say "equal", so no re-render should
    // occur here.
    rerender(
      <ReactFlowProvider>
        <ChatNodeView {...p} data={{ ...p.data, chatScrollValue: 500 }} />
      </ReactFlowProvider>,
    );
    expect(root.className).toBe("CORRUPTED");
  });

  // Review-fix: this test's own title used to claim "...and actually happens
  // when content changes" while its body only ever changed `selected` - a
  // real re-render, but not the one the title advertised. content DOES flow
  // through the mounted component here (unlike chatNodePropsAreEqual's own
  // pure-function it.each table above, which calls the comparator directly
  // and never mounts anything), closing the gap between "the comparator
  // returns false for a content diff" (proven above) and "a live component
  // actually re-renders when content changes" (proven here).
  it("real render: actually happens when content changes", () => {
    // Deliberately NOT the className-corruption trick the two tests above
    // use (mutate the DOM directly, then check a real re-render overwrites
    // it): that only proves something when the changed prop actually feeds
    // the className expression (selected/collapsed do; content does not),
    // so it would pass here even with NO re-render at all - React skips
    // reassigning a DOM attribute whose newly-computed value is unchanged
    // from the fiber's last-rendered value, corrupted or not. Asserting on
    // the rendered TEXT itself is the direct, unambiguous proof that a real
    // re-render happened with the new content.
    const p = props({ content: "v1" }, { selected: false });
    const { rerender } = render(
      <ReactFlowProvider>
        <ChatNodeView {...p} />
      </ReactFlowProvider>,
    );
    expect(screen.getByText("v1")).toBeInTheDocument();

    rerender(
      <ReactFlowProvider>
        <ChatNodeView {...p} data={{ ...p.data, content: "v2" }} />
      </ReactFlowProvider>,
    );
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.queryByText("v1")).toBeNull();
  });

  it("real render: also happens when selected changes", () => {
    const p = props({ content: "v1" }, { selected: false });
    const { container, rerender } = render(
      <ReactFlowProvider>
        <ChatNodeView {...p} />
      </ReactFlowProvider>,
    );
    const root = container.querySelector(".scene-node") as HTMLElement;
    root.className = "CORRUPTED";

    rerender(
      <ReactFlowProvider>
        <ChatNodeView {...p} selected />
      </ReactFlowProvider>,
    );
    expect(root.className).not.toBe("CORRUPTED");
    expect(root.className).toContain("selected");
  });
});

// ADR-011 stage 11.4: throttled in-node streaming markdown parse.
describe("ChatNodeView throttled streaming markdown parse (ADR-011 stage 11.4)", () => {
  it("coalesces a rapid burst of deltas into fewer markdown re-parses than the delta count, with the final text byte-identical to the un-throttled concatenation of every delta in order", () => {
    vi.useFakeTimers();
    try {
      const { subscribeStream, listeners } = makeSubscribeStreamMock();
      renderChatNode({ pendingRequestId: "req-1", subscribeStream });
      const listener = listeners.get("req-1")!;

      const NodeMarkdownMock = NodeMarkdown as unknown as ReturnType<typeof vi.fn>;
      const callsBeforeBurst = NodeMarkdownMock.mock.calls.length;

      // Leading (not trailing) space on every word after the first, so the
      // final text has no trailing whitespace for the markdown paragraph
      // renderer to trim - that trimming is a pre-existing, throttle-
      // unrelated quirk of how remark serializes paragraph text, and
      // stripping trailing spaces here keeps this test's "byte-identical"
      // claim tied to the actual property under test (delta order/
      // completeness) rather than an unrelated whitespace nuance.
      const deltaCount = 50;
      let expectedFullText = "";
      act(() => {
        for (let i = 0; i < deltaCount; i++) {
          const delta = i === 0 ? `w${i}` : ` w${i}`;
          expectedFullText += delta;
          listener(delta, false, false, i + 1);
        }
      });

      // The entire burst landed inside one synchronous batch, before the
      // rAF-scheduled flush below ever fires - not one delta has re-parsed
      // yet (proves accumulation happens in a ref, not via setState per
      // delta).
      expect(NodeMarkdownMock.mock.calls.length).toBe(callsBeforeBurst);
      expect(screen.queryByText(/w0/)).toBeNull();

      act(() => {
        vi.advanceTimersByTime(20); // flushes the one scheduled rAF callback
      });

      const callsAfterFlush = NodeMarkdownMock.mock.calls.length;
      const reparseCount = callsAfterFlush - callsBeforeBurst;
      expect(reparseCount).toBeGreaterThan(0); // it did eventually flush...
      expect(reparseCount).toBeLessThan(deltaCount); // ...far fewer times than the delta count

      // No byte dropped or reordered: every delta, in order, still lands in
      // the rendered content exactly.
      const contentEl = document.querySelector(".chat-node-content");
      expect(contentEl?.textContent).toBe(expectedFullText);
    } finally {
      vi.useRealTimers();
    }
  });

  it("flushes the final chunk immediately on stream completion (done=true) rather than waiting for the next frame", () => {
    vi.useFakeTimers();
    try {
      const { subscribeStream, listeners } = makeSubscribeStreamMock();
      renderChatNode({ pendingRequestId: "req-1", subscribeStream });
      const listener = listeners.get("req-1")!;

      act(() => {
        listener("partial ", false, false, 1);
        listener("final chunk", true, false, 2); // done=true
      });

      // No vi.advanceTimersByTime() call - completion must flush
      // synchronously, not on the next rAF frame.
      expect(screen.getByText("partial final chunk")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("never strands a trailing buffered chunk when pendingRequestId changes away mid-stream", () => {
    vi.useFakeTimers();
    try {
      const { subscribeStream, listeners } = makeSubscribeStreamMock();
      const { rerenderWithData } = renderChatNode({ pendingRequestId: "req-1", subscribeStream });
      const listener = listeners.get("req-1")!;

      act(() => {
        listener("buffered but not yet flushed", false, false, 1);
      });
      // Still un-flushed (no timer advance) - about to switch away from this
      // request entirely.
      rerenderWithData({ pendingRequestId: null, content: "final persisted answer" });

      // The unsubscribe cleanup's own flush (and the render-time subscribed-
      // request reset) leaves nothing stuck mid-flight - the node falls back
      // cleanly to the persisted content, not a half-applied streamed value.
      expect(screen.getByText("final persisted answer")).toBeInTheDocument();
      expect(screen.queryByText(/buffered but not yet flushed/)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
