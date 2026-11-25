/** Undoable chart document edits over the authenticated scene WebSocket. */
import { defaultWsUrl } from "../../../lib/ws/transport";
import type { ChartAnalysis } from "../chartSpec";
import type { CompositeDesign } from "./compositeDesign";

function saveChartIntent(nodeId: string, intent: string, args: unknown[], id: string): Promise<void> {
  if (!nodeId) return Promise.reject(new Error("Chart node ID is required"));
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(defaultWsUrl());
    let settled = false;
    const timeout = setTimeout(() => finish(new Error("Saving chart changes timed out")), 30_000);
    function finish(error?: Error) {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      ws.close();
      if (error) reject(error);
      else resolve();
    }
    ws.onopen = () => ws.send(JSON.stringify({
      kind: "intent", topic: "scene", intent, args, id,
    }));
    ws.onmessage = ({ data }) => {
      try {
        const message = JSON.parse(String(data));
        if (message?.id !== id) return;
        if (message.kind === "error") finish(new Error(message.error || "Could not save chart changes"));
        else if (message.kind === "result") finish();
      } catch {
        finish(new Error("Invalid chart response"));
      }
    };
    ws.onerror = () => finish(new Error("Chart analysis connection failed"));
    ws.onclose = () => finish(new Error("Chart analysis connection closed"));
  });
}

export function saveChartAnalysis(nodeId: string, analysis: ChartAnalysis): Promise<void> {
  return saveChartIntent(nodeId, "setChartAnalysis", [nodeId, analysis], "chart-analysis");
}

export function saveCompositeDesign(nodeId: string, design: CompositeDesign, expected: CompositeDesign): Promise<void> {
  return saveChartIntent(nodeId, "setCompositeDesign", [nodeId, design, expected], "chart-composite-design");
}
