import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { GROUP_NAMED_COLORS } from "./GroupColorPicker";
import { NoteNodeView, type NoteFlowNode } from "./NoteNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderNoteNode(overrides: Partial<NoteFlowNode["data"]> = {}) {
  const onSetContent = vi.fn();
  const onSetColor = vi.fn();
  const onDelete = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      content: "Hello **world**",
      color: null,
      headerColor: null,
      isSystemPrompt: false,
      isSummaryNote: false,
      onSetContent,
      onSetColor,
      onDelete,
      ...overrides,
    },
  } as unknown as NodeProps<NoteFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <NoteNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onSetContent, onSetColor, onDelete, container };
}

describe("NoteNodeView", () => {
  it("renders the markdown content", () => {
    renderNoteNode();
    expect(screen.getByText("world")).toBeInTheDocument(); // bold text still renders as text
  });

  it("double-click enters edit mode with a textarea pre-filled with content", () => {
    const { container } = renderNoteNode({ content: "original text" });
    fireEvent.doubleClick(container.querySelector(".note-node-content")!);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea.value).toBe("original text");
  });

  it("onBlur commits the edited text via onSetContent and exits edit mode", () => {
    const { container, onSetContent } = renderNoteNode({ content: "original" });
    fireEvent.doubleClick(container.querySelector(".note-node-content")!);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "edited text" } });
    fireEvent.blur(textarea);

    expect(onSetContent).toHaveBeenCalledWith("edited text");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("Escape reverts to the last-committed content and exits edit mode WITHOUT committing", () => {
    const { container, onSetContent } = renderNoteNode({ content: "original" });
    fireEvent.doubleClick(container.querySelector(".note-node-content")!);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "throwaway edit" } });
    // fireEvent's own return value is the DOM dispatchEvent() result - false
    // means something called preventDefault(). R8a finding #16: this MUST
    // be false, or overlays.tsx's document-level Escape handler (which now
    // checks event.defaultPrevented before closing anything) would also
    // close whatever dialog/popover happens to be open elsewhere the
    // instant a note revert fires - see overlays.tsx's own comment.
    expect(fireEvent.keyDown(textarea, { key: "Escape" })).toBe(false);

    expect(onSetContent).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
    // Re-entering edit mode shows the ORIGINAL content, not the reverted draft.
    fireEvent.doubleClick(container.querySelector(".note-node-content")!);
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("original");
  });

  it("isSystemPrompt renders a dashed border and a gear badge; isSummaryNote renders a grouped-items badge", () => {
    const { container, rerender } = (() => {
      const onSetContent = vi.fn();
      const onSetColor = vi.fn();
      const onDelete = vi.fn();
      const props = {
        id: "n0",
        selected: false,
        data: {
          content: "text",
          color: null,
          headerColor: null,
          isSystemPrompt: true,
          isSummaryNote: false,
          onSetContent,
          onSetColor,
          onDelete,
        },
      } as unknown as NodeProps<NoteFlowNode>;
      const utils = render(
        <ReactFlowProvider>
          <NoteNodeView {...props} />
        </ReactFlowProvider>,
      );
      return utils;
    })();

    expect(container.querySelector(".note-node.system-prompt")).not.toBeNull();
    expect(screen.getByTitle("System Prompt")).toBeInTheDocument();
    expect(screen.queryByTitle("Summary Note")).toBeNull();

    rerender(
      <ReactFlowProvider>
        <NoteNodeView
          {...({
            id: "n0",
            selected: false,
            data: {
              content: "text",
              color: null,
              headerColor: null,
              isSystemPrompt: false,
              isSummaryNote: true,
              onSetContent: vi.fn(),
              onSetColor: vi.fn(),
              onDelete: vi.fn(),
            },
          } as unknown as NodeProps<NoteFlowNode>)}
        />
      </ReactFlowProvider>,
    );
    expect(container.querySelector(".note-node.system-prompt")).toBeNull();
    expect(screen.getByTitle("Summary Note")).toBeInTheDocument();
  });

  it("Delete button calls onDelete", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderNoteNode();
    await user.click(screen.getByRole("button", { name: "Delete note" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("the color swatch trigger opens a popover; picking a named color calls onSetColor(hex, null)", async () => {
    const user = userEvent.setup();
    const { onSetColor } = renderNoteNode();

    await user.click(screen.getByRole("button", { name: "Set color" }));
    expect(screen.getByRole("menu", { name: "Color" })).toBeInTheDocument();

    await user.click(screen.getByRole("menuitem", { name: GROUP_NAMED_COLORS[0].name }));
    expect(onSetColor).toHaveBeenCalledWith(GROUP_NAMED_COLORS[0].hex, null);
    // Choosing a color closes the popover.
    expect(screen.queryByRole("menu", { name: "Color" })).toBeNull();
  });

  it("Reset to Default calls onSetColor(null, null)", async () => {
    const user = userEvent.setup();
    const { onSetColor } = renderNoteNode({ color: "#3f8f5c" });
    await user.click(screen.getByRole("button", { name: "Set color" }));
    await user.click(screen.getByRole("menuitem", { name: "Reset to Default" }));
    expect(onSetColor).toHaveBeenCalledWith(null, null);
  });

  it("outside-click and Escape both dismiss the color popover", async () => {
    const user = userEvent.setup();
    renderNoteNode();
    await user.click(screen.getByRole("button", { name: "Set color" }));
    expect(screen.getByRole("menu", { name: "Color" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu", { name: "Color" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Set color" }));
    expect(screen.getByRole("menu", { name: "Color" })).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("menu", { name: "Color" })).toBeNull();
  });
});
