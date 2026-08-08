import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ADR-011 stage 11.1: GroupNodeView has no LOD hook to spy on (frames/
// containers never auto-collapse on zoom - see this file's own module doc),
// so this wraps <GroupColorPicker/> instead - it's unconditionally part of
// `controlsCluster`, which renders in BOTH the collapsed-pill and
// expanded-topbar branches, for every groupKind - so it's called on every
// ACTUAL render of this view and never on a bailed-out one. Same
// "mock-wrap-and-delegate" technique ImageNodeView.test.tsx established at
// hook granularity, applied here at child-component granularity instead.
const colorPickerRenders = { count: 0 };

vi.mock("./GroupColorPicker", async (importOriginal) => {
  const original = await importOriginal<typeof import("./GroupColorPicker")>();
  return {
    ...original,
    GroupColorPicker: (props: Parameters<typeof original.GroupColorPicker>[0]) => {
      colorPickerRenders.count += 1;
      return original.GroupColorPicker(props);
    },
  };
});

import { GroupNodeView, type GroupFlowNode } from "./GroupNodeView";

function renderGroupNode(overrides: Partial<GroupFlowNode["data"]> = {}) {
  const onSetLabel = vi.fn();
  const onToggleCollapsed = vi.fn();
  const onToggleLock = vi.fn();
  const onSetColor = vi.fn();
  const onResize = vi.fn();
  const onFitToContent = vi.fn();
  const onUngroup = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      groupKind: "frame",
      label: "My Frame",
      color: null,
      headerColor: null,
      isCollapsed: false,
      isLocked: true,
      itemIds: ["n1", "n2"],
      memberKinds: ["chat", "chat"],
      onSetLabel,
      onToggleCollapsed,
      onToggleLock,
      onSetColor,
      onResize,
      onFitToContent,
      onUngroup,
      ...overrides,
    },
  } as unknown as NodeProps<GroupFlowNode>;

  const { container } = render(
    <ReactFlowProvider>
      <GroupNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onSetLabel, onToggleCollapsed, onToggleLock, onSetColor, onResize, onFitToContent, onUngroup, container };
}

