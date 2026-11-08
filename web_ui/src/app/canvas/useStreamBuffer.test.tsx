import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStreamBuffer } from "./useStreamBuffer";
import type { StreamListener } from "../../lib/ws/transport";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useStreamBuffer request replacement", () => {
  it("discards an unflushed old request when a new request begins", () => {
    const listeners = new Map<string, StreamListener>();
    const cancelled = vi.fn();
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
    vi.stubGlobal("cancelAnimationFrame", cancelled);

    const subscribe = (id: string, listener: StreamListener) => {
      listeners.set(id, listener);
      return () => { listeners.delete(id); };
    };
    const { result, rerender } = renderHook(
      ({ id }) => useStreamBuffer(id, subscribe),
      { initialProps: { id: "request-A" } },
    );

    act(() => {
      listeners.get("request-A")?.("stale A", false, false, 0);
    });
    // The rAF has not fired. Switching requestId must clear the buffered
    // delta instead of appending it via the old effect's cleanup.
    rerender({ id: "request-B" });
    expect(result.current).toBe("");
    expect(cancelled).toHaveBeenCalledWith(1);

    act(() => {
      listeners.get("request-B")?.("fresh B", true, false, 0);
    });
    expect(result.current).toBe("fresh B");
  });
});
