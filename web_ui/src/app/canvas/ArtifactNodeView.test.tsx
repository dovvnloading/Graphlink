import { ReactFlowProvider, useStoreApi, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { ArtifactNodeView, artifactNodePropsAreEqual, type ArtifactFlowNode } from "./ArtifactNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx / ConversationNodeView.test.tsx / WebResearchNodeView.test.tsx
// for why a bare ReactFlowProvider is enough here too.

function baseData(overrides: Partial<ArtifactFlowNode["data"]> = {}): ArtifactFlowNode["data"] {
  return {
    artifactContent: "",
    artifactError: "",
    history: [],
    isCollapsed: false,
    pendingRequestId: null,
    onToggleCollapse: vi.fn(),
    onDelete: vi.fn(),
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
}

function renderArtifactNode(overrides: Partial<ArtifactFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "n0", selected: false, data } as unknown as NodeProps<ArtifactFlowNode>;

  render(
    <ReactFlowProvider>
      <ArtifactNodeView {...props} />
    </ReactFlowProvider>,
  );
  return data;
}

// Directly sets the React Flow internal Zustand store's transform/zoom value
// - same technique WebResearchNodeView.test.tsx / ConversationNodeView.test.tsx's
// own ZoomSetter uses (a mounted panZoom instance doesn't exist in this
// direct-render test setup).
function ZoomSetter({ zoom }: { zoom: number }) {
  const store = useStoreApi();
  useEffect(() => {
    store.setState({ transform: [0, 0, zoom] });
  }, [zoom, store]);
  return null;
}

function renderArtifactNodeAtZoom(zoom: number, overrides: Partial<ArtifactFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "n0", selected: false, data } as unknown as NodeProps<ArtifactFlowNode>;

  render(
    <ReactFlowProvider>
      <ZoomSetter zoom={zoom} />
      <ArtifactNodeView {...props} />
    </ReactFlowProvider>,
  );
  return data;
}

