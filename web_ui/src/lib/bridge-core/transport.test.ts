import { describe, expect, it, vi } from "vitest";
import { connectQWebChannel, isQWebChannelAvailable, QtWindow } from "./transport";

function fakeWindow(overrides: Partial<QtWindow> = {}): Window {
  return { ...overrides } as Window;
}

describe("isQWebChannelAvailable", () => {
  it("is false when neither QWebChannel nor the transport is present", () => {
    expect(isQWebChannelAvailable(fakeWindow())).toBe(false);
  });

  it("is false when only QWebChannel is present (no transport)", () => {
    const win = fakeWindow({ QWebChannel: class {} as never });
    expect(isQWebChannelAvailable(win)).toBe(false);
  });

  it("is false when only the transport is present (no QWebChannel)", () => {
    const win = fakeWindow({ qt: { webChannelTransport: {} } });
    expect(isQWebChannelAvailable(win)).toBe(false);
  });

  it("is true when both QWebChannel and the transport are present", () => {
    const win = fakeWindow({
      QWebChannel: class {} as never,
      qt: { webChannelTransport: {} },
    });
    expect(isQWebChannelAvailable(win)).toBe(true);
  });
});

describe("connectQWebChannel", () => {
  it("never calls onConnected when QWebChannel is unavailable", () => {
    const onConnected = vi.fn();
    connectQWebChannel(onConnected, fakeWindow());
    expect(onConnected).not.toHaveBeenCalled();
  });

  it("constructs QWebChannel with the transport and forwards channel.objects", () => {
    const transport = { marker: "the-transport" };
    const fakeObjects = { composerBridge: { ready: vi.fn() } };
    let capturedTransport: unknown = null;

    class FakeQWebChannel {
      constructor(passedTransport: unknown, callback: (channel: { objects: Record<string, unknown> }) => void) {
        capturedTransport = passedTransport;
        callback({ objects: fakeObjects });
      }
    }

    const win = fakeWindow({
      QWebChannel: FakeQWebChannel as unknown as QtWindow["QWebChannel"],
      qt: { webChannelTransport: transport },
    });

    const onConnected = vi.fn();
    connectQWebChannel(onConnected, win);

    expect(capturedTransport).toBe(transport);
    expect(onConnected).toHaveBeenCalledTimes(1);
    expect(onConnected).toHaveBeenCalledWith(fakeObjects);
  });
});
