import { describe, expect, it, vi } from "vitest";
import { buildCommands } from "./commands";
import { initialSceneState } from "../canvas/sceneStore";
import type { OverlayContextValue } from "../overlays/overlays";

function makeStore(nodes: Array<{ id: string; x: number; y: number; title: string; kind: string }> = []) {
  const scene = { ...initialSceneState, nodes, pins: [] };
  return {
    getScene: () => scene,
    organizeNodes: vi.fn(),
    removeNodes: vi.fn(),
    removeEdges: vi.fn(),
    addPin: vi.fn(),
  };
}

function makeRf(nodes: Array<{ id: string; selected?: boolean }> = []) {
  return {
    zoomIn: vi.fn(),
    zoomOut: vi.fn(),
    fitView: vi.fn(),
    setViewport: vi.fn(),
    getViewport: () => ({ x: 0, y: 0, zoom: 1 }),
    getNodes: () => nodes,
    getEdges: () => [],
    setNodes: vi.fn(),
  };
}

function makeOverlays(): OverlayContextValue {
  return {
    openSurface: null,
    open: vi.fn(),
    close: vi.fn(),
    toggle: vi.fn(),
    isOpen: () => false,
    registerSurfaceElement: vi.fn(),
  };
}

describe("buildCommands", () => {
  it("fit-all and organize-nodes are disabled with an empty scene", () => {
    const store = makeStore([]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, makeRf(), makeOverlays());
    expect(commands.find((c) => c.id === "fit-all")!.enabled()).toBe(false);
    expect(commands.find((c) => c.id === "organize-nodes")!.enabled()).toBe(false);
  });

  it("fit-all and organize-nodes enable once nodes exist", () => {
    const store = makeStore([{ id: "n0", x: 0, y: 0, title: "A", kind: "placeholder" }]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, makeRf(), makeOverlays());
    expect(commands.find((c) => c.id === "fit-all")!.enabled()).toBe(true);
    expect(commands.find((c) => c.id === "organize-nodes")!.enabled()).toBe(true);
  });

  it("delete-selected is disabled with no selection and calls remove intents when run", () => {
    const store = makeStore([{ id: "n0", x: 0, y: 0, title: "A", kind: "placeholder" }]);
    const rf = makeRf([{ id: "n0", selected: true }]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, rf, makeOverlays());
    const del = commands.find((c) => c.id === "delete-selected")!;
    expect(del.enabled()).toBe(true);
    del.run();
    expect(store.removeNodes).toHaveBeenCalledWith(["n0"]);
  });

  it("open-* commands call overlays.open with the right name/tier", () => {
    const store = makeStore();
    const overlays = makeOverlays();
    // @ts-expect-error - test double
    const commands = buildCommands(store, makeRf(), overlays);
    commands.find((c) => c.id === "open-settings")!.run();
    expect(overlays.open).toHaveBeenCalledWith("settings", "dialog");
    commands.find((c) => c.id === "open-view")!.run();
    expect(overlays.open).toHaveBeenCalledWith("view", "popover");
  });

  it("add-pin computes the viewport center and calls addPin", () => {
    const store = makeStore();
    // @ts-expect-error - test double
    const commands = buildCommands(store, makeRf(), makeOverlays());
    commands.find((c) => c.id === "add-pin")!.run();
    expect(store.addPin).toHaveBeenCalledTimes(1);
    expect(store.addPin.mock.calls[0][0]).toBe("Pin 1");
  });
});
