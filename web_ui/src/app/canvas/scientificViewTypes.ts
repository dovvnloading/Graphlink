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
export interface ScienceForestEstimate {
  label: string; estimate: number; lower: number; upper: number; weight?: number;
}
export interface ScienceForestPlotView {
  type: "forest_plot"; title: string; xLabel: string; scale: "linear" | "log";
  reference?: number; studies: ScienceForestEstimate[];
  pooled: { label?: string; estimate: number; lower: number; upper: number };
}
export interface SciencePhylogeneticNode {
  name?: string; length?: number; support?: number; children?: SciencePhylogeneticNode[];
}
export interface SciencePhylogeneticTreeView {
  type: "phylogenetic_tree"; title: string; layout: "rectangular" | "circular";
  mode: "phylogram" | "cladogram"; tree: SciencePhylogeneticNode;
}
export interface ScienceVolcanoPoint {
  gene: string; log2FoldChange: number; qValue: number;
}
export interface ScienceVolcanoPlotView {
  type: "volcano_plot"; title: string; xLabel: string;
  fdrThreshold: number; log2FoldChangeThreshold: number; points: ScienceVolcanoPoint[];
}

export type ScienceResultView =
  SciencePlateMapView | ScienceSurvivalCurveView | ScienceForestPlotView | SciencePhylogeneticTreeView | ScienceVolcanoPlotView;

const record = (v: unknown): v is Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const label = (v: unknown, max = 160): v is string =>
  typeof v === "string" && !!v.trim() && v.length <= max;
const keys = (v: Record<string, unknown>, allowed: string[]): boolean =>
  Object.keys(v).every((key) => allowed.includes(key));


function forestInterval(v: unknown, logScale: boolean, pooled: boolean): boolean {
  if (!record(v) || !keys(v, pooled
    ? ["label", "estimate", "lower", "upper"]
    : ["label", "estimate", "lower", "upper", "weight"])) return false;
  if ((!pooled || v.label !== undefined) && !label(v.label, 80)) return false;
  if (!finite(v.lower) || !finite(v.estimate) || !finite(v.upper) ||
      v.lower > v.estimate || v.estimate > v.upper || (logScale && v.lower <= 0)) return false;
  if (!pooled && v.weight !== undefined && (!finite(v.weight) || v.weight < 0)) return false;
  return true;
}

function forestView(v: Record<string, unknown>): boolean {
  if (!keys(v, ["type", "title", "xLabel", "scale", "reference", "studies", "pooled"]) ||
      !label(v.xLabel) || (v.scale !== "linear" && v.scale !== "log") ||
      !Array.isArray(v.studies) || v.studies.length < 1 || v.studies.length > 64) return false;
  const logScale = v.scale === "log";
  const reference = v.reference === undefined ? (logScale ? 1 : 0) : v.reference;
  if (!finite(reference) || (logScale && reference <= 0)) return false;
  const names = new Set<string>();
  for (const study of v.studies) {
    if (!forestInterval(study, logScale, false)) return false;
    const name = (study as ScienceForestEstimate).label;
    if (names.has(name)) return false;
    names.add(name);
  }
  return forestInterval(v.pooled, logScale, true);
}

function phylogeneticView(v: Record<string, unknown>): boolean {
  if (!keys(v, ["type", "title", "layout", "mode", "tree"]) ||
      (v.layout !== "rectangular" && v.layout !== "circular") ||
      (v.mode !== "phylogram" && v.mode !== "cladogram")) return false;
  const seen = new Set<object>(), taxa = new Set<string>();
  let count = 0, positiveLength = false;
  const visit = (node: unknown, depth: number): boolean => {
    if (!record(node) || !keys(node, ["name", "length", "support", "children"]) ||
        seen.has(node) || ++count > 127 || depth > 32) return false;
    seen.add(node);
    if (node.name !== undefined && !label(node.name, 80)) return false;
    if (depth === 0) {
      if (node.length !== undefined || node.support !== undefined ||
          Object.hasOwn(node, "length") || Object.hasOwn(node, "support")) return false;
    } else {
      if (v.mode === "phylogram" && !Object.hasOwn(node, "length")) return false;
      if (Object.hasOwn(node, "length") && (!finite(node.length) || node.length < 0)) return false;
      if (typeof node.length === "number" && node.length > 0) positiveLength = true;
      if (Object.hasOwn(node, "support") &&
          (!finite(node.support) || node.support < 0 || node.support > 100)) return false;
    }
    if (!Object.hasOwn(node, "children")) {
      if (!label(node.name, 80) || taxa.has(node.name) || taxa.size >= 64) return false;
      taxa.add(node.name);
      return true;
    }
    return Array.isArray(node.children) && node.children.length >= 2 &&
      node.children.length <= 16 && node.children.every((child: unknown) => visit(child, depth + 1));
  };
  return visit(v.tree, 0) && record(v.tree) && Array.isArray(v.tree.children) &&
    taxa.size >= 2 && (v.mode !== "phylogram" || positiveLength);
}

function volcanoView(v: Record<string, unknown>): boolean {
  if (!keys(v, ["type", "title", "xLabel", "fdrThreshold", "log2FoldChangeThreshold", "points"]) ||
      !label(v.xLabel) || !finite(v.fdrThreshold) || v.fdrThreshold <= 0 || v.fdrThreshold >= 1 ||
      !finite(v.log2FoldChangeThreshold) || v.log2FoldChangeThreshold < 0 ||
      v.log2FoldChangeThreshold > 100 || !Array.isArray(v.points) || v.points.length > 1024) return false;
  const seen = new Set<string>();
  for (const point of v.points) {
    if (!record(point) || Object.keys(point).length !== 3 ||
        !keys(point, ["gene", "log2FoldChange", "qValue"]) ||
        !label(point.gene, 80) || seen.has(point.gene) || !finite(point.log2FoldChange) ||
        !finite(point.qValue) || point.qValue < 0 || point.qValue > 1) return false;
    seen.add(point.gene);
  }
  return true;
}

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
  if (v.type === "volcano_plot") return volcanoView(v);
  if (v.type === "forest_plot") return forestView(v);
  if (v.type === "phylogenetic_tree") return phylogeneticView(v);
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
