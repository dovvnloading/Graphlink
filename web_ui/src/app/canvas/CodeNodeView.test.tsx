import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CodeNodeView, type CodeFlowNode } from "./CodeNodeView";

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

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderCodeNode(overrides: Partial<CodeFlowNode["data"]> = {}) {
  const onDelete = vi.fn();
  const onRegenerate = vi.fn();
  const onToggleBranchFocus = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      code: "def add(a, b):\n    return a + b",
      language: "python",
      parentChatNodeId: "chat-1",
      onRegenerate,
      onDelete,
      isBranchFocusActive: false,
      onToggleBranchFocus,
      ...overrides,
    },
  } as unknown as NodeProps<CodeFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <CodeNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onDelete, onRegenerate, onToggleBranchFocus, container };
}

describe("CodeNodeView", () => {
  // Node redesign stage 1: NodeMarkdown's own code-block language badge now
  // ALSO renders the language string (e.g. "python") inside the code block
  // itself, alongside this view's own title-bar label - both legitimately
  // say "python" at once, so every query below is scoped to
  // .code-node-language (the title bar specifically) rather than a bare
  // screen.getByText, which would now be ambiguous.
  function titleLabel(container: HTMLElement): HTMLElement {
    return container.querySelector(".code-node-language") as HTMLElement;
  }

  it("renders the language label and syntax-highlighted code content", () => {
    const { container } = renderCodeNode();
    expect(titleLabel(container)).toHaveTextContent("python");
    // Proves the fenced-code-block-through-ReactMarkdown+rehype-highlight
    // pipeline actually ran (not just plain-text rendering of the raw code).
    expect(container.querySelector(".hljs")).not.toBeNull();
    expect(container.textContent).toContain("return a + b");
  });

  it("falls back to the word 'code' when language is empty", () => {
    renderCodeNode({ language: "" });
    expect(screen.getByText("code")).toBeInTheDocument();
  });

  it("right-click opens a menu with real Copy Code/Delete Code Block/Export/Hide Other Branches", async () => {
    const user = userEvent.setup();
    const { onDelete, container } = renderCodeNode();

    const writeText = vi.fn();
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    const label = titleLabel(container);
    fireEvent.contextMenu(label);
    expect(screen.getByRole("menu")).toBeInTheDocument();

    expect(screen.getByRole("menuitem", { name: "Export" })).not.toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Hide Other Branches" })).not.toBeDisabled();

    await user.click(screen.getByRole("menuitem", { name: "Copy Code" }));
    expect(writeText).toHaveBeenCalledWith("def add(a, b):\n    return a + b");

    fireEvent.contextMenu(label);
    await user.click(screen.getByRole("menuitem", { name: "Delete Code Block" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("clicking Export downloads the raw code as a language-appropriate file, then closes the menu (R7.5a)", async () => {
    const user = userEvent.setup();
    const { container } = renderCodeNode({ code: "print('hi')", language: "python" });

    const captured: { anchor?: HTMLAnchorElement } = {};
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        captured.anchor = this;
      });

    fireEvent.contextMenu(titleLabel(container));
    await user.click(screen.getByRole("menuitem", { name: "Export" }));

    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    expect(URL.createObjectURL).toHaveBeenCalled();
    const blobArg = (URL.createObjectURL as ReturnType<typeof vi.fn>).mock.calls[0][0] as Blob;
    expect(await blobArg.text()).toBe("print('hi')");
    expect(captured.anchor?.getAttribute("download")).toBe("code-n0.py");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:fake-object-url");
    expect(screen.queryByRole("menu")).toBeNull(); // onClose fires after Export
  });

  it("falls back to a .txt extension for an unrecognized language (R7.5a)", async () => {
    const user = userEvent.setup();
    const { container } = renderCodeNode({ language: "brainfuck" });

    const captured: { anchor?: HTMLAnchorElement } = {};
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      captured.anchor = this;
    });

    fireEvent.contextMenu(titleLabel(container));
    await user.click(screen.getByRole("menuitem", { name: "Export" }));

    await waitFor(() => expect(captured.anchor).toBeDefined());
    expect(captured.anchor?.getAttribute("download")).toBe("code-n0.txt");
  });

  it("Regenerate Response renders enabled and fires onRegenerate then closes the menu when parentChatNodeId is non-null", async () => {
    const user = userEvent.setup();
    const { onRegenerate, container } = renderCodeNode({ parentChatNodeId: "chat-1" });

    fireEvent.contextMenu(titleLabel(container));
    const regenerate = screen.getByRole("menuitem", { name: "Regenerate Response" });
    expect(regenerate).not.toBeDisabled();

    await user.click(regenerate);
    expect(onRegenerate).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Regenerate Response is absent from the DOM entirely (not merely disabled) when parentChatNodeId is null", () => {
    const { container } = renderCodeNode({ parentChatNodeId: null });
    fireEvent.contextMenu(titleLabel(container));
    expect(screen.queryByRole("menuitem", { name: "Regenerate Response" })).toBeNull();
  });

  it("Hide Other Branches reads 'Hide Other Branches' and calls onToggleBranchFocus then closes the menu when branch focus is inactive (R8a)", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus, container } = renderCodeNode({ isBranchFocusActive: false });

    fireEvent.contextMenu(titleLabel(container));
    const toggle = screen.getByRole("menuitem", { name: "Hide Other Branches" });
    expect(toggle).not.toBeDisabled();

    await user.click(toggle);
    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Hide Other Branches reads 'Show All Branches' and still calls onToggleBranchFocus when branch focus is active (R8a)", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus, container } = renderCodeNode({ isBranchFocusActive: true });

    fireEvent.contextMenu(titleLabel(container));
    expect(screen.queryByRole("menuitem", { name: "Hide Other Branches" })).toBeNull();
    const toggle = screen.getByRole("menuitem", { name: "Show All Branches" });

    await user.click(toggle);
    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("Escape and outside-click both close the menu", async () => {
    const user = userEvent.setup();
    const { container } = renderCodeNode();
    const label = titleLabel(container);

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
