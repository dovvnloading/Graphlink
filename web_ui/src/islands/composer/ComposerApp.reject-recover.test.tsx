import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import ComposerApp from "./ComposerApp";
import { initialComposerState } from "./bridgeTypes";
import type { QtWindow } from "../../lib/bridge-core/transport";

/**
 * Regression coverage for a real bug adversarial review found: the
 * ResizeObserver-attaching effect had a `[]` dependency array, so it ran
 * exactly once for the component's whole lifetime. Before BridgeErrorState
 * existed that was safe - <main ref={shellRef}> was the only thing
 * ComposerApp ever rendered. Once a rejected payload could unmount it
 * (replaced by BridgeErrorState) and a later valid payload could remount a
 * BRAND NEW <main> node, the observer kept watching the original, now-
 * detached node forever: resize() would never fire again for the real one,
 * silently freezing the host's negotiated height.
 *
 * Fixed by keying the effect on `rejection` instead of `[]`. These tests
 * mount the real ComposerApp against a real (faked) QWebChannel - not the
 * jsdom mock-bridge fallback other tests use - because the bug is entirely
 * about what happens when real payloads flow through the real bridge and
 * cause a real mount/unmount/remount cycle.
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

const validPayload = () => JSON.stringify({ ...initialComposerState, minCompatibleSchemaVersion: 1 });

describe("ComposerApp reject -> recover DOM/resize lifecycle", () => {
  afterEach(() => {
    cleanup();
    uninstall();
    vi.restoreAllMocks();
  });

  it("mounts a NEW shell DOM node on recovery, not the original", async () => {
    const remote = installFakeQWebChannel();
    render(<ComposerApp />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(validPayload());
    const shellBefore = document.querySelector(".composer-shell:not(.bridge-error)");
    expect(shellBefore).not.toBeNull();

    push("not json"); // rejection -> unmounts <main>
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(document.querySelector(".composer-shell:not(.bridge-error)")).toBeNull();

    push(validPayload()); // recovery -> remounts a NEW <main>
    await waitFor(() =>
      expect(document.querySelector(".composer-shell:not(.bridge-error)")).not.toBeNull(),
    );
    const shellAfter = document.querySelector(".composer-shell:not(.bridge-error)");

    expect(shellAfter).not.toBe(shellBefore);
  });

  it("calls resize() again for the new shell after a reject/recover cycle - the actual regression", async () => {
    const remote = installFakeQWebChannel();
    render(<ComposerApp />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    push(validPayload());
    await waitFor(() => expect(remote.resize.mock.calls.length).toBeGreaterThan(0));
    const callsBeforeReject = remote.resize.mock.calls.length;

    push("not json");
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());

    push(validPayload());
    await waitFor(() =>
      expect(document.querySelector(".composer-shell:not(.bridge-error)")).not.toBeNull(),
    );

    // The bug: with a `[]`-keyed effect this next assertion fails, because the
    // ResizeObserver effect never re-ran for the new shell and reportHeight()
    // was never called again.
    await waitFor(() => expect(remote.resize.mock.calls.length).toBeGreaterThan(callsBeforeReject));
  });

  it("does not throw when a rejection arrives immediately, before any successful mount", async () => {
    const remote = installFakeQWebChannel();
    render(<ComposerApp />);
    const push = remote.stateChanged.connect.mock.calls[0][0] as (payload: string) => void;

    expect(() => push("not json")).not.toThrow();
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
