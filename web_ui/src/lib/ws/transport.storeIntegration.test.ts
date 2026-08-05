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
