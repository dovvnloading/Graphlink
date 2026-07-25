import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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
    fireEvent.keyDown(input, { key: "Escape" });

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
