import { useRef, useState, type FormEvent } from "react";
import type {
  AppSettingsState, EffectiveModelPrice, ModelPricingOverride,
} from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import "./ModelPricingSettings.css";

type Prices = Record<string, { input: number; output: number }>;

function formatRate(price: number | null | undefined): string {
  return price == null ? "Unknown" : "$" + price.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

function tableFrom(overrides: ModelPricingOverride[]): Prices {
  return Object.fromEntries(overrides.map((row) => [
    row.modelId, { input: row.inputPerMillion, output: row.outputPerMillion },
  ]));
}

function sourceLabel(source: EffectiveModelPrice["source"]): string {
  if (source === "local") return "Local · free";
  if (source === "override") return "Your override";
  if (source === "built-in") return "Built-in table";
  return "No known price";
}

/** Local-first settings editor; only the validated app-settings intent persists data. */
export function ModelPricingSettings({ state, transport }: {
  state: AppSettingsState;
  transport: WsTransport;
}) {
  const overrides = state.pricingOverrides ?? [];
  const rows = state.effectiveModelPrices ?? [];
  const [modelId, setModelId] = useState("");
  const [inputDraft, setInputDraft] = useState("");
  const [outputDraft, setOutputDraft] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const modelInput = useRef<HTMLInputElement>(null);

  function clear() {
    setEditing(null);
    setModelId("");
    setInputDraft("");
    setOutputDraft("");
  }

  function edit(row: EffectiveModelPrice) {
    setModelId(row.modelId);
    setEditing(overrides.some((entry) => entry.modelId === row.modelId.toLowerCase())
      ? row.modelId.toLowerCase() : null);
    setInputDraft(row.inputPerMillion == null ? "" : String(row.inputPerMillion));
    setOutputDraft(row.outputPerMillion == null ? "" : String(row.outputPerMillion));
    setError("");
    setNotice("");
    modelInput.current?.focus();
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const id = modelId.trim();
    const input = Number(inputDraft);
    const output = Number(outputDraft);
    if (!id) { setError("Enter a non-empty model ID."); return; }
    if (!inputDraft.trim() || !outputDraft.trim()
      || !Number.isFinite(input) || !Number.isFinite(output) || input < 0 || output < 0) {
      setError("Enter finite, non-negative input and output prices.");
      return;
    }
    if (rows.some((row) => row.source === "local" && row.modelId.toLowerCase() === id.toLowerCase())) {
      setError("Local Ollama and llama.cpp models are always free.");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    const next = tableFrom(overrides);
    next[id.toLowerCase()] = { input, output };
    try {
      await transport.request("app-settings", "setPricingOverrides", [next]);
      clear();
      setNotice("Model pricing saved. Chat and Re-run estimates use the new prices.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save model pricing.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(model: string) {
    if (saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    const next = tableFrom(overrides);
    delete next[model.toLowerCase()];
    try {
      await transport.request("app-settings", "setPricingOverrides", [next]);
      if (editing === model.toLowerCase()) clear();
      setNotice("Price override removed. Built-in pricing now applies where available.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not remove price override.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-general-page settings-model-pricing">
      <p className="settings-integrations-intro">
        Prices are in USD per million input and output tokens. Estimates are not bills.
        Ollama and llama.cpp models run locally and always have $0 token cost.
        Overrides match the model ID exactly (case-insensitive); built-in pricing is used otherwise.
      </p>
      <fieldset className="settings-fieldset">
        <legend>Effective model prices</legend>
        {rows.length === 0 && (
          <p className="settings-update-status">
            No configured or recently used models yet. Add an override using a model ID below.
          </p>
        )}
        <ul className="settings-model-pricing-list" aria-label="Effective model prices">
          {rows.map((row) => {
            const overridden = overrides.some((value) => value.modelId === row.modelId.toLowerCase());
            const rowLabel = row.provider ? row.provider + " / " + row.modelId : row.modelId;
            return (
              <li className="settings-model-pricing-row" key={row.provider + ":" + row.modelId}>
                <div className="settings-model-pricing-detail">
                  <strong className="settings-model-pricing-name">{row.modelId}</strong>
                  <span className="settings-update-status">
                    {row.provider || "Custom model"} · {sourceLabel(row.source)}
                  </span>
                  <span className="settings-model-pricing-rates">
                    Input: {formatRate(row.inputPerMillion)} · Output: {formatRate(row.outputPerMillion)}
                  </span>
                </div>
                {row.source !== "local" && (
                  <div className="settings-button-row settings-model-pricing-actions">
                    <button type="button" className="settings-button" disabled={saving}
                      aria-label={`Edit pricing for ${rowLabel}`} onClick={() => edit(row)}>
                      Edit
                    </button>
                    {overridden && (
                      <button type="button" className="settings-button" disabled={saving}
                        aria-label={`Remove override for ${rowLabel}`}
                        onClick={() => { void remove(row.modelId); }}>Remove</button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </fieldset>

      <form className="settings-fieldset settings-model-pricing-form" onSubmit={(event) => { void save(event); }} noValidate>
        <fieldset className="settings-model-pricing-inputs" disabled={saving}>
          <legend>{editing ? "Edit model override" : "Add or update model override"}</legend>
          <label className="settings-field">
            <span className="settings-field-label">Model ID</span>
            <input ref={modelInput} className="settings-select" type="text" value={modelId}
              readOnly={editing !== null} autoComplete="off" required
              onChange={(event) => setModelId(event.target.value)}
              placeholder="e.g. gpt-4o-mini" />
          </label>
          <label className="settings-field">
            <span className="settings-field-label">Input (USD / million tokens)</span>
            <input className="settings-select" type="number" min="0" step="any" required
              value={inputDraft} onChange={(event) => setInputDraft(event.target.value)} />
          </label>
          <label className="settings-field">
            <span className="settings-field-label">Output (USD / million tokens)</span>
            <input className="settings-select" type="number" min="0" step="any" required
              value={outputDraft} onChange={(event) => setOutputDraft(event.target.value)} />
          </label>
          <div className="settings-button-row">
            <button type="submit" className="settings-button">{saving ? "Saving…" : "Save override"}</button>
            <button type="button" className="settings-button" onClick={() => { clear(); setError(""); setNotice(""); }}>
              Cancel edit
            </button>
          </div>
        </fieldset>
        {error && <p className="settings-model-pricing-error" role="alert">{error}</p>}
        {notice && <p className="settings-update-status" role="status">{notice}</p>}
      </form>
    </div>
  );
}
