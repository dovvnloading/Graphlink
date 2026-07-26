import { describe, expect, it, vi } from "vitest";

// Mocked so the export-canvas-png test can assert on the WIRING (that run()
// actually invokes it, with the right instance and token) rather than on
// "run() didn't throw" - which, since run() `void`s a call to an async
// function, held for every possible implementation including a no-op.
const exportCanvasAsPngMock = vi.fn();
vi.mock("../canvas/exportCanvasPng", () => ({
  exportCanvasAsPng: (...args: unknown[]) => exportCanvasAsPngMock(...args),
}));

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
    addNote: vi.fn(),
    createFrame: vi.fn(),
    createContainer: vi.fn(),
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

  it("add-note is always enabled and calls addNote with the viewport center", () => {
    const store = makeStore();
    // @ts-expect-error - test double
    const commands = buildCommands(store, makeRf(), makeOverlays());
    const addNote = commands.find((c) => c.id === "add-note")!;
    expect(addNote.enabled()).toBe(true);
    addNote.run();
    expect(store.addNote).toHaveBeenCalledTimes(1);
  });

  it("create-frame/create-container are disabled with 0 or 1 selected nodes", () => {
    const store = makeStore([{ id: "n0", x: 0, y: 0, title: "A", kind: "placeholder" }]);
    // @ts-expect-error - test double
    const noneSelected = buildCommands(store, makeRf([{ id: "n0", selected: false }]), makeOverlays());
    expect(noneSelected.find((c) => c.id === "create-frame")!.enabled()).toBe(false);
    expect(noneSelected.find((c) => c.id === "create-container")!.enabled()).toBe(false);

    // @ts-expect-error - test double
    const oneSelected = buildCommands(store, makeRf([{ id: "n0", selected: true }]), makeOverlays());
    expect(oneSelected.find((c) => c.id === "create-frame")!.enabled()).toBe(false);
    expect(oneSelected.find((c) => c.id === "create-container")!.enabled()).toBe(false);
  });

  it("create-frame is enabled with 2+ selected nodes and calls createFrame with their ids", () => {
    const store = makeStore();
    const rf = makeRf([
      { id: "n0", selected: true },
      { id: "n1", selected: true },
      { id: "n2", selected: false },
    ]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, rf, makeOverlays());
    const createFrame = commands.find((c) => c.id === "create-frame")!;
    expect(createFrame.enabled()).toBe(true);
    createFrame.run();
    expect(store.createFrame).toHaveBeenCalledWith(["n0", "n1"]);
  });

  it("create-container is enabled with 2+ selected nodes and calls createContainer with their ids", () => {
    const store = makeStore();
    const rf = makeRf([
      { id: "n0", selected: true },
      { id: "n1", selected: true },
    ]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, rf, makeOverlays());
    const createContainer = commands.find((c) => c.id === "create-container")!;
    expect(createContainer.enabled()).toBe(true);
    createContainer.run();
    expect(store.createContainer).toHaveBeenCalledWith(["n0", "n1"]);
  });

  it("export-canvas-png is disabled with an empty scene and enabled once nodes exist (R6.8)", () => {
    // exportCanvasAsPng itself is fully unit-tested in exportCanvasPng.test.ts
    // (including the html-to-image mock) - this confirms the command is wired
    // to the same hasNodes gate fit-all/organize-nodes already use.
    // @ts-expect-error - test double
    const empty = buildCommands(makeStore([]), makeRf(), makeOverlays());
    const exportEmpty = empty.find((c) => c.id === "export-canvas-png")!;
    expect(exportEmpty.name).toBe("Export Canvas as PNG");
    expect(exportEmpty.enabled()).toBe(false);

    const nodeList = [{ id: "n0", x: 0, y: 0, title: "A", kind: "placeholder" }];
    // @ts-expect-error - test double
    const withNodes = buildCommands(makeStore(nodeList), makeRf([{ id: "n0" }]), makeOverlays());
    const exportWithNodes = withNodes.find((c) => c.id === "export-canvas-png")!;
    expect(exportWithNodes.enabled()).toBe(true);
  });

  it("export-canvas-png's run() actually invokes exportCanvasAsPng with the instance and background token", () => {
    // Audit finding: this used to assert `expect(() => run()).not.toThrow()`,
    // which is vacuous - run() `void`s a call to an async function, and an
    // async function never throws synchronously, so it held for EVERY
    // implementation including `run: () => {}` (verified by mutation: the
    // command could be wired to nothing at all and the suite stayed green).
    exportCanvasAsPngMock.mockClear();
    const rf = makeRf([{ id: "n0" }]);
    const nodeList = [{ id: "n0", x: 0, y: 0, title: "A", kind: "placeholder" }];
    // @ts-expect-error - test double
    const commands = buildCommands(makeStore(nodeList), rf, makeOverlays());

    commands.find((c) => c.id === "export-canvas-png")!.run();

    expect(exportCanvasAsPngMock).toHaveBeenCalledOnce();
    expect(exportCanvasAsPngMock).toHaveBeenCalledWith(rf, "--gl-surface-window");
  });
});
