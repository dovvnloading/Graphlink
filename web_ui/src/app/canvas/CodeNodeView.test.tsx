import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CodeNodeView, type CodeFlowNode } from "./CodeNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderCodeNode(overrides: Partial<CodeFlowNode["data"]> = {}) {
  const onDelete = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      code: "def add(a, b):\n    return a + b",
      language: "python",
      onDelete,
      ...overrides,
    },
  } as unknown as NodeProps<CodeFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <CodeNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onDelete, container };
}

describe("CodeNodeView", () => {
  it("renders the language label and syntax-highlighted code content", () => {
    const { container } = renderCodeNode();
    expect(screen.getByText("python")).toBeInTheDocument();
    // Proves the fenced-code-block-through-ReactMarkdown+rehype-highlight
    // pipeline actually ran (not just plain-text rendering of the raw code).
    expect(container.querySelector(".hljs")).not.toBeNull();
    expect(container.textContent).toContain("return a + b");
  });

  it("falls back to the word 'code' when language is empty", () => {
    renderCodeNode({ language: "" });
    expect(screen.getByText("code")).toBeInTheDocument();
  });

  it("right-click opens a menu with real Copy Code/Delete Code Block and deferred Regenerate/Export", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderCodeNode();

    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const label = screen.getByText("python");
    fireEvent.contextMenu(label);
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

    await user.click(screen.getByRole("menuitem", { name: "Copy Code" }));
    expect(writeText).toHaveBeenCalledWith("def add(a, b):\n    return a + b");

    fireEvent.contextMenu(label);
    await user.click(screen.getByRole("menuitem", { name: "Delete Code Block" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("Escape and outside-click both close the menu", async () => {
    const user = userEvent.setup();
    renderCodeNode();
    const label = screen.getByText("python");

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
