import { ReactFlowProvider, useStoreApi, type NodeProps } from "@xyflow/react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { PyCoderNodeView, type PyCoderFlowNode } from "./PyCoderNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ArtifactNodeView.test.tsx for why a bare ReactFlowProvider is enough here
// too. NOT wrapped in OverlayProvider (unlike GitlinkNodeView.test.tsx) -
// this view's <CodeExecutionApprovalPanel> is a small, self-contained modal
// post-R5.4-fix-pass (see that component's own module doc for FIX A/FIX B),
// not a <Dialog> from the R2.1 overlay system, so there is no provider
// ancestor requirement here at all.

function baseData(overrides: Partial<PyCoderFlowNode["data"]> = {}): PyCoderFlowNode["data"] {
  return {
    pycoderMode: "ai_driven",
    pycoderPrompt: "",
    pycoderCode: "",
    pycoderOutput: "",
    pycoderAnalysis: "",
    pycoderLastRunFailed: false,
    pycoderAwaitingApproval: false,
    pycoderError: "",
    isCollapsed: false,
    pendingRequestId: null,
    onSetMode: vi.fn(),
    onRun: vi.fn(),
    onCancel: vi.fn(),
    onApprove: vi.fn(),
    onDeny: vi.fn(),
    onToggleCollapse: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  };
}

function renderPyCoderNode(overrides: Partial<PyCoderFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "pc-1", selected: false, data } as unknown as NodeProps<PyCoderFlowNode>;

  render(
    <ReactFlowProvider>
      <PyCoderNodeView {...props} />
    </ReactFlowProvider>,
  );
  return data;
}

// Directly sets the React Flow internal Zustand store's transform/zoom value
// - same technique GitlinkNodeView.test.tsx/ArtifactNodeView.test.tsx's own
// ZoomSetter uses.
function ZoomSetter({ zoom }: { zoom: number }) {
  const store = useStoreApi();
  useEffect(() => {
    store.setState({ transform: [0, 0, zoom] });
  }, [zoom, store]);
  return null;
}

function renderPyCoderNodeAtZoom(zoom: number, overrides: Partial<PyCoderFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "pc-1", selected: false, data } as unknown as NodeProps<PyCoderFlowNode>;

  render(
    <ReactFlowProvider>
      <ZoomSetter zoom={zoom} />
      <PyCoderNodeView {...props} />
    </ReactFlowProvider>,
  );
  return data;
}

