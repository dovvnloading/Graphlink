import type { ReactElement } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
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
    builderActivity: [],
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
    onSetPlanSteps: vi.fn(),
    ...overrides,
  };
}

function planElement(data: PlanNodeData) {
  return (
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
    </ReactFlowProvider>
  );
}

function renderPlan(data: PlanNodeData) {
  return render(planElement(data));
}

// ReactFlowProvider remounting on every rerender would reset internal state
// unrelated to this test - reusing the exact same element shape as
// renderPlan keeps rerender() a like-for-like prop update instead.
function rerenderPlan(rerender: (ui: ReactElement) => void, data: PlanNodeData) {
  rerender(planElement(data));
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

  // -- collapse/delete wiring (previously declared on PlanNodeData with no
  // renderer in this view at all - see the tech-debt sweep that added this
  // affordance, matching every other content-card kind's own inline
  // chevron + right-click menu). ---------------------------------------

  it("the inline collapse chevron calls onToggleCollapse", async () => {
    const user = userEvent.setup();
    const data = makeData();
    renderPlan(data);
    await user.click(screen.getByRole("button", { name: "Collapse" }));
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();
  });

  it("the node-level right-click menu shows exactly Collapse/Expand + Delete Node", async () => {
    const user = userEvent.setup();
    const data = makeData();
    renderPlan(data);

    fireEvent.contextMenu(screen.getByText("Research solar output and chart it"));
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();

    const items = screen.getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("Collapse");
    expect(items[1]).toHaveTextContent("Delete Node");

    await user.click(items[0]);
    expect(data.onToggleCollapse).toHaveBeenCalledOnce();

    fireEvent.contextMenu(screen.getByText("Research solar output and chart it"));
    await user.click(screen.getByRole("menuitem", { name: "Delete Node" }));
    expect(data.onDelete).toHaveBeenCalledOnce();
  });

  it("the menu's Collapse/Expand label flips when isCollapsed is true", () => {
    renderPlan(makeData({ isCollapsed: true }));
    fireEvent.contextMenu(screen.getByText("Research solar output and chart it"));
    expect(screen.getAllByRole("menuitem")[0]).toHaveTextContent("Expand");
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

  describe("activity log", () => {
    it("stays hidden entirely when the build has no activity yet", () => {
      renderPlan(makeData({ builderActivity: [] }));
      expect(screen.queryByText(/activity entr/)).not.toBeInTheDocument();
    });

    it("shows the count, error count, and every row's tool/summary/elapsed - collapsed by default", () => {
      const data = makeData({
        builderActivity: [
          { tool: "graph.create_node", summary: '{"kind":"note"}', outcome: "ok", stepId: "s1", elapsedMs: 12 },
          {
            tool: "graph.create_node",
            summary: "Tool call 'graph.create_node' was denied approval.",
            outcome: "error", stepId: "s1", elapsedMs: 0,
          },
        ],
      });
      const { container } = renderPlan(data);

      // The error count lives in its own <span> (it is the one part of this
      // line worth reading at a glance and is styled to say so), so the
      // summary's text is assembled from more than one node - matched on
      // the element's own textContent rather than with a single-node text
      // query, which no longer sees the whole string.
      const summary = container.querySelector(".plan-node-activity summary")!;
      expect(summary.textContent).toBe("2 activity entries · 1 error");
      const details = summary.closest("details");
      expect(details).not.toHaveAttribute("open");
      expect(screen.getAllByText("graph.create_node")).toHaveLength(2);
      expect(screen.getByText('{"kind":"note"}')).toBeInTheDocument();
      expect(screen.getByText(/was denied approval/)).toBeInTheDocument();
      expect(screen.getByText("12ms")).toBeInTheDocument();
    });

    it("tints an error row distinctly from an ok row", () => {
      renderPlan(makeData({
        builderActivity: [
          { tool: "graph.create_node", summary: "ok call", outcome: "ok", stepId: "s1", elapsedMs: 5 },
          { tool: "run_node", summary: "boom", outcome: "error", stepId: "s1", elapsedMs: 3 },
        ],
      }));

      expect(screen.getByText("ok call").closest(".chat-node-tool-invocation")).not.toHaveClass("error");
      expect(screen.getByText("boom").closest(".chat-node-tool-invocation")).toHaveClass("error");
    });

    it("singular-cases one entry and omits the error count when nothing failed", () => {
      renderPlan(makeData({
        builderActivity: [
          { tool: "builder.complete_step", summary: "{}", outcome: "ok", stepId: "s1", elapsedMs: 1 },
        ],
      }));
      expect(screen.getByText("1 activity entry")).toBeInTheDocument();
      expect(screen.queryByText(/error/)).not.toBeInTheDocument();
    });

    it("review-fix: only auto-scrolls while running AND the disclosure is open - not collapsed, not landed", () => {
      const scrollToSpy = vi.fn();
      const originalScrollTo = Element.prototype.scrollTo;
      Element.prototype.scrollTo = scrollToSpy;
      const row = (n: number) => ({ tool: `tool-${n}`, summary: "{}", outcome: "ok", stepId: "s1", elapsedMs: n });

      try {
        const { rerender } = renderPlan(makeData({ builderStatus: "running", builderActivity: [row(1)] }));
        const details = document.querySelector(".plan-node-activity") as HTMLDetailsElement;
        expect(details.open).toBe(false); // collapsed by default

        rerenderPlan(rerender, makeData({ builderStatus: "running", builderActivity: [row(1), row(2)] }));
        expect(scrollToSpy).not.toHaveBeenCalled(); // still collapsed - must not scroll a hidden panel

        details.open = true; // the same native toggle a real click on <summary> performs
        rerenderPlan(rerender, makeData({ builderStatus: "running", builderActivity: [row(1), row(2), row(3)] }));
        expect(scrollToSpy).toHaveBeenCalledTimes(1); // running + open - follows the newest row

        scrollToSpy.mockClear();
        rerenderPlan(rerender, makeData({
          builderStatus: "done", pendingRequestId: null,
          builderActivity: [row(1), row(2), row(3), row(4)],
        }));
        expect(scrollToSpy).not.toHaveBeenCalled(); // landed - stops following, doesn't yank a spot the user scrolled to
      } finally {
        Element.prototype.scrollTo = originalScrollTo;
      }
    });
  });
});

describe("PlanNodeView plan editing (ADR-021 stage 21.3)", () => {
  // ADR-008 decided on "a checklist the user sees and can edit before
  // execution proceeds"; scene/setPlanSteps and the store method shipped in
  // 8.3 with no caller at all. These cover the affordance that closes it.

  function editable(overrides: Partial<PlanNodeData> = {}): PlanNodeData {
    return makeData({
      builderStatus: "awaiting_start",
      pendingRequestId: null,
      planSteps: [
        { id: "s1", title: "Gather research", status: "pending", detail: "" },
        { id: "s2", title: "Write summary", status: "pending", detail: "" },
      ],
      ...overrides,
    });
  }

  it("offers Edit plan only when the build is startable or resumable", () => {
    const { unmount } = render(planElement(editable()));
    expect(screen.getByRole("button", { name: "Edit plan" })).toBeTruthy();
    unmount();

    render(planElement(editable({ builderStatus: "running", pendingRequestId: "run-1" })));
    expect(screen.queryByRole("button", { name: "Edit plan" })).toBeNull();
  });

  it("retitles a pending step and commits the whole list once", async () => {
    const onSetPlanSteps = vi.fn();
    const user = userEvent.setup();
    render(planElement(editable({ onSetPlanSteps })));

    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    const first = screen.getByRole("textbox", { name: "Step 1 title" });
    await user.clear(first);
    await user.type(first, "Gather sources");

    expect(onSetPlanSteps).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Save plan" }));

    expect(onSetPlanSteps).toHaveBeenCalledTimes(1);
    expect(onSetPlanSteps.mock.calls[0][0]).toEqual([
      { id: "s1", title: "Gather sources", status: "pending", detail: "" },
      { id: "s2", title: "Write summary", status: "pending", detail: "" },
    ]);
  });

  it("adds a step with a blank id so the backend mints one", async () => {
    const onSetPlanSteps = vi.fn();
    const user = userEvent.setup();
    render(planElement(editable({ onSetPlanSteps })));

    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    await user.click(screen.getByRole("button", { name: "Add step" }));
    await user.type(screen.getByRole("textbox", { name: "Step 3 title" }), "Chart it");
    await user.click(screen.getByRole("button", { name: "Save plan" }));

    const steps = onSetPlanSteps.mock.calls[0][0];
    expect(steps).toHaveLength(3);
    expect(steps[2]).toEqual({ id: "", title: "Chart it", status: "pending", detail: "" });
  });

  it("removes a pending step", async () => {
    const onSetPlanSteps = vi.fn();
    const user = userEvent.setup();
    render(planElement(editable({ onSetPlanSteps })));

    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    await user.click(screen.getByRole("button", { name: "Remove step 1" }));
    await user.click(screen.getByRole("button", { name: "Save plan" }));

    expect(onSetPlanSteps.mock.calls[0][0]).toEqual([
      { id: "s2", title: "Write summary", status: "pending", detail: "" },
    ]);
  });

  it("reorders pending steps", async () => {
    const onSetPlanSteps = vi.fn();
    const user = userEvent.setup();
    render(planElement(editable({ onSetPlanSteps })));

    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    await user.click(screen.getByRole("button", { name: "Move step 2 up" }));
    await user.click(screen.getByRole("button", { name: "Save plan" }));

    expect(onSetPlanSteps.mock.calls[0][0].map((s: { id: string }) => s.id)).toEqual(["s2", "s1"]);
  });

  it("renders an already-run step read-only, with no way to edit or reorder past it", async () => {
    const user = userEvent.setup();
    render(
      planElement(
        editable({
          builderStatus: "paused",
          planSteps: [
            { id: "s1", title: "Gather research", status: "done", detail: "found 3" },
            { id: "s2", title: "Write summary", status: "pending", detail: "" },
          ],
        }),
      ),
    );

    await user.click(screen.getByRole("button", { name: "Edit plan" }));

    // The done step has no title input, no remove, and no move control -
    // it is immutable history, which set_plan_steps also enforces server-side.
    expect(screen.queryByRole("textbox", { name: "Step 1 title" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove step 1" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "Step 2 title" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Move step 2 up" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("refuses to save a blank title", async () => {
    const onSetPlanSteps = vi.fn();
    const user = userEvent.setup();
    render(planElement(editable({ onSetPlanSteps })));

    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    await user.clear(screen.getByRole("textbox", { name: "Step 1 title" }));

    expect(screen.getByRole("button", { name: "Save plan" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("alert").textContent).toContain("Every step needs a title");
    expect(onSetPlanSteps).not.toHaveBeenCalled();
  });

  it("discards the draft on cancel", async () => {
    const onSetPlanSteps = vi.fn();
    const user = userEvent.setup();
    render(planElement(editable({ onSetPlanSteps })));

    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    await user.type(screen.getByRole("textbox", { name: "Step 1 title" }), " extra");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onSetPlanSteps).not.toHaveBeenCalled();
    // Back to the read-only list, showing the untouched original.
    expect(screen.queryByRole("textbox", { name: "Step 1 title" })).toBeNull();
    expect(screen.getByText("Gather research")).toBeTruthy();
  });

  it("closes an open editor when the underlying plan moves under it", async () => {
    // The draft is keyed to the step list it was seeded from, so a build
    // that runs a step (or replans) while the editor is open drops the
    // draft rather than letting the user save an edit against a plan that
    // has since moved - which set_plan_steps would reject anyway, since it
    // refuses to rewrite a step that has already run.
    const onSetPlanSteps = vi.fn();
    const user = userEvent.setup();
    const data = editable({ onSetPlanSteps, builderStatus: "paused" });
    const { rerender } = render(planElement(data));

    await user.click(screen.getByRole("button", { name: "Edit plan" }));
    expect(screen.getByRole("textbox", { name: "Step 1 title" })).toBeTruthy();

    // The same node, one step later: s1 has run.
    rerender(
      planElement({
        ...data,
        planSteps: [
          { id: "s1", title: "Gather research", status: "done", detail: "found 3" },
          { id: "s2", title: "Write summary", status: "pending", detail: "" },
        ],
      }),
    );

    expect(screen.queryByRole("textbox", { name: "Step 1 title" })).toBeNull();
    expect(screen.getByRole("button", { name: "Edit plan" })).toBeTruthy();
    expect(onSetPlanSteps).not.toHaveBeenCalled();
  });
  // -- Builder redesign ------------------------------------------------------

  describe("terminal states", () => {
    it("a stopped build says it cannot be resumed, rather than just omitting Resume", () => {
      renderPlan(makeData({ builderStatus: "stopped" }));
      // "stopped" is deliberately absent from both this component's
      // RESUMABLE set and the backend's own _RESUMABLE_STATUSES, so Stop is
      // a one-way door. The card used to communicate that only by the
      // silent absence of a button.
      expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
      expect(
        screen.getByText(/A stopped build can't be resumed/),
      ).toBeInTheDocument();
    });

    it("does not show the terminal note for a state that CAN be resumed", () => {
      renderPlan(makeData({ builderStatus: "paused" }));
      expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
      expect(screen.queryByText(/can't be resumed/)).toBeNull();
    });

    it("Stop warns that it is one-way before it is pressed, not after", () => {
      renderPlan(makeData({ builderStatus: "running" }));
      expect(screen.getByRole("button", { name: "Stop" })).toHaveAttribute(
        "title",
        "Stop this build. A stopped build cannot be resumed.",
      );
    });
  });

  describe("step markers", () => {
    // The four semantic status tokens in this app's palette resolve to
    // #848484, #919191, #838383 and #828282 - error, warning, success and
    // info are the same grey. A done step and a failed step were previously
    // separated by a text glyph pair set in two of those, i.e. by nothing
    // legible. These assertions pin the replacement: distinct SHAPES, drawn
    // in the same icon language as the rest of the app.
    it("draws each status as an icon rather than as a text glyph", () => {
      const { container } = renderPlan(
        makeData({
          planSteps: [
            { id: "s1", title: "done step", status: "done", detail: "" },
            { id: "s2", title: "running step", status: "running", detail: "" },
            { id: "s3", title: "failed step", status: "failed", detail: "" },
            { id: "s4", title: "pending step", status: "pending", detail: "" },
          ],
        }),
      );
      expect(container.querySelectorAll(".plan-node-step-icon")).toHaveLength(4);
      // None of the old glyphs survive anywhere in the rendered card.
      for (const glyph of ["○", "◐", "●", "✕", "–"]) {
        expect(container.textContent).not.toContain(glyph);
      }
    });

    it("gives each status its own distinct geometry", () => {
      const markup = (status: string) =>
        renderPlan(makeData({ planSteps: [{ id: "s1", title: "t", status, detail: "" }] }))
          .container.querySelector(".plan-node-step-icon")!.innerHTML;
      const shapes = ["done", "running", "failed", "skipped", "pending"].map(markup);
      // Five statuses, five different drawings - if any two ever collapse
      // to the same paths, the distinction is back to being colour-only.
      expect(new Set(shapes).size).toBe(5);
    });
  });
});
