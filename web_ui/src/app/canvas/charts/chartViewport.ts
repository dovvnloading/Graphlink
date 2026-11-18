/** Data-space viewport arithmetic shared by the four analysis charts.
 * No observation is filtered or rebinned when a viewport changes.
 * Logarithmic interaction interpolates log10(data), not raw data or SVG px.
 */
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";
import { usesScientificMarks } from "../chartSpec";
import {
  computeHistogramBins, createLinearScale, createLog10Scale, niceTicks,
  pickCategoricalTicks, scientificDomain, type Scale,
} from "./chartScales";
import { computeBottomMargin, computeLeftMargin } from "./chartLayout";

export type Domain = [number, number];
export interface ViewDomains { x: Domain; y: Domain }
export type AxisKind = "linear" | "log10";
export interface ChartFrame {
  left: number;
  top: number;
  width: number;
  height: number;
  base: ViewDomains;
  xKind: AxisKind;
  yKind: AxisKind;
}

export function supportsChartAnalysis(type: string): boolean {
  return type === "line" || type === "scatter" || type === "histogram" || type === "boxplot";
}

export function chartFrame(chart: ChartDataRow, type: string, width: number, height: number): ChartFrame {
  let x: Domain;
  let y: Domain;
  let left = 76;
  let top = 18;
  let right = 20;
  let bottom: number;
  let xKind: AxisKind = "linear";
  let yKind: AxisKind = "linear";

  if (type === "boxplot") {
    const groups = chart.groups ?? [];
    const values = groups.flatMap((group) => group.values);
    x = [-0.5, groups.length - 0.5]; // matplotlib's bxp positions are 0..n-1
    y = scientificDomain(Math.min(...values), Math.max(...values), "linear");
    const rotate = pickCategoricalTicks(groups.length, Math.max(1, width - left - right)).rotate;
    bottom = computeBottomMargin(rotate, true);
  } else if (type === "histogram") {
    const bins = computeHistogramBins(chart.values ?? [], chart.bins ?? 10);
    const yMax = Math.max(...bins.map((bin) => bin.count)) * 1.18 || 1;
    const ticks = niceTicks(0, yMax, 5);
    y = [0, Math.max(yMax, ticks[ticks.length - 1])];
    x = [bins[0].x0, bins[bins.length - 1].x1];
    left = computeLeftMargin(ticks, Boolean(chart.yAxis));
    top = 12;
    right = 16;
    bottom = computeBottomMargin(false, Boolean(chart.xAxis));
  } else if (type === "line" && !usesScientificMarks(chart, type)) {
    const values = chart.values ?? [];
    const rawMin = Math.min(...values);
    const rawMax = Math.max(...values);
    const span = rawMax - rawMin;
    const buffer = span * 0.18 || Math.max(Math.abs(rawMax), 1) * 0.18 || 1;
    const ticks = niceTicks(rawMin - buffer, rawMax + buffer, 5);
    y = [Math.min(rawMin - buffer, ticks[0]), Math.max(rawMax + buffer, ticks[ticks.length - 1])];
    x = [0, values.length - 1];
    left = computeLeftMargin(ticks, Boolean(chart.yAxis));
    right = 16;
    top = 12;
    const rotate = pickCategoricalTicks(values.length, Math.max(1, width - left - right)).rotate;
    bottom = computeBottomMargin(rotate, Boolean(chart.xAxis));
  } else {
    const rows = chart.series ?? [{
      name: "", values: chart.values ?? [], xError: chart.xError, yError: chart.yError,
    }];
    const xs = chart.xValues ?? rows[0].values.map((_, index) => index);
    let xMin = Infinity;
    let xMax = -Infinity;
    let yMin = Infinity;
    let yMax = -Infinity;
    for (const row of rows) {
      row.values.forEach((value, index) => {
        const [ex0, ex1] = row.xError?.[index] ?? [0, 0];
        const [ey0, ey1] = row.yError?.[index] ?? [0, 0];
        xMin = Math.min(xMin, xs[index] - ex0);
        xMax = Math.max(xMax, xs[index] + ex1);
        yMin = Math.min(yMin, value - ey0);
        yMax = Math.max(yMax, value + ey1);
      });
    }
    xKind = chart.xScale ?? "linear";
    yKind = chart.yScale ?? "linear";
    x = scientificDomain(xMin, xMax, xKind);
    y = scientificDomain(yMin, yMax, yKind);
    const legendRows = chart.series ? Math.ceil(chart.series.length / Math.min(4, chart.series.length)) : 0;
    top = 16 + legendRows * 22;
    left = 74;
    right = 22;
    const categorical = type === "line" && !chart.xValues;
    const rotate = categorical && pickCategoricalTicks(xs.length, Math.max(1, width - left - right)).rotate;
    bottom = computeBottomMargin(Boolean(rotate), true);
  }
  return {
    left, top, width: Math.max(1, width - left - right),
    height: Math.max(1, height - top - bottom),
    base: { x, y }, xKind, yKind,
  };
}

