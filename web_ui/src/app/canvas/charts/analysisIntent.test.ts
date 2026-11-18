import { afterEach, describe, expect, it, vi } from "vitest";
import { saveChartAnalysis } from "./analysisIntent";
import type { ChartAnalysis } from "../chartSpec";

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  closed = false;
  sent = "";
  constructor(public url: string) { FakeSocket.instances.push(this); }
  open() { this.onopen?.(new Event("open")); }
  send(content: string) { this.sent = content; }
  close() { this.closed = true; }
  respond(value: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(value) }));
  }
}

const analysis: ChartAnalysis = {
  lockX: true, lockY: false, xRange: [1, 100],
  annotations: [{ kind: "label", x: 10, y: 3, text: "Peak" }],
};

describe("saveChartAnalysis", () => {
  afterEach(() => { vi.unstubAllGlobals(); FakeSocket.instances = []; });

  it("sends an authenticated, undoable document intent and awaits its acknowledgement", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const pending = saveChartAnalysis("chart-1", analysis);
    const ws = FakeSocket.instances[0];
    expect(ws.url).toContain("/ws?session=default");
    ws.open();
    expect(JSON.parse(ws.sent)).toEqual({
      kind: "intent", topic: "scene", intent: "setChartAnalysis",
      args: ["chart-1", analysis], id: "chart-analysis",
    });
    ws.respond({ kind: "result", id: "chart-analysis", value: analysis });
    await expect(pending).resolves.toBeUndefined();
    expect(ws.closed).toBe(true);
  });

  it("does not claim saved on server errors or lost connections", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const failed = saveChartAnalysis("chart-1", analysis);
    const ws = FakeSocket.instances[0];
    ws.open();
    ws.respond({ kind: "error", id: "chart-analysis", error: "invalid region" });
    await expect(failed).rejects.toThrow("invalid region");

    const closed = saveChartAnalysis("chart-2", analysis);
    FakeSocket.instances[1].onclose?.(new CloseEvent("close"));
    await expect(closed).rejects.toThrow("connection closed");

    await expect(saveChartAnalysis("", analysis)).rejects.toThrow("node ID");
  });
});