describe("ArtifactNodeView", () => {
  // -- document preview -----------------------------------------------------

  it("renders the empty-document placeholder when artifactContent is an empty string", () => {
    renderArtifactNode({ artifactContent: "" });
    expect(screen.getByText("Document is currently empty.")).toBeInTheDocument();
  });

  it("renders real rendered Markdown (heading, bold text, a GFM table) for non-empty artifactContent", () => {
    renderArtifactNode({
      artifactContent:
        "# Project Proposal\n\nThis is **very important**.\n\n| Item | Cost |\n| --- | --- |\n| Widget | $5 |\n",
    });
    expect(screen.queryByText("Document is currently empty.")).toBeNull();
    expect(screen.getByRole("heading", { name: "Project Proposal" })).toBeInTheDocument();
    expect(screen.getByText("very important")).toBeInTheDocument(); // bold text still renders as text
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Widget")).toBeInTheDocument();
  });

  // -- turn history -----------------------------------------------------------

  it("renders one bubble per history entry with the correct user/assistant styling class", () => {
    renderArtifactNode({
      history: [
        { role: "user", content: "Draft a **proposal**" },
        { role: "assistant", content: "Here is a draft." },
      ],
    });
    const userBubble = screen.getByText("proposal").closest(".artifact-node-bubble");
    const assistantBubble = screen.getByText("Here is a draft.").closest(".artifact-node-bubble");
    expect(userBubble).toHaveClass("artifact-node-bubble", "user");
    expect(assistantBubble).toHaveClass("artifact-node-bubble", "assistant");
  });

  it("renders no turn-history section at all when history is empty", () => {
    renderArtifactNode({ history: [] });
    expect(document.querySelector(".artifact-node-messages")).toBeNull();
  });

  // -- collapse/expand + LOD -------------------------------------------------

  it("manual collapse hides the body and shows only the header", () => {
    renderArtifactNode({ isCollapsed: true, artifactContent: "hello" });
    expect(screen.getByText("Artifact")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("the inline collapse chevron calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const data = renderArtifactNode();
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("LOD auto-collapse (zoom below threshold) also hides the body, even when isCollapsed is false", () => {
    renderArtifactNodeAtZoom(0.2, { isCollapsed: false, artifactContent: "hello" });
    expect(screen.getByText("Artifact")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("stays expanded above the LOD threshold when isCollapsed is false", () => {
    renderArtifactNodeAtZoom(1, { isCollapsed: false });
    expect(screen.getByRole("textbox", { name: "Instruction" })).toBeInTheDocument();
  });

  // -- failure banner ---------------------------------------------------------

  it("renders the failure on the card, not just as a session-wide toast", () => {
    // With two artifact nodes on a canvas, a toast cannot say which one
    // failed. Every other async node kind already carries its failure on
    // the node itself.
    renderArtifactNode({ artifactError: "Artifact generation failed: provider exploded" });
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent("Artifact generation failed: provider exploded");
  });

  it("renders no failure banner when there is no error", () => {
    renderArtifactNode({ artifactError: "" });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  // -- submit label + disabled state ------------------------------------------

  it("the submit button reads Generate when artifactContent is empty", () => {
    renderArtifactNode({ artifactContent: "" });
    expect(screen.getByRole("button", { name: "Generate" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Refine" })).toBeNull();
  });

  it("the submit button reads Refine once artifactContent is non-empty", () => {
    renderArtifactNode({ artifactContent: "# Existing draft" });
    expect(screen.getByRole("button", { name: "Refine" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Generate" })).toBeNull();
  });

  it("treats whitespace-only artifactContent the same as empty (Generate, not Refine)", () => {
    renderArtifactNode({ artifactContent: "   \n  " });
    expect(screen.getByRole("button", { name: "Generate" })).toBeInTheDocument();
  });

  it("the submit button is disabled when the draft is empty or whitespace-only", async () => {
    const user = userEvent.setup();
    renderArtifactNode();
    const input = screen.getByRole("textbox", { name: "Instruction" });
    const submitButton = screen.getByRole("button", { name: "Generate" });

    expect(submitButton).toBeDisabled();
    await user.type(input, "   ");
    expect(submitButton).toBeDisabled();
    await user.type(input, "real text");
    expect(submitButton).toBeEnabled();
  });

  it("the submit button is disabled while pendingRequestId is set, even with non-empty draft", async () => {
    const user = userEvent.setup();
    renderArtifactNode({ pendingRequestId: "req-1" });
    const input = screen.getByRole("textbox", { name: "Instruction" });
    await user.type(input, "real text");
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  });

  // -- submit / Enter / Shift+Enter -------------------------------------------

  it("typing text and pressing Enter calls onSubmit with the trimmed text and clears the input", async () => {
    const user = userEvent.setup();
    const data = renderArtifactNode();
    const input = screen.getByRole("textbox", { name: "Instruction" });

    await user.type(input, "  draft a proposal  {Enter}");
    expect(data.onSubmit).toHaveBeenCalledWith("draft a proposal");
    expect(input).toHaveValue("");
  });

  it("Shift+Enter does not submit and instead allows a newline", async () => {
    const user = userEvent.setup();
    const data = renderArtifactNode();
    const input = screen.getByRole("textbox", { name: "Instruction" });

    await user.type(input, "line one{Shift>}{Enter}{/Shift}line two");
    expect(data.onSubmit).not.toHaveBeenCalled();
    expect(input).toHaveValue("line one\nline two");
  });

  it("clicking the submit button calls onSubmit with the trimmed text and clears the input", async () => {
    const user = userEvent.setup();
    const data = renderArtifactNode();
    const input = screen.getByRole("textbox", { name: "Instruction" });

    await user.type(input, "click to submit");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect(data.onSubmit).toHaveBeenCalledWith("click to submit");
    expect(input).toHaveValue("");
  });

  // -- Cancel -----------------------------------------------------------------

  it("the Cancel button is absent when pendingRequestId is null", () => {
    renderArtifactNode({ pendingRequestId: null });
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("the Cancel button is present and calls onCancel when pendingRequestId is set", async () => {
    const user = userEvent.setup();
    const data = renderArtifactNode({ pendingRequestId: "req-42" });
    const cancelButton = screen.getByRole("button", { name: "Cancel" });
    expect(cancelButton).toBeInTheDocument();
    await user.click(cancelButton);
    expect(data.onCancel).toHaveBeenCalledOnce();
  });

  // -- card-level menu ----------------------------------------------------

  it("the node-level right-click menu shows exactly Collapse/Expand + Delete Node - no dock action", async () => {
    const user = userEvent.setup();
    const data = renderArtifactNode();

    fireEvent.contextMenu(screen.getByText("Artifact"));
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Collapse");
    expect(items[1]).toHaveTextContent("Delete Node");
    expect(screen.queryByRole("menuitem", { name: /Dock/ })).toBeNull();

    await user.click(items[0]);
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();

    fireEvent.contextMenu(screen.getByText("Artifact"));
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(data.onDelete).toHaveBeenCalledOnce();
  });

  it("the menu's Collapse/Expand label flips when isCollapsed is true", () => {
    renderArtifactNode({ isCollapsed: true });
    fireEvent.contextMenu(screen.getByText("Artifact"));
    expect(screen.getByRole("menuitem", { name: "Expand" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Collapse" })).toBeNull();
  });

  it("Escape and outside-click both close the node-level menu", async () => {
    const user = userEvent.setup();
    renderArtifactNode();
    const header = screen.getByText("Artifact");

    fireEvent.contextMenu(header);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.contextMenu(header);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  // -- security: no raw-HTML passthrough ---------------------------------

  it("SECURITY: a document string containing a literal <img onerror> tag never becomes a real rendered img element", () => {
    renderArtifactNode({
      artifactContent: 'Look at this: <img src="x" onerror="alert(1)"> nothing happened.',
    });
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("SECURITY: a turn-history entry containing a literal <img onerror> tag never becomes a real rendered img element", () => {
    renderArtifactNode({
      history: [{ role: "assistant", content: '<img src="x" onerror="alert(1)"> as requested.' }],
    });
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });
});

// ADR-011 stage 11.1: the React.memo comparator. Direct unit tests of the
// exported pure function (the same function reference wired straight into
// `memo(ArtifactNodeView, artifactNodePropsAreEqual)`) plus one real-render
// integration test proving the wiring itself is correct - a passing unit
// test of the comparator alone couldn't catch "the comparator is right but
// never actually got passed to memo()".
describe("ArtifactNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  function props(overrides: Partial<ArtifactFlowNode["data"]> = {}, propOverrides: Record<string, unknown> = {}) {
    return {
      id: "n0",
      selected: false,
      data: baseData(overrides),
      ...propOverrides,
    } as unknown as NodeProps<ArtifactFlowNode>;
  }

  it("treats identical props as equal", () => {
    const p = props({ artifactContent: "hello" });
    expect(artifactNodePropsAreEqual(p, { ...p })).toBe(true);
  });

  it("treats a fresh-but-value-identical history array as equal (not a bare reference check)", () => {
    const a = props({ history: [{ role: "user", content: "hi" }] });
    // Same callbacks as `a` (comparator would correctly reject two
    // independently-minted vi.fn() instances) - only history's ARRAY
    // OBJECT differs, its contents do not.
    const b = { ...a, data: { ...a.data, history: [{ role: "user" as const, content: "hi" }] } };
    expect(a.data.history).not.toBe(b.data.history);
    expect(artifactNodePropsAreEqual(a, b)).toBe(true);
  });

  it("is unaffected by NodeProps fields this component never reads (id, dragging, zIndex)", () => {
    const a = props({}, { id: "n0", dragging: false, zIndex: 0 });
    const b = { ...a, id: "n1", dragging: true, zIndex: 7 };
    expect(artifactNodePropsAreEqual(a, b)).toBe(true);
  });

  it("returns false when selected changes", () => {
    const a = props({}, { selected: false });
    const b = props({}, { selected: true });
    expect(artifactNodePropsAreEqual(a, b)).toBe(false);
  });

  it.each([
    ["artifactContent", { artifactContent: "changed" }],
    ["isCollapsed", { isCollapsed: true }],
    ["pendingRequestId", { pendingRequestId: "req-1" }],
    ["onToggleCollapse", { onToggleCollapse: vi.fn() }],
    ["onDelete", { onDelete: vi.fn() }],
    ["onSubmit", { onSubmit: vi.fn() }],
    ["onCancel", { onCancel: vi.fn() }],
  ] as const)("returns false when data.%s changes and nothing else does", (_name, override) => {
    const a = props();
    // Isolate the change to exactly this one field - everything else
    // (including every callback reference) stays byte-for-byte identical,
    // so a false result here can only be attributed to this field.
    const b = { ...a, data: { ...a.data, ...override } };
    expect(artifactNodePropsAreEqual(a, b)).toBe(false);
  });

  it("returns false when history's contents differ, not just its reference", () => {
    const a = props({ history: [{ role: "user", content: "hi" }] });
    const b = { ...a, data: { ...a.data, history: [{ role: "user" as const, content: "bye" }] } };
    expect(artifactNodePropsAreEqual(a, b)).toBe(false);
  });

  it("returns false when history's length differs", () => {
    const a = props({ history: [{ role: "user", content: "hi" }] });
    const b = {
      ...a,
      data: { ...a.data, history: [{ role: "user" as const, content: "hi" }, { role: "assistant" as const, content: "hi" }] },
    };
    expect(artifactNodePropsAreEqual(a, b)).toBe(false);
  });

  it("real render: skipped when only an unread NodeProps field changes, and actually happens when selected changes", () => {
    const p = props({ artifactContent: "hello" }, { selected: false });
    const { container, rerender } = render(
      <ReactFlowProvider>
        <ArtifactNodeView {...p} />
      </ReactFlowProvider>,
    );
    const root = container.querySelector(".scene-node") as HTMLElement;
    expect(root).not.toBeNull();

    // Corrupt the root element's class list directly, bypassing React - if
    // the memoized component's render function is never called again, React
    // never touches this element and the corruption survives.
    root.className = "CORRUPTED";

    // `dragging` is a real NodeProps field this component never reads -
    // the comparator must say "equal" here, so no re-render should occur.
    rerender(
      <ReactFlowProvider>
        <ArtifactNodeView {...p} dragging />
      </ReactFlowProvider>,
    );
    expect(root.className).toBe("CORRUPTED");

    // `selected` actually IS read (it drives the "selected" class) - the
    // comparator must say "not equal" here, forcing a real re-render that
    // recomputes and resets the class.
    rerender(
      <ReactFlowProvider>
        <ArtifactNodeView {...p} selected />
      </ReactFlowProvider>,
    );
    expect(root.className).not.toBe("CORRUPTED");
    expect(root.className).toContain("selected");
  });
});
