import { describe, expect, it, vi } from "vitest";

// Mocked so the export-canvas-png test can assert on the WIRING (that run()
// actually invokes it, with the right instance and token) rather than on
// "run() didn't throw" - which, since run() `void`s a call to an async
// function, held for every possible implementation including a no-op.
const exportCanvasAsPngMock = vi.fn();
vi.mock("../canvas/exportCanvasPng", () => ({
  exportCanvasAsPng: (...args: unknown[]) => exportCanvasAsPngMock(...args),
}));

import { buildCommands, requestNewChat } from "./commands";
import { initialSceneState } from "../canvas/sceneStore";
import type { OverlayContextValue } from "../overlays/overlays";

function makeStore(
  nodes: Array<{ id: string; x: number; y: number; title: string; kind: string }> = [],
  hasSavedChat = false,
) {
  const scene = { ...initialSceneState, nodes, pins: [], hasSavedChat };
  return {
    getScene: () => scene,
    organizeNodes: vi.fn(),
    removeNodes: vi.fn(),
    removeEdges: vi.fn(),
    addPin: vi.fn(),
    addNote: vi.fn(),
    createFrame: vi.fn(),
    createContainer: vi.fn(),
    newChat: vi.fn(),
    saveChat: vi.fn(),
    collapseAllNodes: vi.fn(),
    expandAllNodes: vi.fn(),
    // ADR-021 stage 21.5: the two branch agents' store entry points.
    compareBranches: vi.fn(),
    setSynthesizeTargetNodeIds: vi.fn(),
    showInfoNotification: vi.fn(),
  };
}

