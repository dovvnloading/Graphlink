import { createContext, useCallback, useContext, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { SceneStore } from "./sceneStore";
import type { WorkflowCanvasState } from "./workflowRunTypes";
import { StatusPill } from "../ui/Badge";
import { ProgressBar } from "../ui/ProgressBar";
import { Button } from "../ui/Button";
import { useOverlays } from "../overlays/overlays";
import "./WorkflowCanvas.css";

const empty: WorkflowCanvasState = { statuses: {}, progress: null };
type Listener = () => void;

// A new receipt changes only its own badge (and possibly the progress bar).
// Keep the context identity stable: broadcasting a status-map update to all
// mounted node badges otherwise re-renders the entire visible graph.
class WorkflowProjection {
  private state = empty;
  private nodes = new Map<string, Set<Listener>>();
  private progress = new Set<Listener>();

  getStatus = (id: string | null) => id ? this.state.statuses[id] : undefined;
  getProgress = () => this.state.progress;
  subscribeNode = (id: string | null, listener: Listener) => {
    if (!id) return () => {};
    const listeners = this.nodes.get(id) ?? new Set<Listener>();
    this.nodes.set(id, listeners);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
      if (!listeners.size) this.nodes.delete(id);
    };
  };
  subscribeProgress = (listener: Listener) => {
    this.progress.add(listener);
    return () => { this.progress.delete(listener); };
  };

  set(next: WorkflowCanvasState) {
    const previous = this.state;
    const a = previous.progress;
    const b = next.progress;
    const progressChanged = a?.runId !== b?.runId || a?.total !== b?.total || a?.finished !== b?.finished;
    this.state = { statuses: next.statuses, progress: progressChanged ? b : a };
    for (const [id, listeners] of this.nodes) {
      if (previous.statuses[id] !== next.statuses[id]) {
        for (const listener of listeners) listener();
      }
    }
    if (progressChanged) for (const listener of this.progress) listener();
  }
}

const emptyProjection = new WorkflowProjection();
const WorkflowCanvasContext = createContext(emptyProjection);

/** Poll the small read-only status projection, not every run's payload. Only
 * leaf badges/progress subscribe to this context; canvas node data stays stable. */
export function WorkflowCanvasProvider({ store, enabled, children }: {
  store: SceneStore; enabled: boolean; children: ReactNode;
}) {
  const [projection] = useState(() => new WorkflowProjection());
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await store.getWorkflowCanvasState();
        if (active && next?.statuses) {
          projection.set(next);
        }
      } catch {
        if (active) projection.set(empty);
      } finally {
        if (active) timer = setTimeout(poll, 750);
      }
    }
    // Avoid polling when there are no bound nodes; never overlap requests.
    timer = setTimeout(poll, 750);
    return () => { active = false; clearTimeout(timer); projection.set(empty); };
  }, [store, enabled, projection]);
  return <WorkflowCanvasContext.Provider value={enabled ? projection : emptyProjection}>{children}</WorkflowCanvasContext.Provider>;
}

export function WorkflowNodeStatus({ nodeId, stale }: { nodeId: string | null; stale: boolean }) {
  const projection = useContext(WorkflowCanvasContext);
  const subscribe = useCallback((listener: Listener) => projection.subscribeNode(nodeId, listener), [projection, nodeId]);
  const snapshot = useCallback(() => projection.getStatus(nodeId), [projection, nodeId]);
  const status = useSyncExternalStore(subscribe, snapshot);
  if (!status || (stale && (status === "Done" || status === "Skipped"))) return null;
  return <StatusPill status={status} announce={false} />;
}

export function WorkflowCanvasProgress() {
  const projection = useContext(WorkflowCanvasContext);
  const progress = useSyncExternalStore(projection.subscribeProgress, projection.getProgress);
  const overlays = useOverlays();
  if (!progress) return null;
  return <div className="workflow-canvas-progress">
    <ProgressBar label="Workflow run progress" value={progress.finished} max={progress.total} />
    <span className="visually-hidden" role="status">Workflow: {progress.finished} of {progress.total} steps finished.</span>
    <Button size="sm" onClick={() => overlays.open("run-history", "dialog")}>Run history</Button>
  </div>;
}
