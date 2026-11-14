import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GROUP_NAMED_COLORS } from "./GroupColorPicker";
import { initialSceneState } from "./sceneStore";
import { validateSceneState, type SceneState } from "../../lib/bridge-core/generated/scene-state";

// ADR-011 stage 11.1: NoteNodeView has no LOD hook to spy on (see its own
// module doc - it never auto-collapses), so instead this wraps NodeMarkdown
// - the one child that's UNCONDITIONALLY rendered whenever the note isn't in
// edit mode (the default/only state these memo tests exercise) - via real
// JSX composition (not a raw function call, so NodeMarkdown's own hooks
// attach to their own fiber correctly). Every ACTUAL render of
// NoteNodeViewImpl creates this element; a React.memo bailout skips the
// function body entirely, so this never fires on a bailed re-render. See
// ImageNodeView.test.tsx for the general technique's full rationale.
const nodeMarkdownRenders = { count: 0 };

vi.mock("./NodeMarkdown", async (importOriginal) => {
  const original = await importOriginal<typeof import("./NodeMarkdown")>();
  const OriginalNodeMarkdown = original.NodeMarkdown;
  return {
    ...original,
    NodeMarkdown: (props: Parameters<typeof OriginalNodeMarkdown>[0]) => {
      nodeMarkdownRenders.count += 1;
      return <OriginalNodeMarkdown {...props} />;
    },
  };
});

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
      isBranchComparison: false,
      compareSourceNodeIds: [],
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

  it("selects a source and operation with the keyboard and clears a deleted choice before binding", async () => {
    const user = userEvent.setup();
    const validated = validateSceneState({
      ...initialSceneState,
      nodes: [
        { id: "n0", x: 0, y: 0, title: "", kind: "note" },
        { id: "reply", x: 100, y: 0, title: "", kind: "chat", content: "Reply" },
        { id: "draft", x: 200, y: 0, title: "", kind: "artifact", artifactContent: "Drafter text" },
      ],
    });
    if (!validated.ok) throw new Error(validated.errors.join("\n"));
    let scene: SceneState = validated.value;
    const listeners = new Set<() => void>();
    const onBindTextInput = vi.fn();
    const onRunTextInput = vi.fn();
    renderNoteNode({
      targetId: "n0",
      getScene: () => scene,
      subscribeScene: (listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
      onBindTextInput, onRunTextInput,
    });
    screen.getByRole("button", { name: "Text input" }).focus();
    await user.keyboard("{Enter}");
    const chooser = screen.getByLabelText("Text source");
    await user.tab();
    expect(chooser).toHaveFocus();
    await user.selectOptions(chooser, "draft");
    act(() => {
      scene = { ...scene, nodes: scene.nodes.filter((row) => row.id !== "draft") };
      listeners.forEach((listener) => listener());
    });
    expect(chooser).toHaveValue("");
    const connect = screen.getByRole("button", { name: "Connect" });
    expect(connect).toBeDisabled();
    await user.selectOptions(chooser, "reply");
    await user.tab();
    await user.selectOptions(screen.getByLabelText("Operation"), "word_count");
    await user.tab();
    expect(connect).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onBindTextInput).toHaveBeenCalledWith("reply", "word_count");
    expect(onRunTextInput).not.toHaveBeenCalled();
  });

  it("shows durable upstream freshness and waits for a run acknowledgement", async () => {
    const user = userEvent.setup();
    const onRunTextInput = vi.fn();
    const onUnbindTextInput = vi.fn();
    const onBindTextInput = vi.fn();
    const { onSetContent } = renderNoteNode({
      textInputSourceId: "chat-1",
      textInputSourceLabel: "Chat · Original (chat-1)",
      textInputOperation: "copy",
      textInputStatus: "stale",
      onBindTextInput, onRunTextInput, onUnbindTextInput,
    });
    expect(screen.getByText("Text from Chat · Original (chat-1)")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Source changed · rerun to update");
    const run = screen.getByRole("button", { name: "Run text input" });
    await user.click(run);
    expect(run).toBeDisabled();
    act(() => onRunTextInput.mock.calls[0]![0](false));
    expect(screen.getByRole("status")).toHaveTextContent("Run failed");
    expect(run).toBeEnabled();
    await user.selectOptions(screen.getByLabelText("Operation"), "word_count");
    expect(onBindTextInput).toHaveBeenCalledWith("chat-1", "word_count");
    await user.click(screen.getByRole("button", { name: "Disconnect text input" }));
    expect(onUnbindTextInput).toHaveBeenCalledOnce();
    expect(onSetContent).not.toHaveBeenCalled();
    expect(onRunTextInput).toHaveBeenCalledOnce();
  });

  it.each(["isSystemPrompt", "isSummaryNote", "isBranchComparison"] as const)(
    "keeps semantic notes (%s) out of text workflows", (flag) => {
      const { container } = renderNoteNode({ [flag]: true });
      expect(screen.queryByRole("button", { name: "Text input" })).toBeNull();
      expect(container.querySelectorAll(".scene-text-handle")).toHaveLength(0);
    },
  );

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

  it("Ctrl+S while editing pushes the draft before the app-level save handler, and stays in edit mode", () => {
    // Save is exempt from the while-typing shortcut gate, so Ctrl+S reaches
    // App's document-level handler (saveChat) with no blur. The draft has
    // to go out first or the saved session lacks the edit.
    const { container, onSetContent } = renderNoteNode({ content: "original" });
    fireEvent.doubleClick(container.querySelector(".note-node-content")!);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    const order: string[] = [];
    onSetContent.mockImplementation(() => {
      order.push("commit");
    });
    const onDocumentKeyDown = () => {
      order.push("document keydown");
    };
    document.addEventListener("keydown", onDocumentKeyDown);
    try {
      fireEvent.change(textarea, { target: { value: "saved edit" } });
      fireEvent.keyDown(textarea, { key: "s" });
      expect(onSetContent).not.toHaveBeenCalled();
      order.length = 0;

      fireEvent.keyDown(textarea, { key: "s", ctrlKey: true });
    } finally {
      document.removeEventListener("keydown", onDocumentKeyDown);
    }

    expect(onSetContent).toHaveBeenCalledTimes(1);
    expect(onSetContent).toHaveBeenCalledWith("saved edit");
    expect(order).toEqual(["commit", "document keydown"]);
    expect(screen.getByRole("textbox")).toBeTruthy();
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
    // The marker is carried by shape (the .system-prompt class's dashed,
    // heavier border) and the badge above - never by an inline colour. It
    // used to take the colour picker's own "Purple" swatch, which made a
    // semantic marker indistinguishable from a note somebody had simply
    // coloured purple.
    expect(container.querySelector<HTMLElement>(".note-node")!.style.borderColor).toBe("");

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

  // ADR-002 Workstream 1 ("Compare Branches") - a third, distinct badge
  // alongside isSystemPrompt/isSummaryNote above.
  it("isBranchComparison renders a distinct badge showing the source-branch count, absent otherwise", () => {
    renderNoteNode({ isBranchComparison: false });
    expect(screen.queryByTitle(/Branch Comparison/)).toBeNull();

    renderNoteNode({ isBranchComparison: true, compareSourceNodeIds: ["n1", "n2", "n3"] });
    expect(screen.getByLabelText("Branch Comparison")).toBeInTheDocument();
    expect(screen.getByTitle("Branch Comparison (3 sources)")).toBeInTheDocument();
    // Distinct from isSummaryNote's own badge - never conflated.
    expect(screen.queryByTitle("Summary Note")).toBeNull();
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

// ADR-011 stage 11.1: React.memo comparator correctness. `nodeMarkdownRenders`
// (see the mock above) fires exactly once per ACTUAL render of this view -
// never on a bailed-out one - so it's the oracle for both directions: it
// must stay flat across an irrelevant/equivalent prop change (too-tight
// would fail this) and must increment on a change to a prop the view
// actually reads (too-loose would fail this). `compareSourceNodeIds` is the
// one array field, so it gets extra coverage beyond the usual
// primitive-field pair.
describe("NoteNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  beforeEach(() => {
    nodeMarkdownRenders.count = 0;
  });

  function noteProps(overrides: Partial<NoteFlowNode["data"]> = {}) {
    const data = {
      content: "Hello world",
      color: null,
      headerColor: null,
      isSystemPrompt: false,
      isSummaryNote: false,
      isBranchComparison: false,
      compareSourceNodeIds: [] as string[],
      onSetContent: vi.fn(),
      onSetColor: vi.fn(),
      onDelete: vi.fn(),
      ...overrides,
    };
    return { id: "n0", selected: false, data } as unknown as NodeProps<NoteFlowNode>;
  }

  it("skips re-rendering when a fresh `data` object carries identical field values", () => {
    const props = noteProps({ compareSourceNodeIds: ["a", "b"] });
    const { rerender } = render(
      <ReactFlowProvider>
        <NoteNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(nodeMarkdownRenders.count).toBe(1);

    // A brand-new `data` object AND a brand-new `compareSourceNodeIds`
    // array, but every value is identical - a plain `===` on either would
    // wrongly re-render here.
    const sameValuesNewObject = { ...props.data, compareSourceNodeIds: [...props.data.compareSourceNodeIds] };
    rerender(
      <ReactFlowProvider>
        <NoteNodeView {...{ ...props, data: sameValuesNewObject }} />
      </ReactFlowProvider>,
    );
    expect(nodeMarkdownRenders.count).toBe(1);
  });

  it("re-renders when `content` (a field the view reads) changes", () => {
    const props = noteProps({ content: "first draft" });
    const { rerender } = render(
      <ReactFlowProvider>
        <NoteNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(nodeMarkdownRenders.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <NoteNodeView {...{ ...props, data: { ...props.data, content: "revised draft" } }} />
      </ReactFlowProvider>,
    );
    expect(nodeMarkdownRenders.count).toBe(2);
    expect(screen.getByText("revised draft")).toBeInTheDocument();
  });

  it("re-renders when `compareSourceNodeIds` gains/loses an id, even as a same-length-then-different array", () => {
    const props = noteProps({ isBranchComparison: true, compareSourceNodeIds: ["a", "b"] });
    const { rerender } = render(
      <ReactFlowProvider>
        <NoteNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(nodeMarkdownRenders.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <NoteNodeView {...{ ...props, data: { ...props.data, compareSourceNodeIds: ["a", "c"] } }} />
      </ReactFlowProvider>,
    );
    expect(nodeMarkdownRenders.count).toBe(2);
  });

  it("skips re-rendering when only `id` or `selected`-irrelevant NodeProps fields change - this view never reads `id`", () => {
    const props = noteProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <NoteNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(nodeMarkdownRenders.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <NoteNodeView {...{ ...props, id: "some-other-id", dragging: true }} />
      </ReactFlowProvider>,
    );
    expect(nodeMarkdownRenders.count).toBe(1);
  });
});
