/**
 * ADR-013 stage 13.2: pure, framework-free scale/formatting/layout math
 * shared by the bar/line/histogram renderers. Kept separate from any React
 * component (same posture as ChartNodeView.tsx's own
 * makeDebouncedChartResize) so the arithmetic is unit-testable without
 * mounting anything, and reused across all three cartesian chart types
 * rather than each reimplementing its own scale.
 *
 * `niceTicks`/`_format_value`(->formatValue)/`_wrap_label`(->wrapLabel) are
 * deliberate ports of graphlink_chart_rendering.py's own helpers - not
 * pixel-identical (this renders live SVG, that renders a static PNG at a
 * fixed DPI), but the same rounding/wrapping RULES, so a chart looks like
 * the same design language whether it's the live client render or the
 * exported PNG.
 */

export type Scale = (value: number) => number;

/** Linear interpolation from a numeric domain to a pixel range. Does not
 * clamp - callers that need values outside the domain to just be linearly
 * extrapolated (e.g. a zero baseline slightly outside [min, max]) rely on
 * that. */
export function createLinearScale(domain: [number, number], range: [number, number]): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  if (span === 0) return () => (r0 + r1) / 2;
  return (value: number) => r0 + ((value - d0) / span) * (r1 - r0);
}

/** "Nice" round-number ticks spanning at least [min, max] - the classic
 * Sparks/D3-style algorithm: pick a step from the {1, 2, 5} x 10^n family
 * closest to the ideal step for the requested tick count, then emit every
 * multiple of that step inside the (slightly widened) span. */
export function niceTicks(min: number, max: number, targetCount = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0];
  if (min === max) {
    if (min === 0) return [0];
    const magnitude = 10 ** Math.floor(Math.log10(Math.abs(min)));
    return [min - magnitude, min, min + magnitude];
  }
  const span = max - min;
  const roughStep = span / Math.max(1, targetCount);
  const magnitude = 10 ** Math.floor(Math.log10(roughStep));
  const residual = roughStep / magnitude;
  let step: number;
  if (residual >= 5) step = 5 * magnitude;
  else if (residual >= 2) step = 2 * magnitude;
  else step = magnitude;

  const niceMin = Math.floor(min / step) * step;
  const niceMax = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  // Round to kill float noise (e.g. 0.1 + 0.2 style drift) at the step's own precision.
  const decimals = Math.max(0, -Math.floor(Math.log10(step)));
  for (let tick = niceMin; tick <= niceMax + step / 2; tick += step) {
    ticks.push(Number(tick.toFixed(decimals + 2)));
  }
  return ticks;
}

/** Port of graphlink_chart_rendering.py's _format_value: thousands-grouped
 * integers, near-integers snapped to whole numbers, everything else to 2
 * decimal places with trailing zeros trimmed. */
export function formatValue(value: number): string {
  if (Math.abs(value) >= 1000) {
    return Math.round(value).toLocaleString("en-US");
  }
  if (Math.abs(value - Math.round(value)) < 0.001) {
    return Math.round(value).toLocaleString("en-US");
  }
  return value
    .toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    .replace(/0+$/, "")
    .replace(/\.$/, "");
}

/** Port of _wrap_label - greedy word-wrap into at most `maxLines` lines of
 * at most `width` characters, the last line ellipsis-truncated if content
 * remains. Returns the lines as an array (the caller renders one <tspan>
 * per line) rather than a "\n"-joined string, since SVG <text> does not
 * wrap or honor newlines on its own. */
export function wrapLabel(text: string, width = 14, maxLines = 2): string[] {
  const clean = String(text).trim().replace(/\s+/g, " ");
  if (!clean) return [];
  const words = clean.split(" ");
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length > width && current) {
      lines.push(current);
      current = word;
    } else {
      current = candidate;
    }
  }
  if (current) lines.push(current);

  if (lines.length <= maxLines) return lines;
  const kept = lines.slice(0, maxLines - 1);
  const remainder = lines.slice(maxLines - 1).join(" ");
  const budget = Math.max(1, width - 1);
  const truncated = remainder.length > width ? `${remainder.slice(0, budget)}...` : remainder;
  kept.push(truncated);
  return kept;
}

export interface CategoricalTicks {
  /** Indexes into the original labels/positions array that should render a tick. */
  indexes: number[];
  /** Whether the caller should rotate those tick labels (too many to fit horizontally). */
  rotate: boolean;
}

/** Port of _set_categorical_ticks's thinning rule: show a readable subset of
 * evenly-strided labels while always keeping the very last one, scaled to
 * how much horizontal room is actually available. */
export function pickCategoricalTicks(count: number, plotWidthPx: number): CategoricalTicks {
  if (count <= 0) return { indexes: [], rotate: false };
  const maxTicks = Math.max(4, Math.min(12, Math.floor(Math.max(1, plotWidthPx) / 72)));
  const stride = Math.max(1, Math.ceil(count / maxTicks));
  const indexes: number[] = [];
  for (let index = 0; index < count; index += stride) indexes.push(index);
  if (indexes[indexes.length - 1] !== count - 1) indexes.push(count - 1);
  return { indexes, rotate: indexes.length > 5 };
}

export interface HistogramBin {
  x0: number;
  x1: number;
  count: number;
}

/** Equal-width binning matching matplotlib's ax.hist default: `bins` equal
 * intervals spanning [min(values), max(values)], right edge inclusive only
 * for the final bin (a value exactly equal to the max lands in the last
 * bin rather than overflowing). */
export function computeHistogramBins(values: number[], bins: number): HistogramBin[] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = (max - min) / bins || 1;
  const counts = new Array(bins).fill(0);
  for (const value of values) {
    let index = Math.floor((value - min) / width);
    if (index >= bins) index = bins - 1;
    if (index < 0) index = 0;
    counts[index] += 1;
  }
  return counts.map((count, index) => ({
    x0: min + index * width,
    x1: min + (index + 1) * width,
    count,
  }));
}

export function mean(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}
