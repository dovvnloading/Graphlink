import { afterEach, describe, expect, it, vi } from "vitest";
import { saveChartAnalysis, saveCompositeDesign, branchCompositeSelection } from "./analysisIntent";
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


describe("saveCompositeDesign", () => {
  afterEach(() => { vi.unstubAllGlobals(); FakeSocket.instances = []; });

  it("sends view-only bindings and an expected design over an authenticated intent", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const design = { layers: [{ mark: "point" as const, x: "time", y: "response" }], facetBy: null };
    const expected = { layers: [{ mark: "line" as const, x: "time", y: "response" }], facetBy: "group" };
    const pending = saveCompositeDesign("chart-1", design, expected);
    const ws = FakeSocket.instances[0];
    ws.open();
    expect(JSON.parse(ws.sent)).toEqual({
      kind: "intent", topic: "scene", intent: "setCompositeDesign",
      args: ["chart-1", design, expected], id: "chart-composite-design",
    });
    ws.respond({ kind: "result", id: "chart-composite-design", value: {} });
    await expect(pending).resolves.toBeUndefined();
    expect(ws.closed).toBe(true);
  });

  it("surfaces stale document conflicts rather than claiming a successful save", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const design = { layers: [{ mark: "point" as const, x: "time", y: "response" }] };
    const pending = saveCompositeDesign("chart-1", design, design);
    const ws = FakeSocket.instances[0];
    ws.open();
    ws.respond({ kind: "error", id: "chart-composite-design",
      error: "Composite design changed; review current figure" });
    await expect(pending).rejects.toThrow(/design changed/);
  });
});

describe("branchCompositeSelection", () => {
  afterEach(() => { vi.unstubAllGlobals(); FakeSocket.instances = []; });

  const expected = {
    numbers: { x: [0, 1, 2, 3], y: [3, 4, 5, 6] },
    categories: { group: ["A", "A", "B", "B"] },
    layers: [{ mark: "point" as const, x: "x", y: "y" }],
  };

  it("sends only source indices and the checked original column snapshot to the authenticated scene", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const pending = branchCompositeSelection("chart-1", [0, 2], expected);
    const ws = FakeSocket.instances[0];
    ws.open();
    expect(JSON.parse(ws.sent)).toEqual({
      kind: "intent", topic: "scene", intent: "branchCompositeSelection",
      args: ["chart-1", [0, 2], expected], id: "chart-branch-selection",
    });
    ws.respond({ kind: "result", id: "chart-branch-selection",
      value: { chartNodeId: "child-1", selected: 2, sourceCount: 4 } });
    await expect(pending).resolves.toBeUndefined();
    expect(ws.closed).toBe(true);
  });

  it("propagates a stale-data validation error without claiming a new chart exists", async () => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const pending = branchCompositeSelection("chart-1", [0, 1], expected);
    const ws = FakeSocket.instances[0];
    ws.open();
    ws.respond({ kind: "error", id: "chart-branch-selection",
      error: "Composite source data changed; review before branching" });
    await expect(pending).rejects.toThrow(/data changed/);
    expect(ws.closed).toBe(true);
  });
});
