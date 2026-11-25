/** Deterministic V2 column-backed geometry, shared by full and compact SVG.
 * No script evaluation, network lookups, arbitrary callbacks, or data mutation.
 */
import type { ChartDataRow, ChartCompositeSpecRow, ChartCompositeLayerRow } from "../../../lib/bridge-core/generated/scene-state";
import { scientificDomain } from "./chartScales";
import { scientificMarkIndices } from "./scientificSampling";

export type CompositeSpec = ChartCompositeSpecRow;
export type CompositeLayer = ChartCompositeLayerRow;
export type CompositeFacet = string | null;
export interface CompositeGroup {
  key: string;
  group: string | null;
  indices: number[];
}

export function compositeFacets(spec: CompositeSpec): CompositeFacet[] {
  const field = spec.facetBy;
  return field && spec.categories?.[field] ? [...new Set(spec.categories[field])] : [null];
}

export function compositeDomains(data: ChartDataRow): {
  x: [number, number]; y: [number, number];
} {
  const spec = data.composite!;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const layer of spec.layers) {
    const xx = spec.numbers[layer.x];
    const yy = spec.numbers[layer.y];
    for (let i = 0; i < xx.length; i++) {
      minX = Math.min(minX, xx[i]);
      maxX = Math.max(maxX, xx[i]);
      minY = Math.min(minY, yy[i], layer.y2 ? spec.numbers[layer.y2][i] : yy[i]);
      maxY = Math.max(maxY, yy[i], layer.y2 ? spec.numbers[layer.y2][i] : yy[i]);
    }
    if (layer.mark === "area") { minY = Math.min(minY, 0); maxY = Math.max(maxY, 0); }
  }
  return {
    x: scientificDomain(minX, maxX, data.xScale ?? "linear"),
    y: scientificDomain(minY, maxY, data.yScale ?? "linear"),
  };
}

export function compositeGroups(spec: CompositeSpec, layer: CompositeLayer, facet: CompositeFacet): CompositeGroup[] {
  const num = spec.numbers[layer.x].length;
  const category = layer.colorBy ? spec.categories?.[layer.colorBy] : undefined;
  const facetValues = spec.facetBy ? spec.categories?.[spec.facetBy] : undefined;
  const grouped = new Map<string, CompositeGroup>();
  for (let i = 0; i < num; i++) {
    if (facetValues && facetValues[i] !== facet) continue;
    const group = category?.[i] ?? null;
    const key = group ?? "__all__";
    let record = grouped.get(key);
    if (!record) {
      record = { key, group, indices: [] };
      grouped.set(key, record);
    }
    record.indices.push(i);
  }
  for (const record of grouped.values()) {
    record.indices.sort((a, b) => spec.numbers[layer.x][a] - spec.numbers[layer.x][b] || a - b);
  }
  return [...grouped.values()];
}

/** Structured identity prevents equal category text in different fields from
 * silently sharing a color. Ungrouped layers have their own palette slots. */
export function compositeColorKey(layer: CompositeLayer, index: number, group: string | null): string {
  return layer.colorBy && group !== null ? JSON.stringify([layer.colorBy, group]) : JSON.stringify([index]);
}

export function compositeColorKeys(spec: CompositeSpec): { key: string; label: string }[] {
  const result = new Map<string, string>();
  spec.layers.forEach((layer, index) => {
    const values = layer.colorBy ? spec.categories?.[layer.colorBy] : undefined;
    if (values) {
      for (const value of values) {
        result.set(compositeColorKey(layer, index, value), layer.colorBy + ": " + value);
      }
    } else {
      result.set(compositeColorKey(layer, index, null),
        "Layer " + (index + 1) + ": " + (layer.label ?? layer.y));
    }
  });
  return Array.from(result, ([key, label]) => ({ key, label }));
}

export function compositeSample(spec: CompositeSpec, layer: CompositeLayer, indices: number[], budget: number): number[] {
  if (indices.length <= budget) return indices;
  const xx = indices.map(i => spec.numbers[layer.x][i]);
  const yy = indices.map(i => spec.numbers[layer.y][i]);
  const error = layer.y2 ? indices.map(i => [0, spec.numbers[layer.y2!][i] - spec.numbers[layer.y][i]]) : null;
  return scientificMarkIndices(xx, yy, undefined, budget, "line", error).map(i => indices[i]);
}
