import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReactFlowProvider } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import { PlanNodeView, type PlanNodeData } from "./PlanNodeView";

// NodeProps requires the full React Flow node context - rendering inside
// ReactFlowProvider with a minimal props shape is the established pattern
// the other node-view tests use.
function makeData(overrides: Partial<PlanNodeData> = {}): PlanNodeData {
  return {
    planGoal: "Research solar output and chart it",
    planSteps: [
      { id: "s1", title: "Gather research", status: "done", detail: "found 3 sources" },
      { id: "s2", title: "Write summary", status: "running", detail: "" },
      { id: "s3", title: "Chart it", status: "pending", detail: "" },
    ],
    builderStatus: "running",
    builderMode: "copilot",
    builderRunId: "run-1",
    builderMaxSteps: 12,
    builderMaxTokens: 150000,
    builderMaxWallSeconds: 900,
    builderSpentSteps: 2,
    builderSpentTokens: 12345,
    builderSpentWallSeconds: 42,
    builderAwaitingToolApproval: false,
    builderApprovalToolName: "",
    builderApprovalSummary: "",
    builderStatusDetail: "",
    isCollapsed: false,
    pendingRequestId: "run-1",
    onToggleCollapse: vi.fn(),
    onDelete: vi.fn(),
    onStartExecution: vi.fn(),
    onCancel: vi.fn(),
    onApproveTool: vi.fn(),
    onDenyTool: vi.fn(),
    onUndoBuild: vi.fn(),
    onSaveRecipe: vi.fn(),
    ...overrides,
  };
}

function renderPlan(data: PlanNodeData) {
  return render(
    <ReactFlowProvider>
      <PlanNodeView
        id="n1"
        type="plan"
        data={data}
        selected={false}
        isConnectable={false}
        positionAbsoluteX={0}
        positionAbsoluteY={0}
        zIndex={0}
        dragging={false}
        deletable
        selectable
        draggable
        parentId={undefined}
      />
    </ReactFlowProvider>,
  );
}

describe("PlanNodeView", () => {
  it("renders the goal, every step with its status, and the budget line", () => {
    renderPlan(makeData());

    expect(screen.getByText("Research solar output and chart it")).toBeInTheDocument();
    expect(screen.getByText("Gather research")).toBeInTheDocument();
    expect(screen.getByText("found 3 sources")).toBeInTheDocument();
    expect(screen.getByText("Write summary")).toBeInTheDocument();
    expect(screen.getByText("Chart it")).toBeInTheDocument();
    expect(screen.getByText("Steps 2/12")).toBeInTheDocument();
    expect(screen.getByText("Tokens 12,345/150,000")).toBeInTheDocument();
    expect(screen.getByText("Building…")).toBeInTheDocument();
  });

  it("shows Stop while running and fires the cancel callback", async () => {
    const user = userEvent.setup();
    const data = makeData();
    renderPlan(data);

    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(data.onCancel).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /Start build|Resume/ })).not.toBeInTheDocument();
  });

  it("shows Start build when awaiting_start and Resume when paused", async () => {
    const user = userEvent.setup();
    const awaiting = makeData({ builderStatus: "awaiting_start", pendingRequestId: null });
    const { unmount } = renderPlan(awaiting);
    await user.click(screen.getByRole("button", { name: "Start build" }));
    expect(awaiting.onStartExecution).toHaveBeenCalledTimes(1);
    unmount();

    const paused = makeData({
      builderStatus: "paused", pendingRequestId: null,
      builderStatusDetail: "Token budget reached (150,000). Raise the budget and resume to continue.",
    });
    renderPlan(paused);
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.getByText(/Token budget reached/)).toBeInTheDocument();
  });

  it("renders the approval panel with Approve/Deny when a tool call is parked", async () => {
    const user = userEvent.setup();
    const data = makeData({
      builderAwaitingToolApproval: true,
      builderApprovalToolName: "graph.create_node",
      builderApprovalSummary: 'graph.create_node {"kind":"note"}',
    });
    renderPlan(data);

    expect(screen.getByRole("group", { name: "Builder tool approval" })).toBeInTheDocument();
    expect(screen.getByText("graph.create_node")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(data.onApproveTool).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Deny" }));
    expect(data.onDenyTool).toHaveBeenCalledTimes(1);
  });

  it("offers Undo build only once the run is over, and fires it", async () => {
    const user = userEvent.setup();
    const running = makeData(); // builderStatus: "running"
    const { unmount } = renderPlan(running);
    expect(screen.queryByRole("button", { name: "Undo build" })).not.toBeInTheDocument();
    unmount();

    const done = makeData({ builderStatus: "done", pendingRequestId: null });
    renderPlan(done);
    await user.click(screen.getByRole("button", { name: "Undo build" }));
    expect(done.onUndoBuild).toHaveBeenCalledTimes(1);
  });

  it("offers Save as recipe only on a done build with steps, and fires it", async () => {
    const user = userEvent.setup();
    const done = makeData({ builderStatus: "done", pendingRequestId: null });
    renderPlan(done);
    await user.click(screen.getByRole("button", { name: "Save as recipe" }));
    expect(done.onSaveRecipe).toHaveBeenCalledTimes(1);
  });

  it("hides Save as recipe on a failed build", () => {
    renderPlan(makeData({ builderStatus: "failed", pendingRequestId: null }));
    expect(screen.queryByRole("button", { name: "Save as recipe" })).not.toBeInTheDocument();
  });

  it("review-fix: shows Resume on a failed build - the backend now treats it as resumable", () => {
    renderPlan(makeData({
      builderStatus: "failed", pendingRequestId: null,
      builderStatusDetail: "Build failed: rate limited — resume to retry.",
    }));
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("hides Undo build when no run ever stamped the plan", () => {
    renderPlan(makeData({ builderStatus: "done", builderRunId: "", pendingRequestId: null }));
    expect(screen.queryByRole("button", { name: "Undo build" })).not.toBeInTheDocument();
  });

  it("every action button carries the nodrag class so React Flow's drag handler cannot swallow the click", () => {
    renderPlan(makeData({
      builderStatus: "paused", pendingRequestId: null,
      builderAwaitingToolApproval: true,
      builderApprovalToolName: "graph.create_node",
      builderApprovalSummary: "x",
    }));
    for (const name of ["Resume", "Undo build", "Approve", "Deny"]) {
      expect(screen.getByRole("button", { name })).toHaveClass("nodrag");
    }
  });

  it("marks a failed build's detail as an alert and shows the autopilot chip", () => {
    renderPlan(makeData({
      builderStatus: "failed", builderMode: "autopilot",
      builderStatusDetail: "Build failed: the model aborted.", pendingRequestId: null,
    }));

    expect(screen.getByRole("alert")).toHaveTextContent("the model aborted");
    expect(screen.getByText("autopilot")).toBeInTheDocument();
  });
});
