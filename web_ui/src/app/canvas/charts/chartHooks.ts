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

/** Reads the chart theme from the host element's resolved `--gl-*` custom
 * properties, and KEEPS IT CURRENT as the active theme changes.
 *
 * This grew the listener its own previous comment promised. That comment
 * said a single read was fine because "nothing in this codebase mutates
 * `--gl-*` at runtime today (ADR-012's theme toggle is still proposed, not
 * shipped)" - but ADR-012 stage 12.2 shipped a genuine live toggle on the
 * same day this file landed (App.tsx calls applyTheme() on every fresh
 * app-settings snapshot, stamping/clearing documentElement's `data-theme`,
 * no reload). A 2026-08-12 audit caught the two halves contradicting each
 * other: because ChartNodeView is React.memo'd on chartData/chartType (none
 * of which change on a theme switch), an already-open chart never re-rendered
 * and kept rendering last theme's colors - wrong-contrast bars, lines, and
 * tooltips against the new background - until that node happened to unmount
 * and remount.
 *
 * Two sources have to be watched, because `applyTheme` supports three states:
 *   - `data-theme="light"`/`"dark"` on <html> for an explicit choice, which a
 *     MutationObserver on that one attribute catches;
 *   - NO attribute at all for "system", where the palette instead follows the
 *     `prefers-color-scheme` media query, which only a matchMedia listener
 *     catches (the DOM never changes when the OS flips appearance).
 * Watching only the attribute would silently miss every system-mode user. */
export function useChartTheme<T extends Element>(ref: RefObject<T>): ChartTheme {
  const [theme, setTheme] = useState<ChartTheme>(FALLBACK_CHART_THEME);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    // Re-read on demand, and only commit a new object when something actually
    // changed - readChartTheme builds a fresh object every call, so returning
    // it unconditionally would re-render every chart on any unrelated
    // attribute mutation.
    function syncTheme() {
      const element = ref.current;
      if (!element) return;
      const next = readChartTheme(element);
      setTheme((prev) => (chartThemesEqual(prev, next) ? prev : next));
    }

    syncTheme();

    const observer = new MutationObserver(syncTheme);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    const media = window.matchMedia("(prefers-color-scheme: light)");
    media.addEventListener("change", syncTheme);

    return () => {
      observer.disconnect();
      media.removeEventListener("change", syncTheme);
    };
  }, [ref]);

  return theme;
}

/** Value-equality for the theme record, so a re-read that resolved to the same
 * colors doesn't churn every mounted chart.
 *
 * `categorical` needs an element-wise compare, not `===`: it is a string[]
 * that readChartTheme rebuilds on every call, so reference equality is always
 * false and a naive key-wise `a[k] === b[k]` would report "changed" every
 * single time - turning this guard into a no-op and re-rendering every chart
 * on any unrelated <html> attribute mutation. Every other field is a plain
 * string and compares directly. */
function chartThemesEqual(a: ChartTheme, b: ChartTheme): boolean {
  if (a.categorical.length !== b.categorical.length) return false;
  if (!a.categorical.every((color, index) => color === b.categorical[index])) return false;
  return (
    a.text === b.text &&
    a.textMuted === b.textMuted &&
    a.border === b.border &&
    a.gridline === b.gridline &&
    a.surface === b.surface &&
    a.tooltipBackground === b.tooltipBackground &&
    a.tooltipBorder === b.tooltipBorder &&
    a.fontFamily === b.fontFamily
  );
}
