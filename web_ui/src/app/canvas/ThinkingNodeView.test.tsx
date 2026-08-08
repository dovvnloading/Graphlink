import { ReactFlowProvider, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// ADR-011 stage 11.1: wraps the real useLodVisibility so every ACTUAL render
// of this view is countable - a React.memo bailout skips the function body
// (and every hook inside it) entirely, so this never fires on a bailed
// re-render. See ImageNodeView.test.tsx for the same technique's full
// rationale.
const lodVisibilityCalls = { count: 0 };

vi.mock("./useLodVisibility", async (importOriginal) => {
  const original = await importOriginal<typeof import("./useLodVisibility")>();
  return {
    ...original,
    useLodVisibility: (...args: Parameters<typeof original.useLodVisibility>) => {
      lodVisibilityCalls.count += 1;
      return original.useLodVisibility(...args);
    },
  };
});

import { ThinkingNodeView, type ThinkingFlowNode } from "./ThinkingNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ChatNodeView.test.tsx for why a bare ReactFlowProvider is enough here too.
function renderThinkingNode(overrides: Partial<ThinkingFlowNode["data"]> = {}) {
  const onDock = vi.fn();
  const onDelete = vi.fn();
  const onToggleBranchFocus = vi.fn();
  const props = {
    id: "n0",
    selected: false,
    data: {
      thinkingText: "Considering **several** approaches before answering.",
      onDock,
      onDelete,
      isBranchFocusActive: false,
      onToggleBranchFocus,
      ...overrides,
    },
  } as unknown as NodeProps<ThinkingFlowNode>;

  render(
    <ReactFlowProvider>
      <ThinkingNodeView {...props} />
    </ReactFlowProvider>,
  );
  return { onDock, onDelete, onToggleBranchFocus };
}

describe("ThinkingNodeView", () => {
  it("renders the markdown thinking text", () => {
    renderThinkingNode();
    expect(screen.getByText("several")).toBeInTheDocument(); // bold text still renders as text
  });

  it("right-click opens a menu with real Copy Content/Dock to Parent Node/Hide Other Branches/Delete Node", async () => {
    const user = userEvent.setup();
    const { onDock, onDelete } = renderThinkingNode();

    // ADR-011 stage 11.1 (D11): production now chains `.catch()` off this
    // call, so the mock must return a real Promise (a bare vi.fn() returns
    // undefined, and `.catch` on undefined would throw).
    const writeText = vi.fn().mockResolvedValue(undefined);
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
    expect(hideBranches).toBeEnabled();

    await user.click(copyItem);
    expect(writeText).toHaveBeenCalledWith("Considering **several** approaches before answering.");

    fireEvent.contextMenu(label);
    await user.click(screen.getByRole("menuitem", { name: "Dock to Parent Node" }));
    expect(onDock).toHaveBeenCalledOnce();

    fireEvent.contextMenu(label);
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  // ADR-011 stage 11.1 (D11): Copy Content's clipboard write previously had
  // no `.catch()` - a rejected promise (permission denied, no Clipboard API,
  // insecure context) would surface as an unhandled promise rejection. Now
  // it's caught and logged, matching ImageNodeView's own established pattern
  // for its own clipboard write.
  it("does not throw/reject unhandled when Copy Content's clipboard write fails", async () => {
    const user = userEvent.setup();
    renderThinkingNode();

    const writeText = vi.fn().mockRejectedValue(new Error("permission denied"));
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    fireEvent.contextMenu(screen.getByText("Thinking"));
    await user.click(screen.getByRole("menuitem", { name: "Copy Content" }));

    await waitFor(() => expect(writeText).toHaveBeenCalled());
    await waitFor(() => expect(consoleError).toHaveBeenCalled());
    // The menu still closes normally - the failure is swallowed, not thrown.
    expect(screen.queryByRole("menu")).toBeNull();
    consoleError.mockRestore();
  });

  it("Hide Other Branches calls onToggleBranchFocus and closes the menu when branch focus is inactive", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus } = renderThinkingNode({ isBranchFocusActive: false });
    const label = screen.getByText("Thinking");

    fireEvent.contextMenu(label);
    const hideBranches = screen.getByRole("menuitem", { name: "Hide Other Branches" });
    await user.click(hideBranches);

    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("labels the item Show All Branches and still calls onToggleBranchFocus when branch focus is active", async () => {
    const user = userEvent.setup();
    const { onToggleBranchFocus } = renderThinkingNode({ isBranchFocusActive: true });
    const label = screen.getByText("Thinking");

    fireEvent.contextMenu(label);
    expect(screen.queryByRole("menuitem", { name: "Hide Other Branches" })).toBeNull();
    const showBranches = screen.getByRole("menuitem", { name: "Show All Branches" });
    await user.click(showBranches);

    expect(onToggleBranchFocus).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu")).toBeNull();
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

// ADR-011 stage 11.1: React.memo comparator correctness. `lodVisibilityCalls`
// fires exactly once per ACTUAL render - never on a bailed-out one - so it's
// the oracle for both directions (see ImageNodeView.test.tsx for the full
// rationale of this technique).
describe("ThinkingNodeView React.memo comparator (ADR-011 stage 11.1)", () => {
  beforeEach(() => {
    lodVisibilityCalls.count = 0;
  });

  function thinkingProps(overrides: Partial<ThinkingFlowNode["data"]> = {}) {
    const data = {
      thinkingText: "considering approaches",
      onDock: vi.fn(),
      onDelete: vi.fn(),
      isBranchFocusActive: false,
      onToggleBranchFocus: vi.fn(),
      ...overrides,
    };
    return { id: "n0", selected: false, data } as unknown as NodeProps<ThinkingFlowNode>;
  }

  it("skips re-rendering when a fresh `data` object carries identical field values", () => {
    const props = thinkingProps();
    const { rerender } = render(
      <ReactFlowProvider>
        <ThinkingNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    const sameValuesNewObject = { ...props.data };
    rerender(
      <ReactFlowProvider>
        <ThinkingNodeView {...{ ...props, data: sameValuesNewObject }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);
  });

  it("re-renders when `thinkingText` (a field the view reads) changes", () => {
    const props = thinkingProps({ thinkingText: "first draft" });
    const { rerender } = render(
      <ReactFlowProvider>
        <ThinkingNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <ThinkingNodeView {...{ ...props, data: { ...props.data, thinkingText: "revised draft" } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
    expect(screen.getByText("revised draft")).toBeInTheDocument();
  });

  it("re-renders when `isBranchFocusActive` toggles", () => {
    const props = thinkingProps({ isBranchFocusActive: false });
    const { rerender } = render(
      <ReactFlowProvider>
        <ThinkingNodeView {...props} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(1);

    rerender(
      <ReactFlowProvider>
        <ThinkingNodeView {...{ ...props, data: { ...props.data, isBranchFocusActive: true } }} />
      </ReactFlowProvider>,
    );
    expect(lodVisibilityCalls.count).toBe(2);
  });
});
