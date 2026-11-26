/** Viewport-aware, bounded SVG marks for saved full-resolution science data.
 * Select first/last and min/max X and Y observations from consecutive
 * input-order buckets. This preserves extreme trends and source ordering,
 * without rounding measurements, mutating data, or sampling publication output.
 */
import type { ErrorPair } from "../chartSpec";
export const MAX_SCIENTIFIC_SVG_POINTS = 1200;

export function scientificMarkIndices(
  xs: number[], ys: number[],
  viewport?: { x: [number, number]; y: [number, number] },
  budget = MAX_SCIENTIFIC_SVG_POINTS,
  kind: "line" | "scatter" = "scatter",
  errors?: ErrorPair[] | null,
  xErrors?: ErrorPair[] | null,
): number[] {
  const visible: number[] = [];
  const within = (value: number, range: [number, number]) => value >= range[0] && value <= range[1];
  const overlaps = (low: number, high: number, range: [number, number]) => low <= range[1] && high >= range[0];
  for (let i = 0; i < ys.length; i++) {
    if (!viewport) {
      visible.push(i);
      continue;
    }
    const x = xs[i];
    const y = ys[i];
    const yVisible = kind === "line" || within(y, viewport.y);
    // An observation whose center is outside the viewport still counts
    // when its whisker crosses it; the SVG clip path trims the rest.
    const xe = xErrors?.[i];
    const ye = errors?.[i];
    const horizontal = xe ? overlaps(x - xe[0], x + xe[1], viewport.x) : within(x, viewport.x);
    const vertical = kind === "scatter" && !!ye && within(x, viewport.x)
      && overlaps(y - ye[0], y + ye[1], viewport.y);
    if ((horizontal && yVisible) || vertical) visible.push(i);
  }
  if (visible.length <= budget) return visible;
  const indexes = new Set<number>();
  indexes.add(visible[0]);
  indexes.add(visible[visible.length - 1]);
  const buckets = Math.max(1, Math.floor((budget - 2) / 4));
  for (let b = 0; b < buckets; b++) {
    const start = Math.floor(b * visible.length / buckets);
    const end = Math.floor((b + 1) * visible.length / buckets);
    if (end <= start) continue;
    let minX = visible[start], maxX = minX, minY = minX, maxY = minX;
    for (let j = start + 1; j < end; j++) {
      const index = visible[j];
      if (xs[index] < xs[minX]) minX = index;
      if (xs[index] > xs[maxX]) maxX = index;
      const low = (i: number) => ys[i] - (errors?.[i]?.[0] ?? 0);
      const high = (i: number) => ys[i] + (errors?.[i]?.[1] ?? 0);
      if (low(index) < low(minY)) minY = index;
      if (high(index) > high(maxY)) maxY = index;
    }
    for (const index of [minX, maxX, minY, maxY]) indexes.add(index);
  }
  return [...indexes].sort((a, b) => a - b);
}

export function scientificMarkBudget(seriesCount: number): number {
  return Math.floor(MAX_SCIENTIFIC_SVG_POINTS / Math.max(1, seriesCount));
}
