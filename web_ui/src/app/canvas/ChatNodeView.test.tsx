import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatNodeView, type ChatFlowNode } from "./ChatNodeView";

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
  const props = {
    id: "n0",
    selected: false,
    data: {
      content: "Hello **world**",
      isUser: true,
      isCollapsed: false,
      onToggleCollapse,
      onDelete,
      ...overrides,
    },
  } as unknown as NodeProps<ChatFlowNode>;

  render(
    <ReactFlowProvider>
      <ChatNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onToggleCollapse, onDelete };
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

    const regenerate = screen.getByRole("menuitem", { name: "Regenerate Response" });
    expect(regenerate).toBeDisabled();
    expect(regenerate).toHaveAttribute("title", "Agent regeneration lands in R4");
    const exportItem = screen.getByRole("menuitem", { name: "Export" });
    expect(exportItem).toBeDisabled();
    expect(exportItem).toHaveAttribute("title", "Export lands in R6");
    const hideBranches = screen.getByRole("menuitem", { name: "Hide Other Branches" });
    expect(hideBranches).toBeDisabled();
    expect(hideBranches).toHaveAttribute("title", "Branch visibility isn't built yet");
    const docView = screen.getByRole("menuitem", { name: "Open Document View" });
    expect(docView).toBeDisabled();
    for (const name of ["Generate Key Takeaway", "Generate Explainer Note", "Generate Chart", "Generate Image"]) {
      const item = screen.getByRole("menuitem", { name });
      expect(item).toBeDisabled();
      expect(item).toHaveAttribute("title", "AI generation lands in R4");
    }

    await user.click(screen.getByRole("menuitem", { name: "Copy Text" }));
    expect(writeText).toHaveBeenCalledWith("Hello **world**");

    fireEvent.contextMenu(role);
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("Regenerate Response only appears for assistant messages, matching the legacy is_user guard", () => {
    renderChatNode({ isUser: true });
    fireEvent.contextMenu(screen.getByText("You"));
    expect(screen.queryByRole("menuitem", { name: "Regenerate Response" })).toBeNull();
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
});
