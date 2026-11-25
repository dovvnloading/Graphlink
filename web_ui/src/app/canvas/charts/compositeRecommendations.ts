/** Deterministic, inspectable suggestions for existing V2 source columns.
 * Hard statistical claims are deliberately absent: naming and monotonicity
 * heuristics select visual encodings, never estimate or invent observations.
 */
import type { ChartDataRow, ChartCompositeLayerRow } from "../../../lib/bridge-core/generated/scene-state";
import type { CompositeDesign } from "./compositeDesign";

export interface CompositeRecommendation {
  id: string;
  title: string;
  reason: string;
  score: number;
  design: CompositeDesign;
}

const norm = (key: string) => key.toLowerCase().replace(/[\s-]+/g, "_");
const measureScore = (key: string) => /response|signal|measure|metric|value|intensity|yield|count|rate|output/i.test(key) ? 2 : 0;
const timeScore = (key: string) => /time|hour|minute|second|date|year|day|step|sample_index|elapsed/i.test(key) ? 4 : 0;
const isLower = (key: string) => /(^|_)(low|lower|lower_bound|ci_low|lcl|lo)$/.test(norm(key));
const isUpper = (key: string) => /(^|_)(high|upper|upper_bound|ci_high|ucl|hi)$/.test(norm(key));

export function recommendCompositeDesigns(chart: ChartDataRow): CompositeRecommendation[] {
  const spec = chart.composite;
  if (!spec) return [];
  const names = Object.keys(spec.numbers);
  if (names.length < 2) return [];
  const x = [...names].sort((a, b) =>
    timeScore(b) - timeScore(a) || names.indexOf(a) - names.indexOf(b))[0];
  const values = spec.numbers[x];
  const isOrdered = values.every((v, i) => i === 0 || v >= values[i - 1]);
  const uniqueX = new Set(values).size;
  const lower = names.find(isLower);
  const upper = names.find(isUpper);
  const hasBand = !!lower && !!upper && lower !== upper
    && spec.numbers[lower].every((value, index) => value <= spec.numbers[upper][index]);
  const measurements = names.filter(name => name !== x && !(hasBand && (name === lower || name === upper)));
  if (!measurements.length) return [];
  const y = [...measurements].sort((a, b) =>
    measureScore(b) - measureScore(a) || names.indexOf(a) - names.indexOf(b))[0];
  const categories = Object.entries(spec.categories ?? {})
    .map(([name, entries]) => ({ name, count: new Set(entries).size }))
    .filter(({ count }) => count >= 2 && count <= 8);
  const group = categories[0]?.name;
  const facet = categories.find(({ count }) => count <= 6)?.name ?? null;
  const groupLayer = (mark: ChartCompositeLayerRow["mark"]): ChartCompositeLayerRow => ({
    mark, x, y, ...(group ? { colorBy: group } : {}),
  });
  const candidates: CompositeRecommendation[] = [];
  const push = (id: string, title: string, reason: string, score: number, design: CompositeDesign) => {
    candidates.push({ id, title, reason, score, design });
  };

  if (hasBand && lower && upper) {
    const band: ChartCompositeLayerRow = { mark: "band", x, y: lower, y2: upper,
      ...(group ? { colorBy: group } : {}), label: "Reported interval" };
    push("reported-interval", "Reported interval + measurements",
      "Both bound columns exist and every lower value is at or below its upper value; no interval is inferred.",
      100, { layers: [band, groupLayer("line"), groupLayer("point")], facetBy: facet });
  }
  if (isOrdered && uniqueX >= 3) {
    push("trend", group ? "Grouped trend + observations" : "Trend + observations",
      "X is nondecreasing in the source; connecting observations can reveal changes while points retain detail.",
      90, { layers: [groupLayer("line"), groupLayer("point")], facetBy: facet });
  }
  if (facet) {
    push("faceted", "Faceted relationship",
      "Categorical groups provide comparable small multiples with shared X/Y axes.",
      80, { layers: [groupLayer("point")], facetBy: facet });
  }
  push("scatter", "Relationship explorer",
    "Show the supplied numeric coordinates without connecting unrelated observations.",
    60, { layers: [groupLayer("point")], facetBy: null });
  const seen = new Set<string>();
  return candidates.sort((a, b) => b.score - a.score).filter(candidate => {
    const key = JSON.stringify(candidate.design);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 3);
}
