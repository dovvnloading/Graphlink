/**
 * Smart-guide alignment snapping (Qt-removal plan R7.5b-3) - a pure, direct
 * translation of the legacy ChatScene._calculate_smart_guide_snap
 * (graphlink_scene.py), deliberately framework-free so the whole algorithm is
 * unit-testable without mounting a canvas (the scaleDragPosition/toFlowNodes
 * precedent). SceneCanvas.tsx calls this per drag frame and renders the
 * returned guide lines through React Flow's <ViewportPortal>.
 *
 * Ported semantics, all confirmed against the legacy source by recon:
 * - 3 alignment keys per axis, matched SAME-KEY-ONLY (moving-left vs
 *   candidate-left, center vs center, ... - never left-to-center).
 * - Candidates scanned in array order; per candidate, X keys are tried in
 *   left/center/right order and Y keys in top/middle/bottom order, first hit
 *   within tolerance wins that axis. X and Y may therefore snap against two
 *   DIFFERENT candidates in one call.
 * - The scan stops the moment both axes are snapped - it does not keep
 *   looking for a "closer" alignment. At most one guide line per axis.
 * - Tolerance is a strict less-than (legacy: abs(m - s) < ALIGNMENT_TOLERANCE).
 * - A guide line sits at the CANDIDATE's key value and spans the union of
 *   the two rects along the perpendicular axis.
 */

// Legacy's ALIGNMENT_TOLERANCE = 5 (scene px), verbatim. One constant gates
// both "show the guide" and "snap the position" - legacy had no separate
// show-but-don't-snap zone, so neither does this.
export const ALIGNMENT_TOLERANCE_PX = 5;

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GuideLine {
  orientation: "vertical" | "horizontal";
  /** The aligned coordinate: x for vertical guides, y for horizontal. */
  position: number;
  /** Span along the perpendicular axis (y-range for vertical, x-range for
   * horizontal) - the union of the moving and candidate rects, matching
   * legacy's min(top,top)..max(bottom,bottom) segments (never full-canvas
   * lines). */
  start: number;
  end: number;
}

export interface SmartGuideSnapResult {
  x: number;
  y: number;
  guides: GuideLine[];
}

// Key order matters: legacy tested left/center/right (and top/middle/bottom)
// in this exact order and took the FIRST within-tolerance hit, not the
// closest one.
const VERTICAL_KEYS = ["left", "center", "right"] as const;
const HORIZONTAL_KEYS = ["top", "middle", "bottom"] as const;

function verticalValues(r: Rect): Record<(typeof VERTICAL_KEYS)[number], number> {
  return { left: r.x, center: r.x + r.width / 2, right: r.x + r.width };
}

function horizontalValues(r: Rect): Record<(typeof HORIZONTAL_KEYS)[number], number> {
  return { top: r.y, middle: r.y + r.height / 2, bottom: r.y + r.height };
}

export function computeSmartGuideSnap(
  movingRect: Rect,
  candidates: Rect[],
  tolerance: number = ALIGNMENT_TOLERANCE_PX,
): SmartGuideSnapResult {
  let snappedX: number | null = null;
  let snappedY: number | null = null;
  const guides: GuideLine[] = [];

  const movingV = verticalValues(movingRect);
  const movingH = horizontalValues(movingRect);

  for (const candidate of candidates) {
    if (snappedX === null) {
      const candV = verticalValues(candidate);
      for (const key of VERTICAL_KEYS) {
        if (Math.abs(movingV[key] - candV[key]) < tolerance) {
          const offset = key === "left" ? 0 : key === "center" ? movingRect.width / 2 : movingRect.width;
          snappedX = candV[key] - offset;
          guides.push({
            orientation: "vertical",
            position: candV[key],
            start: Math.min(movingRect.y, candidate.y),
            end: Math.max(movingRect.y + movingRect.height, candidate.y + candidate.height),
          });
          break;
        }
      }
    }
    if (snappedY === null) {
      const candH = horizontalValues(candidate);
      for (const key of HORIZONTAL_KEYS) {
        if (Math.abs(movingH[key] - candH[key]) < tolerance) {
          const offset = key === "top" ? 0 : key === "middle" ? movingRect.height / 2 : movingRect.height;
          snappedY = candH[key] - offset;
          guides.push({
            orientation: "horizontal",
            position: candH[key],
            start: Math.min(movingRect.x, candidate.x),
            end: Math.max(movingRect.x + movingRect.width, candidate.x + candidate.width),
          });
          break;
        }
      }
    }
    if (snappedX !== null && snappedY !== null) break;
  }

  return {
    x: snappedX ?? movingRect.x,
    y: snappedY ?? movingRect.y,
    guides,
  };
}
