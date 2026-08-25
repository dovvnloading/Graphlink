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
    harnessApprovalSessionOffered: false,
    harnessPlan: [],
    harnessAwaitingQuestion: false,
    harnessQuestion: "",
    harnessWorkspacePath: "",
    harnessWorkspaceActive: "",
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
    onApproveToolForSession: vi.fn(),
    onDenyTool: vi.fn(),
    onAnswerQuestion: vi.fn(),
    onPickWorkspace: vi.fn(),
    onUseScratch: vi.fn(),
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
    await user.click(screen.getByRole("button", { name: "Approve once" }));
    expect(data.onApproveTool).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Deny" }));
    expect(data.onDenyTool).toHaveBeenCalledTimes(1);
  });

  it("offers the session-scoped grant only when the backend says it may", async () => {
    // PLAN §2.4: the backend decides (shell_policy), because it is the side
    // that knows the command is dangerous - the panel must not infer it.
    const user = userEvent.setup();
    const offered = makeData({
      harnessAwaitingApproval: true,
      harnessApprovalToolName: "fs.write",
      harnessApprovalSessionOffered: true,
    });
    const { unmount } = renderHarness(offered);
    await user.click(screen.getByRole("button", { name: "Always allow this tool" }));
    expect(offered.onApproveToolForSession).toHaveBeenCalledTimes(1);
    unmount();

    renderHarness(makeData({
      harnessAwaitingApproval: true,
      harnessApprovalToolName: "shell.exec",
      harnessApprovalSessionOffered: false,
    }));
    expect(screen.queryByRole("button", { name: "Always allow this tool" })).toBeNull();
  });

  it("renders the agent's checklist with per-step status", () => {
    renderHarness(makeData({
      harnessPlan: [
        { text: "read the config", status: "done" },
        { text: "patch the parser", status: "active" },
        { text: "run the tests", status: "pending" },
      ],
    }));
    expect(screen.getByText("read the config")).toBeInTheDocument();
    expect(screen.getByText("patch the parser")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("parks on a question and sends the typed answer, or a blank one to dismiss", async () => {
    const user = userEvent.setup();
    const data = makeData({
      harnessAwaitingQuestion: true,
      harnessQuestion: "Which database should I target?",
    });
    renderHarness(data);
    expect(screen.getByText("Which database should I target?")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Answer to the agent's question"), "postgres");
    await user.click(screen.getByRole("button", { name: "Answer" }));
    expect(data.onAnswerQuestion).toHaveBeenCalledWith("postgres");

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(data.onAnswerQuestion).toHaveBeenLastCalledWith("");
  });

  it("offers a folder picker on scratch, and a trusted-vs-pending state when bound", async () => {
    const user = userEvent.setup();
    const scratch = makeData({ harnessStatus: "done", pendingRequestId: null });
    const { rerender } = renderHarness(scratch);
    expect(screen.getByText("Scratch workspace")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Choose folder…" }));
    expect(scratch.onPickWorkspace).toHaveBeenCalledTimes(1);

    // Bound and trusted: the active dir matches the request, no warning.
    rerender(
      <ReactFlowProvider>
        <HarnessNodeView
          id="n1" type="harness"
          data={makeData({
            harnessStatus: "done", pendingRequestId: null,
            harnessWorkspacePath: "C:/proj", harnessWorkspaceActive: "C:/proj",
          })}
          selected={false} isConnectable={false} positionAbsoluteX={0} positionAbsoluteY={0}
          zIndex={0} dragging={false} deletable selectable draggable parentId={undefined}
        />
      </ReactFlowProvider>,
    );
    expect(screen.getByText(/C:\/proj/)).toBeInTheDocument();
    expect(screen.queryByText(/not trusted/)).not.toBeInTheDocument();

    // Bound but not trusted on this machine: pending warning shown.
    rerender(
      <ReactFlowProvider>
        <HarnessNodeView
          id="n1" type="harness"
          data={makeData({
            harnessStatus: "done", pendingRequestId: null,
            harnessWorkspacePath: "C:/proj", harnessWorkspaceActive: "",
          })}
          selected={false} isConnectable={false} positionAbsoluteX={0} positionAbsoluteY={0}
          zIndex={0} dragging={false} deletable selectable draggable parentId={undefined}
        />
      </ReactFlowProvider>,
    );
    expect(screen.getByText(/not trusted on this machine/)).toBeInTheDocument();
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
