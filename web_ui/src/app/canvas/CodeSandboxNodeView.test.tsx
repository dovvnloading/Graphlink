import { ReactFlowProvider, useStoreApi, type NodeProps } from "@xyflow/react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it, vi } from "vitest";
import { CodeSandboxNodeView, type CodeSandboxFlowNode } from "./CodeSandboxNodeView";

// Rendered directly (not through a real <ReactFlow nodes=.../> mount) - see
// ArtifactNodeView.test.tsx for why a bare ReactFlowProvider is enough here
// too. NOT wrapped in OverlayProvider (unlike GitlinkNodeView.test.tsx) -
// this view's <CodeExecutionApprovalPanel> is a small, self-contained modal
// post-R5.4-fix-pass (see that component's own module doc for FIX A/FIX B),
// not a <Dialog> from the R2.1 overlay system, so there is no provider
// ancestor requirement here at all.

type StreamListener = (delta: string, done: boolean, reset: boolean, seq: number) => void;

function makeSubscribeStreamMock() {
  const listeners = new Map<string, StreamListener>();
  const unsubscribe = vi.fn();
  const subscribeStream = vi.fn((requestId: string, listener: StreamListener) => {
    listeners.set(requestId, listener);
    return unsubscribe;
  });
  return { subscribeStream, listeners, unsubscribe };
}

function baseData(overrides: Partial<CodeSandboxFlowNode["data"]> = {}): CodeSandboxFlowNode["data"] {
  return {
    codeSandboxRequirements: "",
    codeSandboxApprovalRequirements: "",
    codeSandboxPrompt: "",
    codeSandboxCode: "",
    codeSandboxOutput: "",
    codeSandboxAnalysis: "",
    codeSandboxAwaitingApproval: false,
    codeSandboxError: "",
    isCollapsed: false,
    pendingRequestId: null,
    onSetRequirements: vi.fn(),
    onRun: vi.fn(),
    onCancel: vi.fn(),
    onApprove: vi.fn(),
    onDeny: vi.fn(),
    onToggleCollapse: vi.fn(),
    onDelete: vi.fn(),
    subscribeStream: vi.fn().mockReturnValue(vi.fn()),
    ...overrides,
  };
}

function renderCodeSandboxNode(overrides: Partial<CodeSandboxFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "cs-1", selected: false, data } as unknown as NodeProps<CodeSandboxFlowNode>;

  render(
    <ReactFlowProvider>
      <CodeSandboxNodeView {...props} />
    </ReactFlowProvider>,
  );
  return data;
}

function renderCodeSandboxNodeWithRerender(overrides: Partial<CodeSandboxFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "cs-1", selected: false, data } as unknown as NodeProps<CodeSandboxFlowNode>;

  const utils = render(
    <ReactFlowProvider>
      <CodeSandboxNodeView {...props} />
    </ReactFlowProvider>,
  );

  function rerenderWithData(nextData: CodeSandboxFlowNode["data"]) {
    const nextProps = { id: "cs-1", selected: false, data: nextData } as unknown as NodeProps<CodeSandboxFlowNode>;
    utils.rerender(
      <ReactFlowProvider>
        <CodeSandboxNodeView {...nextProps} />
      </ReactFlowProvider>,
    );
  }

  return { data, rerenderWithData };
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

function renderCodeSandboxNodeAtZoom(zoom: number, overrides: Partial<CodeSandboxFlowNode["data"]> = {}) {
  const data = baseData(overrides);
  const props = { id: "cs-1", selected: false, data } as unknown as NodeProps<CodeSandboxFlowNode>;

  render(
    <ReactFlowProvider>
      <ZoomSetter zoom={zoom} />
      <CodeSandboxNodeView {...props} />
    </ReactFlowProvider>,
  );
  return data;
}

