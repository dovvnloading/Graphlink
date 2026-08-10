/**
 * ADR-013 stage 13.2: small React hooks shared by every chart renderer.
 * Split from the components themselves so the plumbing (ResizeObserver
 * wiring, one-time theme read) doesn't have to be re-derived per chart type.
 */

import { useEffect, useRef, useState, type RefObject } from "react";
import { FALLBACK_CHART_THEME, readChartTheme, type ChartTheme } from "./chartTheme";

export interface ElementSize {
  width: number;
  height: number;
}

/** Tracks a DOM element's content-box size via ResizeObserver - the chart
 * SVG's viewBox is derived from this rather than from the flow node's own
 * chartWidth/chartHeight (see ChartNodeView.tsx's own module doc: those
 * drive the NodeResizer's controlled size, but the actual renderable area
 * is whatever remains after the title/toolbar chrome, which isn't a fixed
 * pixel offset). Returns {0, 0} until the element is measured at least
 * once - callers should not render chart geometry against a zero size. */
export function useElementSize<T extends Element>(): [RefObject<T>, ElementSize] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState<ElementSize>({ width: 0, height: 0 });

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const box = entry.contentRect;
      setSize((prev) => (prev.width === box.width && prev.height === box.height ? prev : { width: box.width, height: box.height }));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return [ref, size];
}

/** Reads the chart theme once the host element is mounted. Deliberately a
 * single read rather than a live-updating subscription: nothing in this
 * codebase mutates `--gl-*` at runtime today (ADR-012's theme toggle is
 * still proposed, not shipped) - see chartTheme.ts's own module doc. When
 * ADR-012 ships a real toggle, this is the one place that would grow a
 * listener. */
export function useChartTheme<T extends Element>(ref: RefObject<T>): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>(FALLBACK_CHART_THEME);
  useEffect(() => {
    if (ref.current) setTheme(readChartTheme(ref.current));
  }, [ref]);
  return theme;
}
