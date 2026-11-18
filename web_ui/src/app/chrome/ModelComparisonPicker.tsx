import type { Dispatch, SetStateAction } from "react";
import { Popover, useOverlays } from "../overlays/overlays";
import type { ComparisonModel } from "./composerStore";

/** The multi-model choices are loaded only when Compare is first opened. */
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
        <Popover anchored name="model-comparison" label="Compare models" className="reasoning-popover model-picker-popover">
          <p>Choose 2–4 models. Each answer streams into its own canvas branch.</p>
          {comparisonLoading && <p role="status">Loading configured models…</p>}
          {comparisonError && <p role="alert">{comparisonError}</p>}
          {!comparisonLoading && !comparisonError && comparisonOptions.length === 0 && (
            <p role="status">No comparison models are configured. Add models in Settings.</p>
          )}
          <div className="model-picker-results" aria-label="Comparison model choices">
            {comparisonOptions.map((model) => {
              const key = comparisonKey(model);
              const checked = selectedComparisonKeys.includes(key);
              return (
                <button
                  key={key}
                  type="button"
                  role="checkbox"
                  aria-checked={checked}
                  disabled={!checked && selectedComparisonKeys.length >= 4}
                  className={"reasoning-option" + (checked ? " active" : "")}
                  onClick={() => setSelectedComparisonKeys((current) =>
                    current.includes(key) ? current.filter((item) => item !== key) : [...current, key].slice(0, 4)
                  )}
                >
                  <span className="reasoning-option-label">{model.label}</span>
                  {checked && <span aria-hidden="true">✓</span>}
                </button>
              );
            })}
          </div>
          <p role="status">{selectedComparisonModels.length}/4 selected {comparisonMode && selectedComparisonModels.length < 2 ? "· choose at least two" : ""}</p>
          <button
            type="button"
            className="reasoning-option"
            onClick={() => { onUseSingleModel(); overlays.close(); }}
          >
            Use single-model send
          </button>
          <button
            type="button"
            className="reasoning-option"
            disabled={selectedComparisonModels.length < 2}
            onClick={() => overlays.close()}
          >
            Done
          </button>
        </Popover>
  );
}