describe("CodeSandboxNodeView", () => {
  // -- requirements: local draft, committed only on blur/Enter -------------

  it("typing into Requirements calls no WS method - only blur commits it", async () => {
    const user = userEvent.setup();
    const data = renderCodeSandboxNode();
    const input = screen.getByRole("textbox", { name: "Requirements" });

    await user.type(input, "numpy");
    expect(data.onSetRequirements).not.toHaveBeenCalled();

    await user.click(document.body);
    expect(data.onSetRequirements).toHaveBeenCalledWith("numpy");
  });

  it("pressing Enter (no shift) in Requirements commits exactly once via blur, not twice", async () => {
    const user = userEvent.setup();
    const data = renderCodeSandboxNode();
    const input = screen.getByRole("textbox", { name: "Requirements" });

    await user.type(input, "numpy{Enter}");
    expect(data.onSetRequirements).toHaveBeenCalledTimes(1);
    expect(data.onSetRequirements).toHaveBeenCalledWith("numpy");
  });

  it("Shift+Enter inserts a newline in Requirements instead of committing", async () => {
    const user = userEvent.setup();
    const data = renderCodeSandboxNode();
    const input = screen.getByRole("textbox", { name: "Requirements" });

    await user.type(input, "numpy{Shift>}{Enter}{/Shift}pandas");
    expect(data.onSetRequirements).not.toHaveBeenCalled();
    expect(input).toHaveValue("numpy\npandas");
  });

  // -- prompt / Run enablement -----------------------------------------------

  it("Run is disabled when both the prompt and any existing code are empty", () => {
    renderCodeSandboxNode({ codeSandboxPrompt: "", codeSandboxCode: "" });
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  it("Run is enabled once a prompt is typed, even with no existing code", async () => {
    const user = userEvent.setup();
    renderCodeSandboxNode({ codeSandboxCode: "" });
    await user.type(screen.getByRole("textbox", { name: "Prompt" }), "plot a sine wave");
    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
  });

  it("Run is enabled with an empty prompt as long as code already exists (re-run), matching the backend's own guard", () => {
    renderCodeSandboxNode({ codeSandboxPrompt: "", codeSandboxCode: "print(1)" });
    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
  });

  it("clicking Run passes the trimmed prompt draft to onRun and never fires on typing alone", async () => {
    const user = userEvent.setup();
    const data = renderCodeSandboxNode();
    await user.type(screen.getByRole("textbox", { name: "Prompt" }), "  plot a sine wave  ");
    expect(data.onRun).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(data.onRun).toHaveBeenCalledExactlyOnceWith("plot a sine wave");
  });

  it("Run is disabled while pendingRequestId is set", () => {
    renderCodeSandboxNode({ pendingRequestId: "req-1", codeSandboxCode: "print(1)" });
    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
  });

  // -- Cancel ---------------------------------------------------------------

  it("Cancel is absent when pendingRequestId is null, and present+wired when set", async () => {
    const user = userEvent.setup();
    expect(renderCodeSandboxNode({ pendingRequestId: null })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();

    const data = renderCodeSandboxNode({ pendingRequestId: "req-1" });
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(data.onCancel).toHaveBeenCalledExactlyOnceWith();
  });

  // -- live terminal streaming ------------------------------------------------

  it("subscribes to the stream for pendingRequestId the instant a run starts", () => {
    const { subscribeStream } = makeSubscribeStreamMock();
    renderCodeSandboxNode({ pendingRequestId: "req-1", subscribeStream });
    expect(subscribeStream).toHaveBeenCalledWith("req-1", expect.any(Function));
  });

  it("accumulates ordered stream deltas into the live terminal pane while a run is in flight", () => {
    const { subscribeStream, listeners } = makeSubscribeStreamMock();
    renderCodeSandboxNode({ pendingRequestId: "req-1", subscribeStream });
    const listener = listeners.get("req-1")!;

    act(() => listener("Hello ", false, false, 1));
    act(() => listener("World", false, false, 2));

    expect(screen.getByText("Hello World")).toBeInTheDocument();
  });

  it("a reset delta clears prior accumulated output before appending", () => {
    const { subscribeStream, listeners } = makeSubscribeStreamMock();
    renderCodeSandboxNode({ pendingRequestId: "req-1", subscribeStream });
    const listener = listeners.get("req-1")!;

    act(() => listener("stale first attempt", false, false, 1));
    act(() => listener("fresh start", false, true, 2));

    expect(screen.queryByText(/stale first attempt/)).toBeNull();
    expect(screen.getByText("fresh start")).toBeInTheDocument();
  });

  it("shows a waiting placeholder before any delta has arrived for an in-flight run", () => {
    const { subscribeStream } = makeSubscribeStreamMock();
    renderCodeSandboxNode({ pendingRequestId: "req-1", subscribeStream });
    expect(screen.getByText("Waiting for output…")).toBeInTheDocument();
  });

  it("reconciles with the static codeSandboxOutput field once the run completes (pendingRequestId back to null)", () => {
    const { subscribeStream, listeners } = makeSubscribeStreamMock();
    const { data, rerenderWithData } = renderCodeSandboxNodeWithRerender({
      pendingRequestId: "req-1",
      subscribeStream,
    });
    const listener = listeners.get("req-1")!;
    act(() => listener("partial output", false, false, 1));
    expect(screen.getByText("partial output")).toBeInTheDocument();

    rerenderWithData({
      ...data,
      pendingRequestId: null,
      codeSandboxOutput: "final complete output",
    });

    expect(screen.queryByText("partial output")).toBeNull();
    expect(screen.getByText("final complete output")).toBeInTheDocument();
  });

  it("unsubscribes the prior stream when pendingRequestId changes to a new run", () => {
    const { subscribeStream, unsubscribe } = makeSubscribeStreamMock();
    const { data, rerenderWithData } = renderCodeSandboxNodeWithRerender({
      pendingRequestId: "req-1",
      subscribeStream,
    });
    rerenderWithData({ ...data, pendingRequestId: "req-2" });
    expect(unsubscribe).toHaveBeenCalled();
    expect(subscribeStream).toHaveBeenCalledWith("req-2", expect.any(Function));
  });

  it("shows the static codeSandboxOutput with no subscription at all when no run is in flight", () => {
    const { subscribeStream } = makeSubscribeStreamMock();
    renderCodeSandboxNode({ pendingRequestId: null, codeSandboxOutput: "previous run's output", subscribeStream });
    expect(subscribeStream).not.toHaveBeenCalled();
    expect(screen.getByText("previous run's output")).toBeInTheDocument();
  });

  // -- error / code / analysis panes ------------------------------------------

  it("shows the codeSandboxError banner when set", () => {
    renderCodeSandboxNode({ codeSandboxError: "Provide a task prompt or Python code before running the sandbox." });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Provide a task prompt or Python code before running the sandbox.",
    );
  });

  it("renders code as a syntax-highlighted fenced block and analysis as markdown", () => {
    renderCodeSandboxNode({
      codeSandboxCode: "import numpy as np\nprint(np.pi)",
      codeSandboxAnalysis: "This **prints pi**.",
    });
    expect(screen.getByText("Code")).toBeInTheDocument();
    expect(document.querySelector("pre code")).not.toBeNull();
    expect(screen.getByText("Analysis")).toBeInTheDocument();
    expect(screen.getByText("prints pi")).toBeInTheDocument();
  });

  it("SECURITY: analysis text containing a literal <img onerror> tag never becomes a real rendered img element", () => {
    renderCodeSandboxNode({ codeSandboxAnalysis: '<img src="x" onerror="alert(1)"> as requested.' });
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("SECURITY: terminal output containing literal HTML never becomes real markup (plain <pre> text, not markdown)", () => {
    renderCodeSandboxNode({ codeSandboxOutput: '<img src="x" onerror="alert(1)">' });
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText('<img src="x" onerror="alert(1)">')).toBeInTheDocument();
  });

  // -- approval panel ---------------------------------------------------------

  it("renders the approval panel only when codeSandboxAwaitingApproval is true", () => {
    renderCodeSandboxNode({ codeSandboxAwaitingApproval: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders the approval panel (with the code_sandbox-specific warning) when codeSandboxAwaitingApproval is true", () => {
    renderCodeSandboxNode({ codeSandboxAwaitingApproval: true, codeSandboxCode: "print(1)" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/isolates installed packages, not the operating system/)).toBeInTheDocument();
  });

  it("Approve calls onApprove with no arguments", async () => {
    const user = userEvent.setup();
    const data = renderCodeSandboxNode({ codeSandboxAwaitingApproval: true, codeSandboxCode: "print(1)" });
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(data.onApprove).toHaveBeenCalledExactlyOnceWith();
  });

  it("Deny calls onDeny with no arguments", () => {
    // Separate render from Approve's own test above - both buttons share one
    // busy flag that locks BOTH the instant either is clicked (see
    // CodeExecutionApprovalPanel's own busy-gate doc), so exercising Approve
    // and then Deny on the SAME mounted instance would find Deny disabled.
    const data = renderCodeSandboxNode({ codeSandboxAwaitingApproval: true, codeSandboxCode: "print(1)" });
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(data.onDeny).toHaveBeenCalledExactlyOnceWith();
  });

  // FIX C regression guard: the approval panel must show the pending
  // requirements manifest for this kind - CodeSandboxNodeView passes the
  // frozen codeSandboxApprovalRequirements snapshot straight through as the
  // panel's requirements prop, no new backend wiring involved.
  it("FIX C: the approval panel shows the Packages to be installed block, sourced from codeSandboxApprovalRequirements", () => {
    renderCodeSandboxNode({
      codeSandboxAwaitingApproval: true,
      codeSandboxCode: "print(1)",
      codeSandboxApprovalRequirements: "numpy\npandas==2.2.0",
    });
    expect(screen.getByText("Packages to be installed")).toBeInTheDocument();
    // Query the panel's own requirements-list element specifically - the
    // Requirements textarea elsewhere on this same node could ALSO contain
    // overlapping text depending on the live draft, so an unscoped
    // screen.getByText(/numpy/) would ambiguously match both.
    const packagesList = document.querySelector(".code-exec-approval-requirements-list");
    expect(packagesList).not.toBeNull();
    expect(packagesList!.textContent).toContain("numpy");
    expect(packagesList!.textContent).toContain("pandas==2.2.0");
  });

  it("FIX C: the approval panel omits the Packages block when codeSandboxApprovalRequirements is blank", () => {
    renderCodeSandboxNode({
      codeSandboxAwaitingApproval: true,
      codeSandboxCode: "print(1)",
      codeSandboxApprovalRequirements: "",
    });
    expect(screen.queryByText("Packages to be installed")).toBeNull();
  });

  // R5.4 CODESANDBOX regression guard: codeSandboxApprovalRequirements is a
  // frozen snapshot of the manifest a pending approval refers to, distinct
  // from codeSandboxRequirements - the user's still-live, editable draft for
  // the NEXT run. The two can genuinely differ at once (the user keeps
  // typing a new manifest while a previous run's approval is still parked
  // awaiting a decision), so the approval panel must read ONLY the frozen
  // field and never fall back to - or leak - the live draft.
  it("shows the frozen codeSandboxApprovalRequirements snapshot, never the live codeSandboxRequirements draft, while awaiting approval", () => {
    renderCodeSandboxNode({
      codeSandboxAwaitingApproval: true,
      codeSandboxCode: "print(1)",
      codeSandboxRequirements: "requests",
      codeSandboxApprovalRequirements: "numpy",
    });
    const packagesList = document.querySelector(".code-exec-approval-requirements-list");
    expect(packagesList).not.toBeNull();
    expect(packagesList!.textContent).toBe("numpy");
    expect(packagesList!.textContent).not.toContain("requests");
  });

  it("FIX C: the approval panel also discloses the repair-loop re-execution risk for code_sandbox", () => {
    renderCodeSandboxNode({ codeSandboxAwaitingApproval: true, codeSandboxCode: "print(1)" });
    expect(
      screen.getByText(/automatically repaired versions of this code may run under this same approval/),
    ).toBeInTheDocument();
  });

  // FIX B regression guard, at the real NodeView-integration level (not just
  // the panel-unit level covered in CodeExecutionApprovalPanel.test.tsx):
  // two different nodes awaiting approval simultaneously must both render
  // and stay independently interactable.
  it("FIX B: two CodeSandboxNodeView instances both awaiting approval are independently visible and interactable", async () => {
    const user = userEvent.setup();
    const dataA = baseData({ codeSandboxAwaitingApproval: true, codeSandboxCode: "print('a')" });
    const dataB = baseData({ codeSandboxAwaitingApproval: true, codeSandboxCode: "print('b')" });
    const propsA = { id: "cs-a", selected: false, data: dataA } as unknown as NodeProps<CodeSandboxFlowNode>;
    const propsB = { id: "cs-b", selected: false, data: dataB } as unknown as NodeProps<CodeSandboxFlowNode>;

    render(
      <ReactFlowProvider>
        <CodeSandboxNodeView {...propsA} />
        <CodeSandboxNodeView {...propsB} />
      </ReactFlowProvider>,
    );

    expect(screen.getAllByRole("dialog")).toHaveLength(2);

    const approveButtons = screen.getAllByRole("button", { name: "Approve" });
    expect(approveButtons).toHaveLength(2);
    await user.click(approveButtons[0]);
    expect(dataA.onApprove).toHaveBeenCalledExactlyOnceWith();
    expect(dataB.onApprove).not.toHaveBeenCalled();

    expect(screen.getAllByRole("dialog")).toHaveLength(2);
    const denyButtons = screen.getAllByRole("button", { name: "Deny" });
    await user.click(denyButtons[1]);
    expect(dataB.onDeny).toHaveBeenCalledExactlyOnceWith();
    expect(dataA.onDeny).not.toHaveBeenCalled();
  });

  // -- collapse/expand + LOD -------------------------------------------------

  it("manual collapse hides the body and shows only the header", () => {
    renderCodeSandboxNode({ isCollapsed: true });
    expect(screen.getByText("Execution Sandbox")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });

  it("the inline collapse chevron calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const data = renderCodeSandboxNode();
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("LOD auto-collapse (zoom below threshold) also hides the body, even when isCollapsed is false", () => {
    renderCodeSandboxNodeAtZoom(0.2, { isCollapsed: false });
    expect(screen.getByText("Execution Sandbox")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
  });

  it("stays expanded above the LOD threshold when isCollapsed is false", () => {
    renderCodeSandboxNodeAtZoom(1, { isCollapsed: false });
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
  });

  // -- card-level menu ----------------------------------------------------

  it("the node-level right-click menu shows exactly Collapse/Expand + Delete Node - no dock action", async () => {
    const user = userEvent.setup();
    const data = renderCodeSandboxNode();

    fireEvent.contextMenu(screen.getByText("Execution Sandbox"));
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Collapse");
    expect(items[1]).toHaveTextContent("Delete Node");
    expect(screen.queryByRole("menuitem", { name: /Dock/ })).toBeNull();

    await user.click(items[0]);
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();

    fireEvent.contextMenu(screen.getByText("Execution Sandbox"));
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(data.onDelete).toHaveBeenCalledOnce();
  });

  it("Escape and outside-click both close the node-level menu", async () => {
    const user = userEvent.setup();
    renderCodeSandboxNode();
    const header = screen.getByText("Execution Sandbox");

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
