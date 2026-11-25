import { useEffect } from "react";
import { authHeaders } from "../../lib/auth/token";
import { useOverlays } from "../overlays/overlays";
import { toast } from "../ui/Toast";

type Gate = {
  workspace_id: number; run_id: string; workflow: string; step_id: string; message: string;
  kind?: "gate" | "unattended";
};
export type ApprovalTarget = { workspaceId: number; runId: string };
let target: ApprovalTarget | null = null;
const listeners = new Set<() => void>();
export function takeApprovalTarget() {
  const next = target;
  target = null;
  return next;
}
export function subscribeApprovalTarget(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function openApprovalTarget(gate: Gate) {
  target = { workspaceId: gate.workspace_id, runId: gate.run_id };
  for (const listener of listeners) listener();
}
async function activeApprovals(signal: AbortSignal): Promise<Gate[]> {
  const response = await fetch("/api/workflows/active-approvals", { headers: authHeaders(), signal });
  if (!response.ok) throw new Error("Could not read active approvals.");
  return response.json() as Promise<Gate[]>;
}

export function WorkflowApprovalNotifications({ connected }: { connected: boolean }) {
  const { open } = useOverlays();
  // Poll only while connected. The lightweight endpoint does not initialize
  // the detached engine or open the database when there is no active run.
  useEffect(() => {
    if (!connected) return;
    const controller = new AbortController();
    const shown = new Map<string, string>();
    let timer: number | undefined;
    async function poll() {
      try {
        const gates = await activeApprovals(controller.signal);
        if (controller.signal.aborted || !Array.isArray(gates)) return;
        const pending = new Set<string>();
        for (const gate of gates) {
          const key = JSON.stringify([gate.workspace_id, gate.run_id, gate.step_id]);
          pending.add(key);
          if (!shown.has(key)) {
            shown.set(key, toast.show({
              scope: "workflow-library", variant: "warning", title: gate.workflow + " is waiting for you",
              details: gate.message, action: {
                label: "Review run", onClick: () => { openApprovalTarget(gate); open("workflow-library", "dialog"); },
              },
            }));
          }
        }
        for (const [key, id] of shown) {
          if (!pending.has(key)) { toast.dismiss(id); shown.delete(key); }
        }
      } catch { /* A disconnected client must not fabricate a gate. */ }
      finally {
        if (!controller.signal.aborted) timer = window.setTimeout(() => { void poll(); }, 1500);
      }
    }
    void poll();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
      for (const id of shown.values()) toast.dismiss(id);
    };
  }, [connected, open]);
  return null;
}

