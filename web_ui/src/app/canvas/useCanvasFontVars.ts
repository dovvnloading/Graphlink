import { useEffect } from "react";
import type { RefObject } from "react";

/**
 * R8a (UI/UX issue list finding #11) extraction: CanvasInner's own CSS
 * custom-property effect, pulled out verbatim as its own hook - matching
 * this directory's small-shared-hook convention (see useLodVisibility.ts/
 * useStreamBuffer.ts alongside this file). Zero behavior change: same
 * three properties, same effect, same dependency array; only the location
 * moved.
 *
 * The View popover's FONT section (family/size/color) already round-trips
 * real intents into scene state - nothing ever consumed them. Written as
 * CSS custom properties on the canvas wrapper (not per-node inline
 * styles) so every current AND future node inherits them for free through
 * .scene-node's own rules in styles.css, the same way .scene-canvas
 * already carries grid state down via React Flow's own Background props.
 *
 * `wrapperRef` is NOT owned here - it takes CanvasInner's own
 * canvasWrapperRef as a parameter rather than creating its own, since that
 * ref is also the pan handler's target and the wrapper JSX's own `ref=`.
 */
export function useCanvasFontVars(
  wrapperRef: RefObject<HTMLDivElement | null>,
  fontFamily: string,
  fontSizePt: number,
  fontColor: string,
): void {
  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    el.style.setProperty("--gl-node-font-family", fontFamily);
    // fontSizePt is POINTS (backend field: font_size_pt, default 9 -> the
    // FONT_SIZE_MIN/MAX range of 8-16 only makes sense as points, not
    // pixels: 16px node text would be barely a size change from the 12px/
    // 11px base, while 16pt is a real, visible jump). `pt` is a real CSS
    // unit (1pt = 1/72in = 4/3px), not print-only, so this needs no
    // conversion - just the right unit suffix.
    el.style.setProperty("--gl-node-font-size", `${fontSizePt}pt`);
    el.style.setProperty("--gl-node-font-color", fontColor);
  }, [wrapperRef, fontFamily, fontSizePt, fontColor]);
}
