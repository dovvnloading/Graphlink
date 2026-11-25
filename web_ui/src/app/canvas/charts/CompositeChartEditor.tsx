/** Data-preserving, keyboard-accessible editor for native ChartSpec V2.
 * Edits only mark bindings and facets; original columns are read-only.
 */
import { useState } from "react";
import { Button } from "../../ui/Button";
import type { ChartDataRow, ChartCompositeLayerRow } from "../../../lib/bridge-core/generated/scene-state";
import { compositeDesignOf, withCompositeDesign, type CompositeDesign } from "./compositeDesign";
import { recommendCompositeDesigns } from "./compositeRecommendations";

export function CompositeChartEditor({ chartData, onPreview, onSave, onClose }: {
  chartData: ChartDataRow;
  onPreview: (data: ChartDataRow | null) => void;
  onSave: (design: CompositeDesign, expected: CompositeDesign) => Promise<void>;
  onClose: () => void;
}) {
  // A new server scene snapshot wins over unsaved local edits; never reuse
  // an editing draft associated with an earlier data revision.
  const [local, setLocal] = useState<{ source: ChartDataRow; design: CompositeDesign } | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const spec = chartData.composite!;
  const draft = local?.source === chartData ? local.design : compositeDesignOf(chartData);
  const nums = Object.keys(spec.numbers);
  const cats = Object.keys(spec.categories ?? {});
  const numericOptions = nums.map(name => ({ value: name, label: name }));
  const categoricalOptions = [{ value: "", label: "None" }, ...cats.map(name => ({ value: name, label: name }))];
  const marks = ["point", "line", "band", "area"] as const;
  const canSave = draft.layers.length > 0;
  const recommendations = recommendCompositeDesigns(chartData);

  function modify(next: CompositeDesign) {
    setLocal({ source: chartData, design: next });
    setError("");
    try { onPreview(withCompositeDesign(chartData, next)); }
    catch { onPreview(null); }
  }

  function changeLayer(index: number, change: Partial<ChartCompositeLayerRow>) {
    const layers = draft.layers.map((layer, i) => {
      if (i !== index) return layer;
      const next = { ...layer, ...change };
      if (next.mark !== "band") delete next.y2;
      if (next.mark === "band" && !next.y2) next.y2 = nums.find(name => name !== next.y) ?? next.y;
      if (!next.colorBy) delete next.colorBy;
      if (!next.label) delete next.label;
      return next;
    });
    modify({ ...draft, layers });
  }

  async function apply() {
    if (!canSave || saving) return;
    try {
      const normalized = withCompositeDesign(chartData, draft);
      setSaving(true);
      setError("");
      await onSave({
        layers: normalized.composite!.layers, facetBy: normalized.composite!.facetBy ?? null,
      }, compositeDesignOf(chartData));
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save figure design");
    } finally {
      setSaving(false);
    }
  }

  return <section className="chart-composite-editor nodrag nowheel nopan" aria-label="Composite figure editor">
    <div className="chart-composite-editor-heading">
      <strong>Composite figure layers</strong>
      <span>Data columns are read-only; edits are undoable.</span>
    </div>
    {recommendations.length > 0 && <div className="chart-composite-recommendations" aria-label="Suggested figure designs">
      <strong>Suggested views</strong>
      {recommendations.map(suggestion =>
        <div key={suggestion.id} className="chart-composite-recommendation">
          <Button size="sm" variant="ghost" onClick={() => modify(suggestion.design)}>
            {suggestion.title}
          </Button>
          <span>{suggestion.reason}</span>
        </div>)}
    </div>}
    <label>Facet panels
      <select aria-label="Facet panels" value={draft.facetBy ?? ""}
        onChange={event => modify({ ...draft, facetBy: event.currentTarget.value || null })}>
        {categoricalOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
    {draft.layers.map((layer, index) =>
      <fieldset key={index} className="chart-composite-edit-layer">
        <legend>{"Layer " + (index + 1)}</legend>
        <label>Mark
          <select aria-label={`Mark ${index + 1}`} value={layer.mark}
            onChange={event => changeLayer(index, { mark: event.currentTarget.value as ChartCompositeLayerRow["mark"] })}>
            {marks.map(mark => <option key={mark} value={mark}>{mark}</option>)}
          </select>
        </label>
        <label>X
          <select aria-label={`X ${index + 1}`} value={layer.x}
            onChange={event => changeLayer(index, { x: event.currentTarget.value })}>
            {numericOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label>Y
          <select aria-label={`Y ${index + 1}`} value={layer.y}
            onChange={event => changeLayer(index, { y: event.currentTarget.value })}>
            {numericOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        {layer.mark === "band" && <label>Upper bound
          <select aria-label={`Upper bound ${index + 1}`} value={layer.y2 ?? ""}
            onChange={event => changeLayer(index, { y2: event.currentTarget.value })}>
            {numericOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>}
        <label>Color by
          <select aria-label={`Color by ${index + 1}`} value={layer.colorBy ?? ""}
            onChange={event => changeLayer(index, { colorBy: event.currentTarget.value || undefined })}>
            {categoricalOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label>Legend label
          <input aria-label={`Legend label ${index + 1}`} maxLength={160}
            value={layer.label ?? ""} onChange={event => changeLayer(index, { label: event.currentTarget.value })} />
        </label>
        <Button size="sm" variant="ghost" disabled={draft.layers.length === 1}
          onClick={() => modify({ ...draft, layers: draft.layers.filter((_, i) => i !== index) })}>
          {"Remove layer " + (index + 1)}
        </Button>
      </fieldset>
    )}
    <div className="chart-composite-editor-actions">
      <Button size="sm" disabled={draft.layers.length >= 8}
        onClick={() => modify({ ...draft, layers: [...draft.layers, {
          mark: "point", x: nums[0], y: nums[1],
        }] })}>Add layer</Button>
      <Button size="sm" disabled={!canSave || saving} onClick={() => { void apply(); }}>
        {saving ? "Saving…" : "Save figure design"}
      </Button>
      <Button size="sm" variant="ghost" disabled={saving} onClick={onClose}>Cancel</Button>
    </div>
    {error && <div role="alert">{error}</div>}
  </section>;
}