export function scaleFor(domain: Domain, pixels: Domain, kind: AxisKind): Scale {
  return kind === "log10" ? createLog10Scale(domain, pixels) : createLinearScale(domain, pixels);
}

function transform(value: number, kind: AxisKind): number {
  return kind === "log10" ? Math.log10(value) : value;
}
function untransform(value: number, kind: AxisKind): number {
  // Stay inside finite/positive double bounds even at log extrema.
  return kind === "log10"
    ? 10 ** Math.max(Math.log10(Number.MIN_VALUE), Math.min(Math.log10(Number.MAX_VALUE), value))
    : value;
}

function validRange(range: Domain, kind: AxisKind): boolean {
  return Number.isFinite(range[0]) && Number.isFinite(range[1]) &&
    range[0] < range[1] && Number.isFinite(range[1] - range[0]) &&
    (kind !== "log10" || range[0] > 0);
}

/** Convert plot-relative pixels into an original data value, including log axes. */
export function invertScale(px: number, domain: Domain, pixels: Domain, kind: AxisKind): number {
  const [lo, hi] = domain.map((value) => transform(value, kind));
  const fraction = (px - pixels[0]) / (pixels[1] - pixels[0]);
  return untransform(lo + fraction * (hi - lo), kind);
}

/** Positive factor zooms in; log axes change by equal exponent intervals. */
export function zoomDomain(domain: Domain, kind: AxisKind, anchor: number, factor: number): Domain {
  if (!Number.isFinite(factor) || factor <= 0 || !Number.isFinite(anchor) ||
      (kind === "log10" && anchor <= 0)) return domain;
  const lo = transform(domain[0], kind);
  const hi = transform(domain[1], kind);
  const pivot = transform(anchor, kind);
  // Distances from the pivot are scaled in axis space (log10 for log axes).
  const next: Domain = [
    untransform(pivot + (lo - pivot) / factor, kind),
    untransform(pivot + (hi - pivot) / factor, kind),
  ];
  return validRange(next, kind) ? next : domain;
}

/** Fraction of current visible span, signed in transformed (log) space. */
export function panDomain(domain: Domain, kind: AxisKind, fraction: number): Domain {
  if (!Number.isFinite(fraction)) return domain;
  const lo = transform(domain[0], kind);
  const hi = transform(domain[1], kind);
  const offset = (hi - lo) * fraction;
  const next: Domain = [untransform(lo + offset, kind), untransform(hi + offset, kind)];
  return validRange(next, kind) ? next : domain;
}

export function domainFromPixels(
  a: number, b: number, range: Domain, pixels: Domain, kind: AxisKind,
): Domain {
  const first = invertScale(a, range, pixels, kind);
  const second = invertScale(b, range, pixels, kind);
  const next: Domain = [Math.min(first, second), Math.max(first, second)];
  return validRange(next, kind) ? next : range;
}

export function datumAt(
  localX: number, localY: number, frame: ChartFrame, view: ViewDomains,
): { x: number; y: number } {
  return {
    x: invertScale(localX - frame.left, view.x, [0, frame.width], frame.xKind),
    y: invertScale(localY - frame.top, view.y, [frame.height, 0], frame.yKind),
  };
}
