/** Deterministic Gaussian KDE and jitter, matching the publication exporter.
 * Density widths are relative to each group's peak and clipped to its
 * observed span; they must not be interpreted as absolute densities.
 */
export function raincloudDensity(values: number[]): [number, number][] {
  if (values.length < 2) return [];
  const lo = Math.min(...values);
  const span = Math.max(...values) - lo;
  if (!Number.isFinite(span) || span <= 0) return [];
  const u = values.map(v => (v - lo) / span);
  const avg = u.reduce((a, b) => a + b, 0) / u.length;
  const sd = Math.sqrt(u.reduce((total, v) => total + (v - avg) ** 2, 0) / (u.length - 1));
  const bandwidth = 1.06 * sd * u.length ** (-0.2);
  if (!(bandwidth > 0) || !Number.isFinite(bandwidth)) return [];
  const estimates: [number, number][] = Array.from({ length: 65 }, (_, i) => {
    const q = i / 64;
    return [lo + q * span, u.reduce((total, v) => total + Math.exp(-0.5 * ((q - v) / bandwidth) ** 2), 0)];
  });
  const peak = Math.max(...estimates.map(p => p[1]));
  return peak ? estimates.map(([v, d]) => [v, d / peak]) : [];
}

export function raincloudJitter(index: number): number {
  return 0.15 + 0.28 * ((index * 137) % 307) / 306;
}
