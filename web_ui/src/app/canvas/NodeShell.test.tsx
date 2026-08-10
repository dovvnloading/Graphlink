import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReactFlowProvider } from "@xyflow/react";
import { NodeShell } from "./NodeShell";

function renderShell(props: Partial<React.ComponentProps<typeof NodeShell>> = {}) {
  return render(
    <ReactFlowProvider>
      <NodeShell
        kindClassName="widget-node"
        selected={false}
        collapsed={false}
        header={<div className="scene-node-title">Widget</div>}
        bodyClassName="widget-node-content"
        {...props}
      >
        {props.children ?? <p>body</p>}
      </NodeShell>
    </ReactFlowProvider>,
  );
}

describe("NodeShell (ADR-012 stage 12.5)", () => {
  it("renders the scene-node wrapper with the kind class", () => {
    const { container } = renderShell();
    const wrapper = container.querySelector(".scene-node");
    expect(wrapper).toHaveClass("scene-node", "widget-node");
    expect(wrapper).not.toHaveClass("selected", "collapsed");
  });

  it("adds .selected and .collapsed classes when set", () => {
    const { container } = renderShell({ selected: true, collapsed: true });
    const wrapper = container.querySelector(".scene-node");
    expect(wrapper).toHaveClass("selected", "collapsed");
  });

  it("renders the header always, even when collapsed", () => {
    renderShell({ collapsed: true });
    expect(screen.getByText("Widget")).toBeInTheDocument();
  });

  it("gates the body behind !collapsed", () => {
    const { rerender, container } = render(
      <ReactFlowProvider>
        <NodeShell
          kindClassName="widget-node"
          selected={false}
          collapsed={false}
          header={<div>Widget</div>}
          bodyClassName="widget-node-content"
        >
          <p>body text</p>
        </NodeShell>
      </ReactFlowProvider>,
    );
    expect(screen.getByText("body text")).toBeInTheDocument();
    expect(container.querySelector(".scene-node-body")).toHaveClass("scene-node-body", "widget-node-content");

    rerender(
      <ReactFlowProvider>
        <NodeShell
          kindClassName="widget-node"
          selected={false}
          collapsed={true}
          header={<div>Widget</div>}
          bodyClassName="widget-node-content"
        >
          <p>body text</p>
        </NodeShell>
      </ReactFlowProvider>,
    );
    expect(screen.queryByText("body text")).not.toBeInTheDocument();
  });

  it("wires onContextMenu and stamps aria-haspopup only when a handler is passed", () => {
    const onContextMenu = vi.fn();
    const { container, rerender } = renderShell({ onContextMenu });
    const wrapper = container.querySelector(".scene-node")!;
    expect(wrapper).toHaveAttribute("aria-haspopup", "menu");
    fireEvent.contextMenu(wrapper);
    expect(onContextMenu).toHaveBeenCalledOnce();

    rerender(
      <ReactFlowProvider>
        <NodeShell
          kindClassName="widget-node"
          selected={false}
          collapsed={false}
          header={<div>Widget</div>}
          bodyClassName="widget-node-content"
        >
          <p>body</p>
        </NodeShell>
      </ReactFlowProvider>,
    );
    expect(container.querySelector(".scene-node")).not.toHaveAttribute("aria-haspopup");
  });

  it("renders the menu slot as-is, after the body", () => {
    renderShell({ menu: <div role="menu">a menu</div> });
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("renders exactly two handles (target then source)", () => {
    const { container } = renderShell();
    const handles = container.querySelectorAll(".scene-node-handle");
    expect(handles).toHaveLength(2);
  });

  it("merges the style prop onto the wrapper, omitting it entirely when unset", () => {
    const { container: withoutStyle } = renderShell();
    expect(withoutStyle.querySelector(".scene-node")).not.toHaveAttribute("style");

    const { container: withStyle } = renderShell({ style: { backgroundColor: "rgb(1, 2, 3)" } });
    expect(withStyle.querySelector(".scene-node")).toHaveStyle({ backgroundColor: "rgb(1, 2, 3)" });
  });

  it("renders the resizer slot first, before the target handle", () => {
    const { container } = renderShell({ resizer: <div data-testid="resizer">resizer</div> });
    const wrapper = container.querySelector(".scene-node")!;
    const resizer = screen.getByTestId("resizer");
    const handle = wrapper.querySelector(".scene-node-handle");
    expect(resizer.compareDocumentPosition(handle!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("wires onBodyDoubleClick to the body div specifically, not the wrapper", () => {
    const onBodyDoubleClick = vi.fn();
    const { container } = renderShell({ onBodyDoubleClick });
    fireEvent.doubleClick(container.querySelector(".scene-node-body")!);
    expect(onBodyDoubleClick).toHaveBeenCalledOnce();
  });
});