describe("GroupNodeView (frame)", () => {
  it("renders the label and a Lock/Unlock toggle for kind=frame", () => {
    renderGroupNode();
    expect(screen.getByText("My Frame")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unlock" })).toBeInTheDocument(); // isLocked: true -> shows "Unlock"
  });

  it("double-click on the header enters edit mode with an input pre-filled with the label", () => {
    const { container } = renderGroupNode({ label: "Original" });
    fireEvent.doubleClick(container.querySelector(".group-node-header")!);
    const input = screen.getByRole("textbox") as HTMLInputElement;
    expect(input.value).toBe("Original");
  });

  it("Enter commits the edited label via onSetLabel and exits edit mode", () => {
    const { container, onSetLabel } = renderGroupNode({ label: "Original" });
    fireEvent.doubleClick(container.querySelector(".group-node-header")!);
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Renamed" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSetLabel).toHaveBeenCalledWith("Renamed");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("blur also commits the edited label via onSetLabel", () => {
    const { container, onSetLabel } = renderGroupNode({ label: "Original" });
    fireEvent.doubleClick(container.querySelector(".group-node-header")!);
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Blurred" } });
    fireEvent.blur(input);

    expect(onSetLabel).toHaveBeenCalledWith("Blurred");
  });

  it("Escape cancels the edit WITHOUT calling onSetLabel", () => {
    const { container, onSetLabel } = renderGroupNode({ label: "Original" });
    fireEvent.doubleClick(container.querySelector(".group-node-header")!);
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Throwaway" } });
    // R8a finding #16: must claim the event (preventDefault -> dispatchEvent
    // returns false) or overlays.tsx's document-level Escape handler would
    // also close whatever dialog/popover is open elsewhere - see
    // NoteNodeView's identical guard and overlays.tsx's own comment.
    expect(fireEvent.keyDown(input, { key: "Escape" })).toBe(false);

    expect(onSetLabel).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("Collapse/Expand toggle calls onToggleCollapsed", async () => {
    const user = userEvent.setup();
    const { onToggleCollapsed } = renderGroupNode({ isCollapsed: false });
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(onToggleCollapsed).toHaveBeenCalledOnce();
  });

  it("Lock/Unlock toggle calls onToggleLock", async () => {
    const user = userEvent.setup();
    const { onToggleLock } = renderGroupNode({ isLocked: true });
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect(onToggleLock).toHaveBeenCalledOnce();
  });

  it("Fit to Content calls onFitToContent when expanded", async () => {
    const user = userEvent.setup();
    const { onFitToContent } = renderGroupNode({ isCollapsed: false });
    await user.click(screen.getByRole("button", { name: "Fit to Content" }));
    expect(onFitToContent).toHaveBeenCalledOnce();
  });

  it("Fit to Content is hidden while collapsed", () => {
    renderGroupNode({ isCollapsed: true });
    expect(screen.queryByRole("button", { name: "Fit to Content" })).toBeNull();
  });

  it("Ungroup calls onUngroup", async () => {
    const user = userEvent.setup();
    const { onUngroup } = renderGroupNode();
    await user.click(screen.getByRole("button", { name: "Ungroup" }));
    expect(onUngroup).toHaveBeenCalledOnce();
  });

  it("the color swatch trigger opens a popover wired to onSetColor", async () => {
    const user = userEvent.setup();
    const { onSetColor } = renderGroupNode();
    await user.click(screen.getByRole("button", { name: "Set color" }));
    await user.click(screen.getByRole("menuitem", { name: "Blue" }));
    expect(onSetColor).toHaveBeenCalledWith("#3f7dc9", null);
  });
});

describe("GroupNodeView (container)", () => {
  it("never renders a Lock/Unlock toggle for kind=container", () => {
    renderGroupNode({ groupKind: "container", isLocked: true });
    expect(screen.queryByRole("button", { name: "Unlock" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Lock" })).toBeNull();
  });

  it("never renders a Fit to Content button for kind=container (frame-only)", () => {
    renderGroupNode({ groupKind: "container" });
    expect(screen.queryByRole("button", { name: "Fit to Content" })).toBeNull();
  });
});

describe("GroupNodeView collapsed-container hover preview (R6.1 follow-up)", () => {
  it("shows a member-count + kind-breakdown tooltip on hover when collapsed", () => {
    const { container } = renderGroupNode({
      groupKind: "container",
      isCollapsed: true,
      memberKinds: ["chat", "chat", "code"],
    });

    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.mouseEnter(container.querySelector(".group-node")!);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    expect(screen.getByText("3 items")).toBeInTheDocument();
    expect(screen.getByText("chat x2, code")).toBeInTheDocument();

    fireEvent.mouseLeave(container.querySelector(".group-node")!);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("does not show the preview for a frame, even when collapsed and hovered (container-only, matching legacy)", () => {
    const { container } = renderGroupNode({
      groupKind: "frame",
      isCollapsed: true,
      memberKinds: ["chat"],
    });

    fireEvent.mouseEnter(container.querySelector(".group-node")!);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("does not show the preview for an EXPANDED container, even when hovered", () => {
    const { container } = renderGroupNode({
      groupKind: "container",
      isCollapsed: false,
      memberKinds: ["chat"],
    });

    fireEvent.mouseEnter(container.querySelector(".group-node")!);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("does not show the preview for an empty container", () => {
    const { container } = renderGroupNode({
      groupKind: "container",
      isCollapsed: true,
      memberKinds: [],
    });

    fireEvent.mouseEnter(container.querySelector(".group-node")!);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});

// ADR-011 stage 11.1: React.memo comparator correctness. `colorPickerRenders`
// (see the mock above) fires exactly once per ACTUAL render of this view -
// never on a bailed-out one - so it's the oracle for both directions: it must
// stay flat across an irrelevant/equivalent prop change (too-tight would fail
// this) and must increment on a change to a prop the view actually reads
// (too-loose would fail this).
describe("GroupNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  beforeEach(() => {
    colorPickerRenders.count = 0;
  });

  function baseGroupProps(overrides: Partial<GroupFlowNode["data"]> = {}) {
    const data = {
      groupKind: "frame" as const,
      label: "My Frame",
      color: null,
      headerColor: null,
      isCollapsed: false,
      isLocked: true,
      itemIds: ["n1", "n2"],
      memberKinds: ["chat", "chat"],
      onSetLabel: vi.fn(),
      onToggleCollapsed: vi.fn(),
      onToggleLock: vi.fn(),
      onSetColor: vi.fn(),
      onResize: vi.fn(),
      onFitToContent: vi.fn(),
      onUngroup: vi.fn(),
      ...overrides,
    };
    return { id: "n0", selected: false, data } as unknown as NodeProps<GroupFlowNode>;
  }

  it("skips re-rendering when a fresh `data` object carries identical field values", () => {
    const props = baseGroupProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <GroupNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);

    // A brand-new object, same primitives, same `memberKinds`/`itemIds` array
    // references and same callback references - exactly what toFlowNodes may
    // mint on an unrelated snapshot. A naive `data === nextData` reference
    // compare (or React.memo's default shallow-props compare) would wrongly
    // re-render here.
    const sameValuesNewObject = { ...props.data };
    rerender(
      <ReactFlowProvider>
        <GroupNodeView {...{ ...props, data: sameValuesNewObject }} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);
  });

  it("skips re-rendering when `memberKinds` is a brand-new but byte-identical array", () => {
    // Proves the comparator does shape-aware element compare on
    // `memberKinds`, not a naive `===` (always a miss for a fresh array
    // instance) and not a naive "always unequal for arrays" shortcut either
    // (which would defeat memoization for every collapsed container).
    const props = baseGroupProps({ groupKind: "container", memberKinds: ["chat", "code"] });
    const { rerender } = render(
      <ReactFlowProvider>
        <GroupNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <GroupNodeView {...{ ...props, data: { ...props.data, memberKinds: ["chat", "code"] } }} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);
  });

  it("skips re-rendering when only `itemIds` (a field this view never reads) changes", () => {
    // Pins the deliberate omission documented on groupNodeDataAreEqual:
    // itemIds is SceneCanvas's own drag-cascade concern, never read by this
    // component's render - so a change to it alone must never re-render.
    const props = baseGroupProps({ itemIds: ["n1", "n2"] });
    const { rerender } = render(
      <ReactFlowProvider>
        <GroupNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <GroupNodeView {...{ ...props, data: { ...props.data, itemIds: ["n1", "n2", "n3"] } }} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);
  });

  it("re-renders when `memberKinds` content actually changes", () => {
    const props = baseGroupProps({ groupKind: "container", memberKinds: ["chat"] });
    const { rerender } = render(
      <ReactFlowProvider>
        <GroupNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <GroupNodeView {...{ ...props, data: { ...props.data, memberKinds: ["chat", "code"] } }} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(2);
  });

  it("re-renders when `label` (a field the view reads) changes", () => {
    const props = baseGroupProps({ label: "My Frame" });
    const { rerender } = render(
      <ReactFlowProvider>
        <GroupNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <GroupNodeView {...{ ...props, data: { ...props.data, label: "Renamed Frame" } }} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(2);
    expect(screen.getByText("Renamed Frame")).toBeInTheDocument();
  });

  it("re-renders when a callback prop is rebound to a new closure (e.g. onUngroup)", () => {
    const props = baseGroupProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <GroupNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <GroupNodeView {...{ ...props, data: { ...props.data, onUngroup: vi.fn() } }} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(2);
  });

  it("re-renders when `id` changes (forwarded to NodeResizer's nodeId)", () => {
    const props = baseGroupProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <GroupNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <GroupNodeView {...{ ...props, id: "n99" }} />
      </ReactFlowProvider>,
    );
    expect(colorPickerRenders.count).toBe(2);
  });
});
