import { useEffect } from "react";
import { authHeaders } from "../../lib/auth/token";
import { useOverlays } from "../overlays/overlays";
import { toast } from "../ui/Toast";

type Gate = {
  workspace_id: number; run_id: string; workflow: string; step_id: string; message: string;
};
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
              variant: "warning", title: "Workflow waiting for approval: " + gate.workflow,
              details: gate.message, action: {
                label: "Open run history", onClick: () => open("run-history", "dialog"),
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

