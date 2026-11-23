import type { Dispatch, SetStateAction } from "react";
import { Popover, useOverlays } from "../overlays/overlays";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Spinner } from "../ui/Spinner";
import type { ComparisonModel } from "./composerStore";

/** Multi-model choices load only when Compare opens; 2–4 selectable routes. */
export function ModelComparisonPicker({
  comparisonMode, comparisonOptions, selectedComparisonKeys, selectedComparisonModels,
  comparisonLoading, comparisonError, comparisonKey, setSelectedComparisonKeys, onUseSingleModel,
}: {
  comparisonMode: boolean;
  comparisonOptions: ComparisonModel[];
  selectedComparisonKeys: string[];
  selectedComparisonModels: ComparisonModel[];
  comparisonLoading: boolean;
  comparisonError: string;
  comparisonKey: (model: ComparisonModel) => string;
  setSelectedComparisonKeys: Dispatch<SetStateAction<string[]>>;
  onUseSingleModel: () => void;
}) {
  const overlays = useOverlays();
  return (
    <Popover anchored name="model-comparison" label="Compare models"
      className="reasoning-popover model-picker-popover">
      <p>Choose 2–4 models. Each answer streams into its own canvas branch.</p>
      {comparisonLoading && <p role="status"><Spinner size={14} decorative /> Loading configured models…</p>}
      {comparisonError && <p role="alert">{comparisonError}</p>}
      {!comparisonLoading && !comparisonError && comparisonOptions.length === 0 && (
        <p role="status">No comparison models are configured. Add models in Settings.</p>
      )}
      <div className="model-picker-results" aria-label="Comparison model choices">
        {comparisonOptions.map((model) => {
          const key = comparisonKey(model);
          const checked = selectedComparisonKeys.includes(key);
          return (
            <Checkbox key={key} label={model.label} checked={checked}
              disabled={!checked && selectedComparisonKeys.length >= 4}
              onChange={() => setSelectedComparisonKeys((current) =>
                current.includes(key) ? current.filter((item) => item !== key)
                  : [...current, key].slice(0, 4)
              )} />
          );
        })}
      </div>
      <p role="status">
        {selectedComparisonModels.length}/4 selected
        {comparisonMode && selectedComparisonModels.length < 2 ? " · choose at least two" : ""}
      </p>
      <div className="composer-comparison-actions">
        <Button variant="ghost" onClick={() => { onUseSingleModel(); overlays.close(); }}>
          Use single-model send
        </Button>
        <Button variant="secondary" disabled={selectedComparisonModels.length < 2}
          onClick={() => overlays.close()}>Done</Button>
      </div>
    </Popover>
  );
}