describe("PyCoderNodeView", () => {
  // -- mode toggle --------------------------------------------------------

  it("defaults to the Prompt textarea (ai_driven) and clicking Manual calls onSetMode", async () => {
    const user = userEvent.setup();
    const data = renderPyCoderNode();
    expect(screen.getByRole("textbox", { name: "Prompt" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Manual" }));
    expect(data.onSetMode).toHaveBeenCalledExactlyOnceWith("manual");
  });

  it("clicking AI-Driven calls onSetMode with ai_driven", async () => {
    const user = userEvent.setup();
    const data = renderPyCoderNode({ pycoderMode: "manual" });
    await user.click(screen.getByRole("button", { name: "AI-Driven" }));
    expect(data.onSetMode).toHaveBeenCalledExactlyOnceWith("ai_driven");
  });

  it("in manual mode, the input area is labeled Code instead of Prompt, and the Manual button shows active", () => {
    renderPyCoderNode({ pycoderMode: "manual" });
    expect(screen.getByRole("textbox", { name: "Code" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Prompt" })).toBeNull();
    expect(screen.getByRole("button", { name: "Manual" })).toHaveClass("active");
    expect(screen.getByRole("button", { name: "AI-Driven" })).not.toHaveClass("active");
  });

  // -- single input economy: local until Run -------------------------------

  it("typing does not call onRun; only clicking Run does, with the trimmed text", async () => {
    const user = userEvent.setup();
    const data = renderPyCoderNode();
    const input = screen.getByRole("textbox", { name: "Prompt" });

    await user.type(input, "  write a fibonacci function  ");
    expect(data.onRun).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(data.onRun).toHaveBeenCalledExactlyOnceWith("write a fibonacci function");
  });

  it("Run is disabled while the input is empty or whitespace-only", async () => {
    const user = userEvent.setup();
    renderPyCoderNode();
    const runButton = screen.getByRole("button", { name: "Run" });
    expect(runButton).toBeDisabled();

    await user.type(screen.getByRole("textbox", { name: "Prompt" }), "   ");
    expect(runButton).toBeDisabled();
  });

  it("Run is disabled while pendingRequestId is set, even with non-empty input", async () => {
    const user = userEvent.setup();
    renderPyCoderNode({ pendingRequestId: "req-1" });
    await user.type(screen.getByRole("textbox", { name: "Prompt" }), "hello");
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  // -- Cancel ---------------------------------------------------------------

  it("Cancel is absent when pendingRequestId is null, and present+wired when set", async () => {
    const user = userEvent.setup();
    expect(renderPyCoderNode({ pendingRequestId: null })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();

    const data = renderPyCoderNode({ pendingRequestId: "req-1" });
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(data.onCancel).toHaveBeenCalledExactlyOnceWith();
  });

  // -- error / failed-run indicators ----------------------------------------

  it("shows the pycoderError banner when set", () => {
    renderPyCoderNode({ pycoderError: "Please enter a prompt." });
    expect(screen.getByRole("alert")).toHaveTextContent("Please enter a prompt.");
  });

  it("shows the last-run-failed badge only when pycoderLastRunFailed is true", () => {
    renderPyCoderNode({ pycoderLastRunFailed: true });
    expect(screen.getByText(/Last run failed/)).toBeInTheDocument();
  });

  it("does not show the failed badge when pycoderLastRunFailed is false", () => {
    renderPyCoderNode({ pycoderLastRunFailed: false });
    expect(screen.queryByText(/Last run failed/)).toBeNull();
  });

  // -- code/output/analysis panes + security -------------------------------

  it("renders the code/output/analysis panes only when their fields are non-empty", () => {
    renderPyCoderNode({ pycoderCode: "", pycoderOutput: "", pycoderAnalysis: "" });
    expect(screen.queryByText("Code")).toBeNull();
    expect(screen.queryByText("Output")).toBeNull();
    expect(screen.queryByText("Analysis")).toBeNull();
  });

  it("renders code as a syntax-highlighted fenced block and output/analysis as markdown", () => {
    renderPyCoderNode({
      pycoderCode: "def add(a, b):\n    return a + b",
      pycoderOutput: "3",
      pycoderAnalysis: "This **adds** two numbers.",
    });
    expect(screen.getByText("Code")).toBeInTheDocument();
    expect(document.querySelector("pre code")).not.toBeNull();
    expect(screen.getByText("Output")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("Analysis")).toBeInTheDocument();
    expect(screen.getByText("adds")).toBeInTheDocument();
  });

  it("SECURITY: analysis text containing a literal <img onerror> tag never becomes a real rendered img element", () => {
    renderPyCoderNode({ pycoderAnalysis: '<img src="x" onerror="alert(1)"> as requested.' });
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  // -- approval panel ---------------------------------------------------------

  it("renders the approval panel only when pycoderAwaitingApproval is true", () => {
    renderPyCoderNode({ pycoderAwaitingApproval: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders the approval panel (with the pycoder-specific warning) when pycoderAwaitingApproval is true", () => {
    renderPyCoderNode({ pycoderAwaitingApproval: true, pycoderCode: "print(1)" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/there is no sandboxing/)).toBeInTheDocument();
  });

  it("Approve calls onApprove with no arguments", async () => {
    const user = userEvent.setup();
    const data = renderPyCoderNode({ pycoderAwaitingApproval: true, pycoderCode: "print(1)" });
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(data.onApprove).toHaveBeenCalledExactlyOnceWith();
  });

  it("Deny calls onDeny with no arguments", () => {
    // Separate render from Approve's own test above - both buttons share one
    // busy flag that locks BOTH the instant either is clicked (see
    // CodeExecutionApprovalPanel's own busy-gate doc), so exercising Approve
    // and then Deny on the SAME mounted instance would find Deny disabled.
    const data = renderPyCoderNode({ pycoderAwaitingApproval: true, pycoderCode: "print(1)" });
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(data.onDeny).toHaveBeenCalledExactlyOnceWith();
  });

  // FIX B regression guard, at the real NodeView-integration level (not just
  // the panel-unit level covered in CodeExecutionApprovalPanel.test.tsx):
  // two different nodes awaiting approval simultaneously must both render
  // and stay independently interactable - there is no shared overlays.open()
  // slot left for a second node's mount to steal from the first anymore.
  it("FIX B: two PyCoderNodeView instances both awaiting approval are independently visible and interactable", async () => {
    const user = userEvent.setup();
    const dataA = baseData({ pycoderAwaitingApproval: true, pycoderCode: "print('a')" });
    const dataB = baseData({ pycoderAwaitingApproval: true, pycoderCode: "print('b')" });
    const propsA = { id: "pc-a", selected: false, data: dataA } as unknown as NodeProps<PyCoderFlowNode>;
    const propsB = { id: "pc-b", selected: false, data: dataB } as unknown as NodeProps<PyCoderFlowNode>;

    render(
      <ReactFlowProvider>
        <PyCoderNodeView {...propsA} />
        <PyCoderNodeView {...propsB} />
      </ReactFlowProvider>,
    );

    expect(screen.getAllByRole("dialog")).toHaveLength(2);

    const approveButtons = screen.getAllByRole("button", { name: "Approve" });
    expect(approveButtons).toHaveLength(2);
    await user.click(approveButtons[0]);
    expect(dataA.onApprove).toHaveBeenCalledExactlyOnceWith();
    expect(dataB.onApprove).not.toHaveBeenCalled();

    // Both panels remain mounted - node B's own dialog was never stolen or
    // hidden by node A's mount/interaction.
    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    const denyButtons = screen.getAllByRole("button", { name: "Deny" });
    await user.click(denyButtons[1]);
    expect(dataB.onDeny).toHaveBeenCalledExactlyOnceWith();
    expect(dataA.onDeny).not.toHaveBeenCalled();
  });

  // -- no streaming here (unlike CodeSandboxNodeView) -----------------------
  // PyCoderNodeData has no subscribeStream field at all in its type, so there
  // is no plumbing FOR this component to call in the first place - but as a
  // defense-in-depth runtime check, a spy attached under the same property
  // name (bypassing the type) is asserted never called across a full mount +
  // mode-toggle + type + Run + Cancel interaction sequence.
  it("never calls a subscribeStream-shaped prop, even if one were present on the data object", async () => {
    const user = userEvent.setup();
    const subscribeStreamSpy = vi.fn();
    const data = renderPyCoderNode({
      pendingRequestId: "req-1",
      ...( { subscribeStream: subscribeStreamSpy } as unknown as Partial<PyCoderFlowNode["data"]> ),
    });
    await user.click(screen.getByRole("button", { name: "Manual" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(subscribeStreamSpy).not.toHaveBeenCalled();
    expect(data.onCancel).toHaveBeenCalled();
  });

  // -- collapse/expand + LOD -------------------------------------------------

  it("manual collapse hides the body and shows only the header", () => {
    renderPyCoderNode({ isCollapsed: true });
    expect(screen.getByText("Py-Coder")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });

  it("the inline collapse chevron calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const data = renderPyCoderNode();
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("LOD auto-collapse (zoom below threshold) also hides the body, even when isCollapsed is false", () => {
    renderPyCoderNodeAtZoom(0.2, { isCollapsed: false });
    expect(screen.getByText("Py-Coder")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });

  it("stays expanded above the LOD threshold when isCollapsed is false", () => {
    renderPyCoderNodeAtZoom(1, { isCollapsed: false });
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
  });

  // -- card-level menu ----------------------------------------------------

  it("the node-level right-click menu shows exactly Collapse/Expand + Delete Node - no dock action", async () => {
    const user = userEvent.setup();
    const data = renderPyCoderNode();

    fireEvent.contextMenu(screen.getByText("Py-Coder"));
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Collapse");
    expect(items[1]).toHaveTextContent("Delete Node");
    expect(screen.queryByRole("menuitem", { name: /Dock/ })).toBeNull();

    await user.click(items[0]);
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();

    fireEvent.contextMenu(screen.getByText("Py-Coder"));
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(data.onDelete).toHaveBeenCalledOnce();
  });

  it("Escape and outside-click both close the node-level menu", async () => {
    const user = userEvent.setup();
    renderPyCoderNode();
    const header = screen.getByText("Py-Coder");

    fireEvent.contextMenu(header);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();

    fireEvent.contextMenu(header);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.click(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
