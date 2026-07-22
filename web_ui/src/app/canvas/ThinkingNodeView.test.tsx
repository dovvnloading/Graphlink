import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ThinkingNodeView, type ThinkingFlowNode } from "./ThinkingNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderThinkingNode(overrides: Partial<ThinkingFlowNode["data"]> = {}) {
  const onDock = vi.fn();
  const onDelete = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      thinkingText: "Considering **several** approaches before answering.",
      onDock,
      onDelete,
      ...overrides,
    },
  } as unknown as NodeProps<ThinkingFlowNode>;

  render(
    <ReactFlowProvider>
      <ThinkingNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onDock, onDelete };
}

describe("ThinkingNodeView", () => {
  it("renders the markdown thinking text", () => {
    renderThinkingNode();
    expect(screen.getByText("several")).toBeInTheDocument(); // bold text still renders as text
  });

  it("right-click opens a menu with real Copy Content/Dock to Parent Node/Delete Node and honest disabled Hide Other Branches", async () => {
    const user = userEvent.setup();
    const { onDock, onDelete } = renderThinkingNode();

    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const label = screen.getByText("Thinking");
    fireEvent.contextMenu(label);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    const copyItem = screen.getByRole("menuitem", { name: "Copy Content" });
    const dockItem = screen.getByRole("menuitem", { name: "Dock to Parent Node" });
    const hideBranches = screen.getByRole("menuitem", { name: "Hide Other Branches" });
    const deleteItem = screen.getByRole("menuitem", { name: "Delete Node" });
    expect(copyItem).toBeEnabled();
    expect(dockItem).toBeEnabled();
    expect(deleteItem).toBeEnabled();
    expect(hideBranches).toBeDisabled();
    expect(hideBranches).toHaveAttribute("title", "Branch visibility isn't built yet");

    await user.click(copyItem);
    expect(writeText).toHaveBeenCalledWith("Considering **several** approaches before answering.");

    fireEvent.contextMenu(label);
    await user.click(screen.getByRole("menuitem", { name: "Dock to Parent Node" }));
    expect(onDock).toHaveBeenCalledOnce();

    fireEvent.contextMenu(label);
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("Escape and outside-click both close the menu", async () => {
    const user = userEvent.setup();
    renderThinkingNode();
    const label = screen.getByText("Thinking");

    fireEvent.contextMenu(label);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.contextMenu(label);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
