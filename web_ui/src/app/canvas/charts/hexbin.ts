/** Deterministic pointy-top hexagonal binning on the visible data domain.
 * Each original observation contributes to exactly one cell or, if outside
 * the viewport, none. Count encodes all input points; coordinates are never
 * rounded or changed in the stored dataset. No external chart dependency.
 */
export interface HexCell {
  q: number;
  r: number;
  cx: number;
  cy: number;
  count: number;
}
const SQRT3 = Math.sqrt(3);

export function hexbinPoints(
  xs: number[], ys: number[],
  xDomain: [number, number], yDomain: [number, number],
  width: number, height: number, radius = 14,
): HexCell[] {
  const dx = xDomain[1] - xDomain[0];
  const dy = yDomain[1] - yDomain[0];
  if (!(dx > 0) || !(dy > 0) || !(radius > 0) ||
      !Number.isFinite(dx) || !Number.isFinite(dy) ||
      !Number.isFinite(width) || !Number.isFinite(height)) return [];
  const cells = new Map<string, HexCell>();
  for (let i = 0; i < xs.length; i++) {
    const x = xs[i], y = ys[i];
    if (x < xDomain[0] || x > xDomain[1] || y < yDomain[0] || y > yDomain[1]) continue;
    const px = (x - xDomain[0]) / dx * width;
    const py = (yDomain[1] - y) / dy * height;
    const aq = (SQRT3 / 3 * px - py / 3) / radius;
    const ar = (2 / 3 * py) / radius;
    // Round the axial -> cube coordinates to the nearest hex center.
    let cq = Math.round(aq), cr = Math.round(ar), cs = Math.round(-aq - ar);
    const dq = Math.abs(cq - aq), dr = Math.abs(cr - ar), ds = Math.abs(cs + aq + ar);
    if (dq > dr && dq > ds) cq = -cr - cs;
    else if (dr > ds) cr = -cq - cs;
    else cs = -cq - cr;
    const key = cq + ":" + cr;
    const existing = cells.get(key);
    if (existing) existing.count++;
    else cells.set(key, {
      q: cq, r: cr, cx: radius * SQRT3 * (cq + cr / 2),
      cy: radius * 1.5 * cr, count: 1,
    });
  }
  return [...cells.values()].sort((a, b) => a.q - b.q || a.r - b.r);
}

export function hexagonVertices(cx: number, cy: number, radius: number): string {
  return Array.from({ length: 6 }, (_, i) => {
    const theta = (60 * i - 90) * Math.PI / 180;
    return (cx + radius * Math.cos(theta)) + "," + (cy + radius * Math.sin(theta));
  }).join(" ");
}
