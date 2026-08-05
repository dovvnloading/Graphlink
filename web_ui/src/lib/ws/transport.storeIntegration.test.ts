/**
 * ADR-003 stage 3.1 review-fix (findings I/J): every other store test wires
 * SceneStore/ComposerStore to a stub transport object whose own `fireIntent`
 * mock is a synchronous push into a recorded-calls array - real, but it never
 * actually exercises WsTransport.fireIntent()'s own id-tracking/error-catch
 * machinery (that lives entirely in transport.test.ts, in isolation from any
 * store). This file closes that gap: a REAL WsTransport (the same FakeSocket
 * harness transport.test.ts uses) wired to a REAL store instance, driving one
 * representative mutating call through the full store -> transport -> wire
 * -> server-error-reply -> notification/showError chain end to end.
 */
import { describe, expect, it } from "vitest";
import { WsTransport } from "./transport";
import { SceneStore } from "../../app/canvas/sceneStore";
import { ComposerStore } from "../../app/chrome/composerStore";

class FakeSocket {
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(public url: string) {}

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.onclose?.();
  }

  open() {
    this.onopen?.();
  }

  receive(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  lastSent(): Record<string, unknown> {
    return JSON.parse(this.sent[this.sent.length - 1]);
  }
}

function connectRealTransport() {
  let socket: FakeSocket | null = null;
  const transport = new WsTransport("ws://test/ws", {
    webSocketFactory: (url) => {
      socket = new FakeSocket(url);
      return socket;
    },
  });
  transport.connect();
  socket!.open();
  return { transport, socket: socket! };
}

describe("real WsTransport wired to a real store (end-to-end fireIntent -> showError)", () => {
  it("SceneStore.collapseAllNodes: a real {kind:error} reply produces a real notification/showError intent on the wire", async () => {
    const { transport, socket } = connectRealTransport();
    const store = new SceneStore(transport);

    store.collapseAllNodes();
    const sent = socket.lastSent();
    expect(sent).toMatchObject({ kind: "intent", topic: "scene", intent: "collapseAllNodes", args: [] });

    socket.receive({ kind: "error", id: sent.id, error: "collapse failed" });
    await Promise.resolve();
    await Promise.resolve();

    expect(socket.lastSent()).toEqual({
      kind: "intent",
      topic: "notification",
      intent: "showError",
      args: ["collapse failed"],
    });
  });

  it("ComposerStore.selectModel: a real {kind:error} reply produces a real notification/showError intent on the wire", async () => {
    const { transport, socket } = connectRealTransport();
    const store = new ComposerStore(transport);

    store.selectModel("llama3");
    const sent = socket.lastSent();
    expect(sent).toMatchObject({ kind: "intent", topic: "app-composer", intent: "selectModel", args: ["llama3"] });

    socket.receive({ kind: "error", id: sent.id, error: "unknown model" });
    await Promise.resolve();
    await Promise.resolve();

    expect(socket.lastSent()).toEqual({
      kind: "intent",
      topic: "notification",
      intent: "showError",
      args: ["unknown model"],
    });
  });

  it("a successful reply does NOT produce a notification/showError intent", async () => {
    const { transport, socket } = connectRealTransport();
    const store = new SceneStore(transport);

    store.collapseAllNodes();
    const sent = socket.lastSent();
    socket.receive({ kind: "result", id: sent.id, value: null });
    await Promise.resolve();
    await Promise.resolve();

    expect(socket.sent.map((raw) => JSON.parse(raw))).not.toContainEqual(
      expect.objectContaining({ intent: "showError" }),
    );
  });
});

// ADR-003 stage 3.5 review-fix: a 4-lens adversarial review found this exact
// class of gap again - every layer's OWN test (transport.test.ts,
// sceneStore.test.ts, SceneCanvas.test.tsx) mocks the boundary immediately
// below the code it exercises, so nothing proved the real WsTransport ->
// real SceneStore wiring (the topic-key match, the field shapes) actually
// works end to end. sceneStore.test.ts's own makeFakeTransport explicitly
// bypasses WsTransport.handleMessage/checkSchemaCompatibility entirely -
// this file's whole reason to exist, per its own header doc above, is
// closing exactly this gap for fireIntent/showError; it was not extended to
// cover version rejection when that stage landed.
describe("real WsTransport wired to a real SceneStore (end-to-end version rejection)", () => {
  it("an incompatible scene frame reaches SceneStore.getSceneBlockingRejection() through the REAL transport pipeline", () => {
    const { transport, socket } = connectRealTransport();
    const store = new SceneStore(transport);
    store.connect();
    expect(store.getSceneBlockingRejection()).toBeNull();

    socket.receive({
      kind: "state",
      topic: "scene",
      payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [], edges: [], pins: [] },
    });

    const rejection = store.getSceneBlockingRejection();
    expect(rejection).toMatchObject({ kind: "version" });
    expect(rejection!.reason).toContain("99");
    // And the incompatible payload never reached `scene` itself.
    expect(store.getScene().nodes).toEqual([]);
  });

  it("a subsequent compatible snapshot clears it, through the REAL transport pipeline", () => {
    const { transport, socket } = connectRealTransport();
    const store = new SceneStore(transport);
    store.connect();

    socket.receive({
      kind: "state",
      topic: "scene",
      payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [], edges: [], pins: [] },
    });
    expect(store.getSceneBlockingRejection()).not.toBeNull();

    socket.receive({
      kind: "state",
      topic: "scene",
      payload: {
        schemaVersion: 2,
        minCompatibleSchemaVersion: 2,
        revision: 1,
        nodes: [],
        edges: [],
        pins: [],
        snapToGrid: false,
        fadeConnectionsEnabled: false,
        orthogonalRouting: false,
        smartGuides: false,
        hasSavedChat: false,
        dragFactor: 1,
        fontFamily: "Segoe UI",
        fontSizePt: 9,
        fontColor: "#F0F0F0",
      },
    });
    expect(store.getSceneBlockingRejection()).toBeNull();
  });

  it("a real mutating intent is blocked and surfaced via showError while the scene topic is version-rejected", async () => {
    const { transport, socket } = connectRealTransport();
    const store = new SceneStore(transport);
    store.connect();

    socket.receive({
      kind: "state",
      topic: "scene",
      payload: { schemaVersion: 2, minCompatibleSchemaVersion: 99, nodes: [], edges: [], pins: [] },
    });

    store.addNode(0, 0, "hello");
    await Promise.resolve();
    await Promise.resolve();

    // The addNode intent itself never reached the wire...
    expect(socket.sent.map((raw) => JSON.parse(raw))).not.toContainEqual(
      expect.objectContaining({ topic: "scene", intent: "addNode" }),
    );
    // ...and a visible error was surfaced instead of silent loss - closing
    // the exact gap the review found: only the canvas viewport was blocked,
    // while every sibling call site (ViewPopover, Composer, PinOverlay, the
    // command palette - all of which ultimately call the same store methods
    // exercised here) kept firing real intents completely unguarded.
    expect(socket.sent.map((raw) => JSON.parse(raw))).toContainEqual(
      expect.objectContaining({ topic: "notification", intent: "showError" }),
    );
  });
});