function makeRf(nodes: Array<{ id: string; selected?: boolean; type?: string }> = []) {
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
    // ADR-020 stage 20.4: the Global Search palette entry.
    commands.find((c) => c.id === "open-global-search")!.run();
    expect(overlays.open).toHaveBeenCalledWith("global-search", "dialog");
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

  it("collapse-all-nodes/expand-all-nodes are disabled when the scene has zero chat/conversation/html nodes, even with other node kinds present", () => {
    const store = makeStore([
      { id: "n0", x: 0, y: 0, title: "A", kind: "code" },
      { id: "n1", x: 0, y: 0, title: "B", kind: "frame" },
    ]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, makeRf(), makeOverlays());
    expect(commands.find((c) => c.id === "collapse-all-nodes")!.enabled()).toBe(false);
    expect(commands.find((c) => c.id === "expand-all-nodes")!.enabled()).toBe(false);
  });

  it("collapse-all-nodes/expand-all-nodes enable once a chat node exists and each run() calls the matching store method once", () => {
    const store = makeStore([{ id: "n0", x: 0, y: 0, title: "A", kind: "chat" }]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, makeRf(), makeOverlays());

    const collapseAll = commands.find((c) => c.id === "collapse-all-nodes")!;
    expect(collapseAll.enabled()).toBe(true);
    collapseAll.run();
    expect(store.collapseAllNodes).toHaveBeenCalledTimes(1);

    const expandAll = commands.find((c) => c.id === "expand-all-nodes")!;
    expect(expandAll.enabled()).toBe(true);
    expandAll.run();
    expect(store.expandAllNodes).toHaveBeenCalledTimes(1);
  });

  it("new-chat is always enabled and calls store.newChat (R7.5a)", () => {
    // Empty scene -> R7.5c's confirm is skipped entirely, so this still
    // reaches newChat without any modal.
    const store = makeStore();
    // @ts-expect-error - test double
    const commands = buildCommands(store, makeRf(), makeOverlays());
    const newChat = commands.find((c) => c.id === "new-chat")!;
    expect(newChat.enabled()).toBe(true);
    newChat.run();
    expect(store.newChat).toHaveBeenCalledTimes(1);
  });

  it("save-chat is always enabled and calls store.saveChat (R7.5c)", () => {
    const store = makeStore();
    // @ts-expect-error - test double
    const commands = buildCommands(store, makeRf(), makeOverlays());
    const save = commands.find((c) => c.id === "save-chat")!;
    expect(save.name).toBe("Save Chat");
    expect(save.enabled()).toBe(true);
    save.run();
    expect(store.saveChat).toHaveBeenCalledTimes(1);
  });

  it("focus-selection is disabled with no selection and calls fitView scoped to the selected ids when run (R7.5a)", () => {
    const store = makeStore([
      { id: "n0", x: 0, y: 0, title: "A", kind: "placeholder" },
      { id: "n1", x: 0, y: 0, title: "B", kind: "placeholder" },
    ]);
    const noneSelected = makeRf([{ id: "n0" }, { id: "n1" }]);
    // @ts-expect-error - test double
    const disabled = buildCommands(store, noneSelected, makeOverlays());
    expect(disabled.find((c) => c.id === "focus-selection")!.enabled()).toBe(false);

    const rf = makeRf([
      { id: "n0", selected: true },
      { id: "n1", selected: false },
    ]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, rf, makeOverlays());
    const focusSelection = commands.find((c) => c.id === "focus-selection")!;
    expect(focusSelection.enabled()).toBe(true);
    focusSelection.run();
    expect(rf.fitView).toHaveBeenCalledWith({ nodes: [{ id: "n0" }], duration: 200 });
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

  it("export-canvas-png's run() actually invokes exportCanvasAsPng with the instance and background token", async () => {
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

    // run() now reaches exportCanvasAsPng through a dynamic import, so the
    // call lands a microtask later. waitFor keeps the assertion real - it
    // still fails if the command is wired to nothing, which is the whole
    // point of the note above.
    await vi.waitFor(() => expect(exportCanvasAsPngMock).toHaveBeenCalledOnce());
    // ADR-011 stage 11.2: a 3rd arg now threads store.setExportInProgress
    // through so exportCanvasAsPng can suspend onlyRenderVisibleElements for
    // the capture's duration - see that module's own doc. Asserted as
    // "any function" rather than a specific reference since it's a fresh
    // arrow closure over `store` on every run(), not a stable callback.
    expect(exportCanvasAsPngMock).toHaveBeenCalledWith(rf, "--gl-surface-window", expect.any(Function));
  });

  // ADR-021 stage 21.5: the two branch agents were reachable ONLY by
  // keyboard shortcut - absent from this palette and every menu.
  it("registers compare-branches and synthesize-branches, gated on a 2+ selection", () => {
    const store = makeStore([
      { id: "n0", x: 0, y: 0, title: "A", kind: "chat" },
      { id: "n1", x: 0, y: 0, title: "B", kind: "chat" },
    ]);
    // @ts-expect-error - test double
    const oneSelected = buildCommands(store, makeRf([{ id: "n0", selected: true }]), makeOverlays());
    expect(oneSelected.find((c) => c.id === "compare-branches")!.enabled()).toBe(false);
    expect(oneSelected.find((c) => c.id === "synthesize-branches")!.enabled()).toBe(false);

    const rf = makeRf([
      { id: "n0", selected: true },
      { id: "n1", selected: true },
    ]);
    // @ts-expect-error - test double
    const twoSelected = buildCommands(store, rf, makeOverlays());
    expect(twoSelected.find((c) => c.id === "compare-branches")!.enabled()).toBe(true);
    expect(twoSelected.find((c) => c.id === "synthesize-branches")!.enabled()).toBe(true);
  });

  it("compare-branches forwards the selected ids to the store", () => {
    const store = makeStore([
      { id: "n0", x: 0, y: 0, title: "A", kind: "chat" },
      { id: "n1", x: 0, y: 0, title: "B", kind: "chat" },
    ]);
    const rf = makeRf([
      { id: "n0", selected: true, type: "chat" },
      { id: "n1", selected: true, type: "chat" },
    ]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, rf, makeOverlays());

    commands.find((c) => c.id === "compare-branches")!.run();

    expect(store.compareBranches).toHaveBeenCalledWith(["n0", "n1"]);
  });

  it("synthesize-branches STAGES the selection rather than firing immediately", () => {
    // The palette and the keyboard shortcut share one implementation
    // (canvas/branchActions.ts) precisely so this non-obvious behavior -
    // and its guards - cannot drift between the two surfaces.
    const store = makeStore([
      { id: "n0", x: 0, y: 0, title: "A", kind: "chat" },
      { id: "n1", x: 0, y: 0, title: "B", kind: "chat" },
    ]);
    const rf = makeRf([
      { id: "n0", selected: true, type: "chat" },
      { id: "n1", selected: true, type: "chat" },
    ]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, rf, makeOverlays());

    commands.find((c) => c.id === "synthesize-branches")!.run();

    expect(store.setSynthesizeTargetNodeIds).toHaveBeenCalledWith(["n0", "n1"]);
  });

  it("synthesize-branches refuses a non-chat selection with an explanation", () => {
    const store = makeStore([
      { id: "n0", x: 0, y: 0, title: "A", kind: "chat" },
      { id: "n1", x: 0, y: 0, title: "B", kind: "code" },
    ]);
    const rf = makeRf([
      { id: "n0", selected: true, type: "chat" },
      { id: "n1", selected: true, type: "code" },
    ]);
    // @ts-expect-error - test double
    const commands = buildCommands(store, rf, makeOverlays());

    commands.find((c) => c.id === "synthesize-branches")!.run();

    expect(store.setSynthesizeTargetNodeIds).not.toHaveBeenCalled();
    expect(store.showInfoNotification).toHaveBeenCalled();
  });
});

// R7.5c: the New Chat confirm, restored from legacy's blocking QMessageBox.
describe("requestNewChat", () => {
  it("skips the confirm on an empty, never-saved canvas - nothing to lose", () => {
    const store = makeStore([]);
    const confirmFn = vi.fn(() => true);
    // @ts-expect-error - test double
    requestNewChat(store, confirmFn);
    expect(confirmFn).not.toHaveBeenCalled();
    expect(store.newChat).toHaveBeenCalledTimes(1);
  });

  it("still asks when the canvas is empty but the scene IS a saved chat", () => {
    // Legacy's skip needs BOTH halves (graphlink_window.py:1429). Emptying a
    // loaded chat and hitting Ctrl+T must not silently detach it: newChat()
    // drops current_chat_id, so the next Save would INSERT a new row instead
    // of updating the one the user believes they are editing.
    const store = makeStore([], true);
    const confirmFn = vi.fn(() => false);
    // @ts-expect-error - test double
    requestNewChat(store, confirmFn);
    expect(confirmFn).toHaveBeenCalledOnce();
    expect(store.newChat).not.toHaveBeenCalled();
  });

  it("asks before discarding a canvas that has content, and proceeds on yes", () => {
    const store = makeStore([{ id: "n0", x: 0, y: 0, title: "A", kind: "chat" }]);
    const confirmFn = vi.fn(() => true);
    // @ts-expect-error - test double
    requestNewChat(store, confirmFn);
    expect(confirmFn).toHaveBeenCalledWith("Start a new chat? Any unsaved changes will be lost.");
    expect(store.newChat).toHaveBeenCalledTimes(1);
  });

  it("does NOT clear the canvas when the confirm is declined", () => {
    const store = makeStore([{ id: "n0", x: 0, y: 0, title: "A", kind: "chat" }]);
    const confirmFn = vi.fn(() => false);
    // @ts-expect-error - test double
    requestNewChat(store, confirmFn);
    expect(confirmFn).toHaveBeenCalledOnce();
    expect(store.newChat).not.toHaveBeenCalled();
  });
});
