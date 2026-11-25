/** Unweighted OLS in normalized coordinates, with pointwise Student-t
 * confidence intervals for the fitted mean. Mirrors chart_regression_fits
 * in graphlink_chart_data.py. Only canonical scientific samples enter here.
 */
import type { ChartDataRow } from "../../../lib/bridge-core/generated/scene-state";

const T95 = [
    12.706204736, 4.302652730, 3.182446305, 2.776445105, 2.570581836, 2.446911851,
    2.364624252, 2.306004135, 2.262157163, 2.228138852, 2.200985160, 2.178812830,
    2.160368656, 2.144786688, 2.131449546, 2.119905299, 2.109815578, 2.100922040,
    2.093024054, 2.085963447, 2.079613845, 2.073873068, 2.068657610, 2.063898562,
    2.059538553, 2.055529439, 2.051830516, 2.048407142, 2.045229642, 2.042272456,
];

function t95(df: number): number {
  if (df <= T95.length) return T95[df - 1];
  const z = 1.959963984540054;
  return z + (z ** 3 + z) / (4 * df)
    + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df ** 2)
    + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * df ** 3);
}

type FitPoint = [x: number, mean: number, lower: number, upper: number];
export interface LinearFit { n: number; r2: number | null; points: FitPoint[] }
const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);

export function linearFits(data: ChartDataRow): LinearFit[] | null {
  const xs = data.xValues;
  if (!xs || xs.length < 3 || (data.xScale ?? "linear") !== "linear"
      || (data.yScale ?? "linear") !== "linear") return null;
  const xmin = Math.min(...xs);
  const span = Math.max(...xs) - xmin;
  if (!Number.isFinite(span) || span <= 0) return null;
  const n = xs.length;
  const u = xs.map(x => (x - xmin) / span);
  const umean = sum(u) / n;
  const suu = sum(u.map(x => (x - umean) ** 2));
  if (!(suu > 0)) return null;
  const rows = data.series ?? [{ values: data.values ?? [], yError: data.yError }];
  const fits: LinearFit[] = [];
  const allY: number[] = [];
  for (const row of rows) {
    const ys = row.values;
    if (ys.length !== n) return null;
    const ymin = Math.min(...ys);
    const yspan = Math.max(...ys) - ymin;
    if (!Number.isFinite(yspan)) return null;
    const yscale = yspan || 1;
    const v = ys.map(y => (y - ymin) / yscale);
    const vmean = sum(v) / n;
    const beta = sum(u.map((x, i) => (x - umean) * (v[i] - vmean))) / suu;
    const sse = sum(v.map((y, i) => (y - (vmean + beta * (u[i] - umean))) ** 2));
    const sst = sum(v.map(y => (y - vmean) ** 2));
    const r2 = sst ? Math.max(0, Math.min(1, 1 - sse / sst)) : null;
    const sigma = Math.sqrt(sse / (n - 2));
    const points: FitPoint[] = [];
    for (let i = 0; i <= 64; i++) {
      const p = i / 64;
      const mean = vmean + beta * (p - umean);
      const half = t95(n - 2) * sigma * Math.sqrt(1 / n + (p - umean) ** 2 / suu);
      const point: FitPoint = [xmin + p * span, ymin + mean * yscale,
        ymin + (mean - half) * yscale, ymin + (mean + half) * yscale];
      if (!point.every(Number.isFinite)) return null;
      points.push(point);
      allY.push(point[2], point[3]);
    }
    ys.forEach((y, i) => {
      const [minus, plus] = row.yError?.[i] ?? [0, 0];
      allY.push(y - minus, y + plus);
    });
    fits.push({ n, r2, points });
  }
  if (!fits.length || !Number.isFinite(Math.max(...allY) - Math.min(...allY))) return null;
  return fits;
}

/** Include the whole fitted interval in the natural domain, without
 * changing a user's explicitly locked/zoomed domain. */
export function regressionYExtent(data: ChartDataRow, extent: [number, number]): [number, number] {
  if (data.analysis?.regression !== "ols95") return extent;
  const fits = linearFits(data);
  if (!fits) return extent;
  let [low, high] = extent;
  for (const fit of fits) {
    for (const point of fit.points) {
      low = Math.min(low, point[2]);
      high = Math.max(high, point[3]);
    }
  }
  return [low, high];
}
