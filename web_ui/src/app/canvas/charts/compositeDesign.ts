/** Only the declarative view binding is editable; column arrays stay unchanged. */
import type { ChartDataRow, ChartCompositeLayerRow } from "../../../lib/bridge-core/generated/scene-state";
import { canonicalizeChartSpec } from "../chartSpec";

export interface CompositeDesign {
  layers: ChartCompositeLayerRow[];
  facetBy?: string | null;
}

export function compositeDesignOf(data: ChartDataRow): CompositeDesign {
  if (!data.composite) throw new Error("Chart has no composite data");
  return {
    layers: data.composite.layers.map(layer => ({ ...layer })),
    facetBy: data.composite.facetBy ?? null,
  };
}

export function withCompositeDesign(data: ChartDataRow, design: CompositeDesign): ChartDataRow {
  if (!data.composite || data.type !== "composite") throw new Error("Composite figure required");
  const composite: NonNullable<ChartDataRow["composite"]> = {
    ...data.composite,
    layers: design.layers.map(layer => ({ ...layer })),
  };
  if (design.facetBy) composite.facetBy = design.facetBy;
  else delete composite.facetBy;
  return canonicalizeChartSpec({ ...data, composite }, "composite");
}
