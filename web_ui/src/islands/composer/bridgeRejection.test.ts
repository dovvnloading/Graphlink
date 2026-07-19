import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { createComposerBridge, type BridgeRejection } from "./bridge";
import { initialComposerState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

/**
 * Regression coverage for the silent-freeze bug.
 *
 * Before this change, parseState() returned null for a malformed or
 * version-mismatched payload and the caller did `if (state) listener(state)` -
 * so the state listener was simply never called. The UI kept rendering
 * whatever it last had, with nothing on screen or in the console indicating a
 * problem. These tests assert the two halves of the fix: bad payloads still
 * never reach the state listener (no corrupt render), AND every rejection is
 * now reported with a reason instead of being swallowed.
 */

function installFakeQWebChannel() {
  const remote = {
    stateChanged: { connect: vi.fn(), disconnect: vi.fn() },
    ready: vi.fn(),
    updateDraft: vi.fn(),
    send: vi.fn(),
    cancel: vi.fn(),
    reviewContext: vi.fn(),
    requestAttachment: vi.fn(),
    stageTextAttachment: vi.fn(),
    removeContextItem: vi.fn(),
    selectModel: vi.fn(),
    setReasoningLevel: vi.fn(),
    openSettings: vi.fn(),
    openModelSelector: vi.fn(),
    openReasoningSelector: vi.fn(),
    resize: vi.fn(),
  };
  class FakeQWebChannel {
    constructor(_t: unknown, cb: (channel: { objects: Record<string, unknown> }) => void) {
      cb({ objects: { composerBridge: remote } });
    }
  }
  const qtWindow = window as unknown as QtWindow;
  qtWindow.QWebChannel = FakeQWebChannel as unknown as QtWindow["QWebChannel"];
  qtWindow.qt = { webChannelTransport: {} };
  return remote;
}

function uninstall() {
  const qtWindow = window as unknown as QtWindow;
  delete qtWindow.QWebChannel;
  delete qtWindow.qt;
}

function connect() {
  const remote = installFakeQWebChannel();
  const listener = vi.fn();
  const rejections: (BridgeRejection | null)[] = [];
  createComposerBridge(listener, (r) => rejections.push(r));
  const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;
  return { listener, rejections, push };
}

const validPayload = () =>
  JSON.stringify({ ...initialComposerState, minCompatibleSchemaVersion: 1 });

describe("bridge payload rejection is visible, not silent", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    uninstall();
    vi.restoreAllMocks();
  });

  it("forwards a valid payload and reports no rejection", () => {
    const { listener, rejections, push } = connect();

    push(validPayload());

    expect(listener).toHaveBeenCalledTimes(1);
    expect(rejections.at(-1)).toBeNull();
  });

  it("reports a reason for unparseable JSON instead of dropping it", () => {
    const { listener, rejections, push } = connect();

    push("not json at all");

    expect(listener).not.toHaveBeenCalled();
    expect(rejections.at(-1)?.kind).toBe("parse");
    expect(rejections.at(-1)?.reason).toBeTruthy();
  });

  it("reports a reason for an incompatible schema version", () => {
    const { listener, rejections, push } = connect();

    push(JSON.stringify({ schemaVersion: 99, minCompatibleSchemaVersion: 99 }));

    expect(listener).not.toHaveBeenCalled();
    expect(rejections.at(-1)?.kind).toBe("version");
  });

  it("reports a reason - with detail - for a structurally wrong payload", () => {
    const { listener, rejections, push } = connect();
    const broken = JSON.parse(validPayload());
    delete broken.draft;

    push(JSON.stringify(broken));

    expect(listener).not.toHaveBeenCalled();
    const rejection = rejections.at(-1);
    expect(rejection?.kind).toBe("shape");
    expect(rejection?.details.length).toBeGreaterThan(0);
    expect(rejection?.details[0]).toContain("draft");
  });

  it("logs every rejection to the console so a developer sees it too", () => {
    const { push } = connect();

    push("not json");

    expect(console.error).toHaveBeenCalled();
  });

  it("clears the rejection once a good payload arrives, so a transient fault does not strand the UI", () => {
    const { listener, rejections, push } = connect();

    push("not json");
    expect(rejections.at(-1)).not.toBeNull();

    push(validPayload());

    expect(listener).toHaveBeenCalledTimes(1);
    expect(rejections.at(-1)).toBeNull();
  });

  it("tolerates unknown extra fields from a newer sender - additive compatibility", () => {
    const { listener, rejections, push } = connect();
    const additive = JSON.parse(validPayload());
    additive.somethingNewerSendersAdded = { nested: true };
    additive.draft.futureField = "x";

    push(JSON.stringify(additive));

    expect(listener).toHaveBeenCalledTimes(1);
    expect(rejections.at(-1)).toBeNull();
  });
});
