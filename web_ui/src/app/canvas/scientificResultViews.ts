/** Receipt-protected scientific views. No arbitrary HTML, colors or SVG paths from plugins. */
export interface SciencePlateWell { well: string; value: number | string | null }
export interface SciencePlateMapView {
  type: "plate_map"; title: string; format: 96 | 384; unit?: string; wells: SciencePlateWell[];
}
export interface ScienceSurvivalGroup {
  label: string; times: number[]; survival: number[]; censored?: number[];
}
export interface ScienceSurvivalCurveView {
  type: "survival_curve"; title: string; xLabel: string; groups: ScienceSurvivalGroup[];
}
export type ScienceResultView = SciencePlateMapView | ScienceSurvivalCurveView;

const record = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const label = (v: unknown, max = 160): v is string =>
  typeof v === "string" && !!v.trim() && v.length <= max;
const keys = (v: Record<string, unknown>, allowed: string[]): boolean =>
  Object.keys(v).every((key) => allowed.includes(key));

export function isScienceResultView(v: unknown): v is ScienceResultView {
  if (!record(v) || !label(v.title)) return false;
  if (v.type === "plate_map") {
    if (!keys(v, ["type", "title", "format", "unit", "wells"])) return false;
    if (v.format !== 96 && v.format !== 384) return false;
    if (v.unit !== undefined && (typeof v.unit !== "string" || v.unit.length > 80)) return false;
    if (!Array.isArray(v.wells) || v.wells.length > v.format) return false;
    const rows = v.format === 96 ? 8 : 16, cols = v.format === 96 ? 12 : 24;
    const seen = new Set<string>();
    for (const well of v.wells) {
      if (!record(well) || Object.keys(well).length !== 2 ||
          !Object.hasOwn(well, "well") || !Object.hasOwn(well, "value") ||
          typeof well.well !== "string") return false;
      const match = /^([A-P])([1-9][0-9]?)$/.exec(well.well);
      if (!match || match[1].charCodeAt(0) - 65 >= rows || Number(match[2]) > cols ||
          seen.has(well.well)) return false;
      seen.add(well.well);
      if (well.value !== null && !finite(well.value) &&
          !(typeof well.value === "string" && well.value.length > 0 && well.value.length <= 128)) return false;
    }
    return true;
  }
  if (v.type !== "survival_curve" ||
      !keys(v, ["type", "title", "xLabel", "groups"]) || !label(v.xLabel) ||
      !Array.isArray(v.groups) || v.groups.length < 2 || v.groups.length > 12) return false;
  const labels = new Set<string>();
  let points = 0;
  for (const group of v.groups) {
    if (!record(group) || !keys(group, ["label", "times", "survival", "censored"]) ||
        !label(group.label, 80) || labels.has(group.label) ||
        !Array.isArray(group.times) || !Array.isArray(group.survival) ||
        !group.times.length || group.times.length !== group.survival.length) return false;
    labels.add(group.label);
    points += group.times.length;
    if (points > 4096 || group.times[0] !== 0 || group.survival[0] !== 1) return false;
    for (let i = 0; i < group.times.length; i++) {
      const t = group.times[i], p = group.survival[i];
      if (!finite(t) || t < 0 || !finite(p) || p < 0 || p > 1) return false;
      if (i > 0 && (t < group.times[i - 1] || (i > 1 && t === group.times[i - 1]) ||
          p > group.survival[i - 1])) return false;
    }
    const censored = group.censored;
    if (censored !== undefined && (!Array.isArray(censored) ||
        censored.length > group.times.length ||
        censored.some((t: unknown, i: number) => !finite(t) ||
          !(group.times as number[]).includes(t) ||
          (i > 0 && t <= censored[i - 1])))) return false;
  }
  return true;
}
