/** Undoable chart document edits over the existing authenticated scene
 * WebSocket intent channel. No new HTTP endpoint or token-bearing URL. */
import { defaultWsUrl } from "../../../lib/ws/transport";
import type { ChartAnalysis } from "../chartSpec";

export function saveChartAnalysis(nodeId: string, analysis: ChartAnalysis): Promise<void> {
  if (!nodeId) return Promise.reject(new Error("Chart node ID is required"));
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(defaultWsUrl());
    let settled = false;
    const timeout = setTimeout(() => finish(new Error("Saving chart analysis timed out")), 30_000);

    function finish(error?: Error) {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      ws.close();
      if (error) reject(error);
      else resolve();
    }
    ws.onopen = () => ws.send(JSON.stringify({
      kind: "intent", topic: "scene", intent: "setChartAnalysis",
      args: [nodeId, analysis], id: "chart-analysis",
    }));
    ws.onmessage = ({ data }) => {
      try {
        const message = JSON.parse(String(data));
        if (message?.id !== "chart-analysis") return;
        if (message.kind === "error") finish(new Error(message.error || "Could not save chart analysis"));
        else if (message.kind === "result") finish();
      } catch {
        finish(new Error("Invalid chart analysis response"));
      }
    };
    ws.onerror = () => finish(new Error("Chart analysis connection failed"));
    ws.onclose = () => finish(new Error("Chart analysis connection closed"));
  });
}
