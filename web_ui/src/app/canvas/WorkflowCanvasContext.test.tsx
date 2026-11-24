import { Profiler } from "react";
import { act, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { WorkflowCanvasProvider, WorkflowNodeStatus } from "./WorkflowCanvasContext";
import type { SceneStore } from "./sceneStore";
import type { WorkflowCanvasState, WorkflowCanvasStatus } from "./workflowRunTypes";

it("only commits the changed badge on a 500-node canvas with 100 workflow steps", async () => {
  vi.useFakeTimers();
  const statuses: Record<string, WorkflowCanvasStatus> = Object.fromEntries(
    Array.from({ length: 100 }, (_, i) => [String(i), "Queued"]),
  );
  let snapshot: WorkflowCanvasState = { statuses, progress: { runId: "audit", total: 100, finished: 0 } };
  const getWorkflowCanvasState = vi.fn(async () => snapshot);
  const store = { getWorkflowCanvasState } as unknown as SceneStore;
  const commits: string[] = [];
  const view = render(<WorkflowCanvasProvider store={store} enabled>
    {Array.from({ length: 500 }, (_, i) => <Profiler key={i} id={String(i)}
      onRender={id => { commits.push(id); }}>
      <WorkflowNodeStatus nodeId={String(i)} stale={false} />
    </Profiler>)}
  </WorkflowCanvasProvider>);
  try {
    await act(async () => { await vi.advanceTimersByTimeAsync(750); });
    expect(screen.getAllByText("Queued")).toHaveLength(100);
    commits.length = 0;
    snapshot = { statuses: { ...statuses, "42": "Done" },
      progress: { runId: "audit", total: 100, finished: 1 } };
    await act(async () => { await vi.advanceTimersByTimeAsync(750); });
    expect(screen.getByText("Done")).toBeVisible();
    expect(commits).toEqual(["42"]);
    commits.length = 0;
    snapshot = { statuses: { ...snapshot.statuses }, progress: { ...snapshot.progress! } };
    await act(async () => { await vi.advanceTimersByTimeAsync(750); });
    expect(commits).toEqual([]);
    expect(getWorkflowCanvasState).toHaveBeenCalledTimes(3);
  } finally {
    view.unmount();
    vi.useRealTimers();
  }
});
