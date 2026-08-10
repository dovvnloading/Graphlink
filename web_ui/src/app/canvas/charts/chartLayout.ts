/**
 * ADR-013 stage 13.2: small DOM-adjacent layout helpers shared by every
 * chart type - margin sizing and hover-tooltip positioning. Kept separate
 * from chartScales.ts (which stays pure math with no DOM/event types) and
 * from the components themselves so each chart type isn't re-deriving the
 * same two calculations.
 */

import type { PointerEvent as ReactPointerEvent, RefObject } from "react";
import { formatValue } from "./chartScales";

export interface Margin {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

const APPROX_CHAR_WIDTH_PX = 6.5;

/** Left margin sized to fit the widest y-axis tick label actually being
 * rendered, rather than a fixed guess - a chart with values in the
 * thousands needs more room than one with single digits. */
export function computeLeftMargin(yTicks: number[], hasYAxisLabel: boolean): number {
  const widest = yTicks.reduce((max, tick) => Math.max(max, formatValue(tick).length), 1);
  const tickSpace = widest * APPROX_CHAR_WIDTH_PX + 14;
  return Math.max(hasYAxisLabel ? 44 : 32, tickSpace + (hasYAxisLabel ? 16 : 0));
}

export function computeBottomMargin(rotateXTicks: boolean, hasXAxisLabel: boolean): number {
  const tickSpace = rotateXTicks ? 46 : 30;
  return tickSpace + (hasXAxisLabel ? 18 : 0);
}

/** Position for an HTML tooltip overlay in the wrapper element's own CSS
 * pixel space - deliberately derived from the pointer event's clientX/Y
 * against the wrapper's bounding rect, NOT from any SVG-internal
 * coordinate, so the tooltip never drifts under useZoomPane's transform
 * (see ChartTooltip.tsx's own module doc). */
export function tooltipPositionFromEvent(
  event: ReactPointerEvent<Element>,
  wrapperRef: RefObject<HTMLElement>,
): { x: number; y: number } {
  const rect = wrapperRef.current?.getBoundingClientRect();
  if (!rect) return { x: 0, y: 0 };
  return { x: event.clientX - rect.left + 12, y: event.clientY - rect.top + 12 };
}
