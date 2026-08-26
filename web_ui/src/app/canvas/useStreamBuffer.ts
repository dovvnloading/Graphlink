import { useEffect, useRef, useState } from "react";
import type { StreamListener } from "../../lib/ws/transport";

/** Dedup (tech-debt sweep, cross-check against ChatNodeView/ConversationNodeView):
 * both views independently implemented the exact same rAF-throttled stream-
 * buffer accumulator for their live-streaming assistant reply (ADR-006 stage
 * 6.4's subscribeStream/pendingRequestId pairing, throttled per ADR-011 stage
 * 11.4 so a fast burst of deltas within one frame re-parses markdown at most
 * once per frame instead of once per delta). The two copies were
 * byte-for-byte identical apart from the returned state's local name
 * (streamedContent vs streamedReply) - this hook is a pure extraction of
 * that shared logic, zero behavior change, matching this codebase's existing
 * small-shared-hook convention (see useLodVisibility.ts alongside this file).
 *
 * Deltas accumulate into refs (not React state) as they arrive - only the
 * rAF-scheduled flush ever calls setState, so a burst of many small deltas
 * within one frame updates the returned string at most once per frame. Every
 * byte still lands in the result, in order; only the re-render cadence
 * changes. pendingBufferRef always holds the full, in-order text accumulated
 * since the last flush, and flushStreamBuffer moves the whole thing into
 * state atomically. pendingResetRef tracks whether the buffered text should
 * REPLACE the accumulated text at the next flush (a `reset` frame) rather
 * than append to it, mirroring the original un-throttled
 * `reset ? delta : current + delta` semantics exactly - just deferred to
 * flush time instead of applied delta-by-delta.
 *
 * Returns "" whenever requestId is null, and re-seeds to "" the moment
 * requestId changes to a new id (derived-state reset during render, so a new
 * request never shows the previous run's stale text before its effect has a
 * chance to run). subscribeStream is expected to be a fresh closure every
 * render (see SceneCanvas's toFlowNodes) - only requestId itself is the
 * effect's re-subscribe key, matching both call sites' own prior
 * eslint-disable-line reasoning for react-hooks/exhaustive-deps. */
export function useStreamBuffer(
  requestId: string | null,
  subscribeStream: (requestId: string, listener: StreamListener) => () => void,
): string {
  const [streamedText, setStreamedText] = useState("");
  const [subscribedRequestId, setSubscribedRequestId] = useState(requestId);
  if (requestId !== subscribedRequestId) {
    setSubscribedRequestId(requestId);
    setStreamedText("");
  }

  const pendingBufferRef = useRef("");
  const pendingResetRef = useRef(false);
  const rafHandleRef = useRef<number | null>(null);

  function flushStreamBuffer() {
    if (rafHandleRef.current !== null) {
      cancelAnimationFrame(rafHandleRef.current);
      rafHandleRef.current = null;
    }
    const bufferedText = pendingBufferRef.current;
    const shouldReset = pendingResetRef.current;
    pendingBufferRef.current = "";
    pendingResetRef.current = false;
    if (!bufferedText && !shouldReset) return;
    setStreamedText((current) => (shouldReset ? bufferedText : current + bufferedText));
  }

  useEffect(() => {
    if (!requestId) return;
    const unsubscribe = subscribeStream(requestId, (delta, done, reset) => {
      if (reset) {
        pendingBufferRef.current = delta;
        pendingResetRef.current = true;
      } else {
        pendingBufferRef.current += delta;
      }
      // Stream completion/reset flushes synchronously - the final chunk (or
      // a fresh restart) shouldn't wait an extra frame, and this is also
      // what guarantees a trailing chunk never gets stranded in the buffer
      // past the last render.
      if (done || reset) {
        flushStreamBuffer();
      } else if (rafHandleRef.current === null) {
        rafHandleRef.current = requestAnimationFrame(flushStreamBuffer);
      }
    });
    return () => {
      unsubscribe();
      // A requestId change (new run, or this run finished) makes any content
      // still sitting in the buffer for the OLD request moot - the
      // render-time subscribedRequestId reset above already clears
      // streamedText to "" for a new id - but flush (rather than silently
      // drop) here too, so no buffered byte is ever lost even in that narrow
      // window.
      flushStreamBuffer();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestId]);

  return streamedText;
}
