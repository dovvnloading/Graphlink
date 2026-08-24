import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReactFlowProvider } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import { HarnessNodeView, type HarnessNodeData } from "./HarnessNodeView";

// Same minimal-NodeProps-inside-ReactFlowProvider shape as
// PlanNodeView.test.tsx - the established node-view test pattern.
function makeData(overrides: Partial<HarnessNodeData> = {}): HarnessNodeData {
  return {
    harnessGoal: "Summarize the workspace files",
    harnessReply: "",
    harnessStatus: "running",
    harnessStatusDetail: "",
    harnessRunId: "run-1",
    harnessActivity: [],
    harnessContextTokens: 0,
    harnessMaxContextTokens: 48000,
    harnessCompactions: 0,
    harnessAwaitingApproval: false,
    harnessApprovalToolName: "",
    harnessApprovalSummary: "",
    harnessMaxTurns: 16,
    harnessSpentTurns: 3,
    harnessSpentTokens: 4321,
    isCollapsed: false,
    pendingRequestId: "run-1",
    onToggleCollapse: vi.fn(),
    onDelete: vi.fn(),
    onSend: vi.fn(),
    onCancel: vi.fn(),
    onApproveTool: vi.fn(),
    onDenyTool: vi.fn(),
    ...overrides,
  };
}

function renderHarness(data: HarnessNodeData) {
  return render(
    <ReactFlowProvider>
      <HarnessNodeView
        id="n1"
        type="harness"
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

describe("HarnessNodeView", () => {
  it("renders the task, status, and spend counters", () => {
    renderHarness(makeData());
    expect(screen.getByText("Summarize the workspace files")).toBeInTheDocument();
    expect(screen.getByText("Working…")).toBeInTheDocument();
    expect(screen.getByText("Turns 3 (max 16/task)")).toBeInTheDocument();
    expect(screen.getByText("Tokens 4,321")).toBeInTheDocument();
  });

  it("shows Stop while running and fires the cancel callback; no composer", async () => {
    const user = userEvent.setup();
    const data = makeData();
    renderHarness(data);
    expect(screen.queryByLabelText("Follow-up message")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(data.onCancel).toHaveBeenCalledTimes(1);
  });

  it("shows the reply and sends a follow-up once the run has landed", async () => {
    const user = userEvent.setup();
    const data = makeData({
      harnessStatus: "done",
      harnessReply: "Two files: notes.txt and data.csv.",
      pendingRequestId: null,
    });
    renderHarness(data);
    expect(screen.getByText("Two files: notes.txt and data.csv.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Follow-up message"), "read notes.txt");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(data.onSend).toHaveBeenCalledWith("read notes.txt");
  });

  it("marks a failed run's detail as an alert and still accepts a follow-up", () => {
    renderHarness(
      makeData({
        harnessStatus: "failed",
        harnessStatusDetail: "Run failed: rate limited — send a follow-up to retry.",
        pendingRequestId: null,
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("rate limited");
    expect(screen.getByLabelText("Follow-up message")).toBeInTheDocument();
  });

  it("shows the approval panel with the verbatim summary and fires approve/deny", async () => {
    const user = userEvent.setup();
    const data = makeData({
      harnessAwaitingApproval: true,
      harnessApprovalToolName: "shell.exec",
      harnessApprovalSummary: "shell.exec\npython build.py --all",
    });
    renderHarness(data);
    expect(screen.getByText("shell.exec")).toBeInTheDocument();
    expect(screen.getByText(/python build\.py --all/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(data.onApproveTool).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Deny" }));
    expect(data.onDenyTool).toHaveBeenCalledTimes(1);
  });

  it("renders activity rows with error styling on failures", () => {
    renderHarness(
      makeData({
        harnessActivity: [
          { tool: "fs.read", summary: '{"path":"a.txt"}', outcome: "ok", elapsedMs: 12 },
          { tool: "fs.grep", summary: "Invalid regular expression", outcome: "error", elapsedMs: 3 },
        ],
      }),
    );
    expect(screen.getByText("2 activity entries · 1 error")).toBeInTheDocument();
    expect(screen.getByText("fs.read")).toBeInTheDocument();
    expect(screen.getByText("Invalid regular expression")).toBeInTheDocument();
  });
});
