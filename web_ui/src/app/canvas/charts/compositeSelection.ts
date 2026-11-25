/** Original-row brushing for V2 figures.
 *
 * All comparisons operate on canonical source columns; SVG point-sampling
 * never affects selections. A brush uses the visible panel's coordinates
 * and returns sorted, unique source row indices. This keeps selections
 * consistent across independent mark layers and across zoom levels.
 */
import type { ChartCompositeSpecRow } from "../../../lib/bridge-core/generated/scene-state";

export interface CompositeDataRect {
  x: [number, number];
  y: [number, number];
}
export interface CompositeBrushPoint { x: number; y: number }
export function compositeBrushBounds(a: CompositeBrushPoint, b: CompositeBrushPoint): CompositeDataRect {
  return { x: [Math.min(a.x, b.x), Math.max(a.x, b.x)],
    y: [Math.min(a.y, b.y), Math.max(a.y, b.y)] };
}
export function compositeRowsInRect(
  spec: ChartCompositeSpecRow,
  facet: string | null,
  rect: CompositeDataRect,
): number[] {
  const sourceCount = Object.values(spec.numbers)[0]?.length ?? 0;
  const facetValues = spec.facetBy ? spec.categories?.[spec.facetBy] : null;
  // A line/point mark defines a record's actual X/Y location. Do not
  // interpret an uncertainty-band lower bound or area fill as a point
  // observation when another mark supplies the measurements.
  const referenceLayers = spec.layers.filter(layer => layer.mark === "point" || layer.mark === "line");
  const layers = referenceLayers.length > 0 ? referenceLayers : spec.layers;
  const selected: number[] = [];
  for (let index = 0; index < sourceCount; index++) {
    if (facetValues && facetValues[index] !== facet) continue;
    if (layers.some(layer => {
      const x = spec.numbers[layer.x][index];
      const lowY = spec.numbers[layer.y][index];
      const highY = layer.y2 ? spec.numbers[layer.y2][index] : lowY;
      const withinX = rect.x[0] <= x && x <= rect.x[1];
      // For band-only figures, brush the actual vertical uncertainty
      // interval (intersection), rather than inventing a central estimate.
      const withinY = layer.mark === "band"
        ? highY >= rect.y[0] && lowY <= rect.y[1]
        : rect.y[0] <= lowY && lowY <= rect.y[1];
      return withinX && withinY;
    })) selected.push(index);
  }
  return selected;
}

/** Map CSS-normalized panel coordinates into real axis data units.
 * Preserves log10 spacing; selecting never uses tick-label rounding.
 */
export function brushPixelToData(
  point: CompositeBrushPoint,
  width: number,
  height: number,
  xDomain: [number, number],
  yDomain: [number, number],
  xLog = false,
  yLog = false,
): CompositeBrushPoint {
  const px = Math.max(0, Math.min(1, point.x / Math.max(1, width)));
  const py = Math.max(0, Math.min(1, point.y / Math.max(1, height)));
  function interpolate(domain: [number, number], t: number, log: boolean) {
    return log
      ? 10 ** (Math.log10(domain[0]) + t * (Math.log10(domain[1]) - Math.log10(domain[0])))
      : domain[0] + t * (domain[1] - domain[0]);
  }
  return { x: interpolate(xDomain, px, xLog), y: interpolate(yDomain, 1 - py, yLog) };
}
