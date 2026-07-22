import { describe, expect, it, vi } from "vitest";
import { SceneStore, initialSceneState, scaleDragPosition } from "./sceneStore";
import type { WsTransport } from "../../lib/ws/transport";

type StateListener = (payload: Record<string, unknown>) => void;

function makeFakeTransport() {
  const listeners = new Map<string, StateListener>();
  const intents: Array<{ topic: string; intent: string; args: unknown[] }> = [];
  const transport = {
    subscribe: vi.fn((topic: string, listener: StateListener) => {
      listeners.set(topic, listener);
      return () => listeners.delete(topic);
    }),
    intent: vi.fn((topic: string, intent: string, args: unknown[] = []) => {
      intents.push({ topic, intent, args });
    }),
  } as unknown as WsTransport;
  return { transport, listeners, intents };
}

function validScenePayload(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: 1,
    minCompatibleSchemaVersion: 1,
    revision: 3,
    nodes: [
      { id: "n0", x: 1, y: 2, title: "A", kind: "placeholder", content: "", isUser: false, isCollapsed: false },
    ],
    edges: [],
    pins: [],
    snapToGrid: true,
    dragFactor: 0.5,
    fontFamily: "Segoe UI",
    fontSizePt: 9,
    fontColor: "#F0F0F0",
    ...overrides,
  };
}

describe("SceneStore", () => {
  it("accepts a VALID scene snapshot and notifies subscribers", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    const seen = vi.fn();
    store.subscribe(seen);

    listeners.get("scene")!(validScenePayload());
    expect(seen).toHaveBeenCalledTimes(1);
    expect(store.getScene().nodes[0].title).toBe("A");
    expect(store.getScene().dragFactor).toBe(0.5);
  });

  it("REJECTS a malformed snapshot and keeps the previous state", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    listeners.get("scene")!({ revision: "not-a-scene" });
    expect(store.getScene()).toEqual(initialSceneState);
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("routes grid snapshots through the grid validator", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    listeners.get("grid-control")!({
      schemaVersion: 1,
      minCompatibleSchemaVersion: 1,
      revision: 1,
      gridSize: 50,
      gridOpacityPercent: 80,
      gridStyle: "Lines",
      gridColor: "#404040",
      sizePresets: [10, 20, 50, 100],
      stylePresets: ["Dots", "Lines", "Cross"],
      colorPresets: [],
    });
    expect(store.getGrid().gridSize).toBe(50);
    expect(store.getGrid().gridStyle).toBe("Lines");
  });

  it("sends intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addNode(10, 20, "hello");
    store.moveNode("n1", 3, 4);
    store.connectNodes("n1", "n2");
    store.addPin("P", 5, 6, "note");
    store.setSnapToGrid(true);
    store.setDragFactor(0.25);
    expect(intents).toEqual([
      { topic: "scene", intent: "addNode", args: [10, 20, "hello"] },
      { topic: "scene", intent: "moveNode", args: ["n1", 3, 4] },
      { topic: "scene", intent: "connectNodes", args: ["n1", "n2"] },
      { topic: "scene", intent: "addPin", args: ["P", 5, 6, "note"] },
      { topic: "scene", intent: "setSnapToGrid", args: [true] },
      { topic: "scene", intent: "setDragFactor", args: [0.25] },
    ]);
  });

  it("sends chat-node intents with the backend's registered names and shapes", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.addChatNode(10, 20, "hello", true);
    store.addChatNode(30, 40, "hi back", false, "n1");
    store.setChatCollapsed("n1", true);
    store.deleteChatNode("n1");
    expect(intents).toEqual([
      { topic: "scene", intent: "addChatNode", args: [10, 20, "hello", true] },
      { topic: "scene", intent: "addChatNode", args: [30, 40, "hi back", false, "n1"] },
      { topic: "scene", intent: "setChatCollapsed", args: ["n1", true] },
      { topic: "scene", intent: "deleteChatNode", args: ["n1"] },
    ]);
  });

  it("suppresses empty removal intents", () => {
    const { transport, intents } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.removeNodes([]);
    store.removeEdges([]);
    expect(intents).toEqual([]);
  });

  it("dispose() unsubscribes every topic", () => {
    const { transport, listeners } = makeFakeTransport();
    const store = new SceneStore(transport);
    store.connect();
    expect(listeners.size).toBe(4);
    store.dispose();
    expect(listeners.size).toBe(0);
  });
});

describe("scaleDragPosition (the drag-speed contract)", () => {
  it("factor 1 leaves motion unscaled", () => {
    expect(scaleDragPosition({ x: 0, y: 0 }, { x: 100, y: 40 }, 1)).toEqual({ x: 100, y: 40 });
  });

  it("factor 0.5 halves the delta from the drag start", () => {
    expect(scaleDragPosition({ x: 10, y: 10 }, { x: 110, y: 50 }, 0.5)).toEqual({ x: 60, y: 30 });
  });

  it("scales relative to the start, not the origin", () => {
    expect(scaleDragPosition({ x: -20, y: 8 }, { x: -20, y: 8 }, 0.25)).toEqual({ x: -20, y: 8 });
  });
});
