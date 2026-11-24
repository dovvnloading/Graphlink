import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { SceneStore } from "./sceneStore";
import type { WorkflowCanvasState } from "./workflowRunTypes";
import { StatusPill } from "../ui/Badge";
import { ProgressBar } from "../ui/ProgressBar";
import { Button } from "../ui/Button";
import { useOverlays } from "../overlays/overlays";
import "./WorkflowCanvas.css";

const empty: WorkflowCanvasState = { statuses: {}, progress: null };
const WorkflowCanvasContext = createContext<WorkflowCanvasState>(empty);

/** Poll the small read-only status projection, not every run's payload. Only
 * leaf badges/progress subscribe to this context; canvas node data stays stable. */
export function WorkflowCanvasProvider({ store, enabled, children }: {
  store: SceneStore; enabled: boolean; children: ReactNode;
}) {
  const [state, setState] = useState<WorkflowCanvasState>(empty);
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await store.getWorkflowCanvasState();
        if (active && next?.statuses) {
          setState(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
        }
      } catch {
        if (active) setState(empty);
      } finally {
        if (active) timer = setTimeout(poll, 750);
      }
    }
    // Avoid polling when there are no bound nodes; never overlap requests.
    timer = setTimeout(poll, 750);
    return () => { active = false; clearTimeout(timer); };
  }, [store, enabled]);
  return <WorkflowCanvasContext.Provider value={enabled ? state : empty}>{children}</WorkflowCanvasContext.Provider>;
}

export function WorkflowNodeStatus({ nodeId, stale }: { nodeId: string | null; stale: boolean }) {
  const { statuses } = useContext(WorkflowCanvasContext);
  const status = nodeId ? statuses[nodeId] : undefined;
  if (!status || (stale && (status === "Done" || status === "Skipped"))) return null;
  return <StatusPill status={status} />;
}

export function WorkflowCanvasProgress() {
  const { progress } = useContext(WorkflowCanvasContext);
  const overlays = useOverlays();
  if (!progress) return null;
  return <div className="workflow-canvas-progress">
    <ProgressBar label="Workflow run progress" value={progress.finished} max={progress.total} />
    <Button size="sm" onClick={() => overlays.open("run-history", "dialog")}>Run history</Button>
  </div>;
}
