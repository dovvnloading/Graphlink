import { ReactFlowProvider, useStoreApi, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { ArtifactNodeView, type ArtifactFlowNode } from "./ArtifactNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx / ConversationNodeView.test.tsx / WebResearchNodeView.test.tsx
// for why a bare ReactFlowProvider is enough here too.

function baseData(overrides: Partial<ArtifactFlowNode["data"]> = {}): ArtifactFlowNode["data"] {
  return {
    artifactContent: "",
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
