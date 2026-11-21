import { useEffect, useMemo, useState } from "react";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { CustomSelect } from "./CustomSelect";

type EmbeddingSource = "off" | "ollama" | "cloud";

const SOURCE_OPTIONS = [
  { id: "off", label: "Off (keyword search)", description: "Local keyword search only, without embeddings." },
  { id: "ollama", label: "Ollama (local)", description: "Use an installed embedding-capable Ollama model." },
  { id: "cloud", label: "Cloud (OpenAI-compatible)", description: "Requires explicit consent to send document text." },
];

function suggestedModel(models: string[], capable: string[]): string {
  return models.find((name) =>
    capable.includes(name) && /^nomic-embed-text(?::|$)/i.test(name)
  ) ?? models.find((name) => capable.includes(name)) ?? "";
}

/** Knowledge search has its own provider choice; it never changes chat's model. */
export function KnowledgeSettingsPage({
  state, transport,
}: {
  state: AppSettingsState;
  transport: WsTransport;
}) {
  const [source, setSource] = useState<EmbeddingSource>("off");
  const [ollamaModel, setOllamaModel] = useState("");
  const [cloudModel, setCloudModel] = useState("");
  const [cloudProvider, setCloudProvider] = useState("OpenAI");
  const [consent, setConsent] = useState(false);

  // Sync only when persisted preferences change, not on model-scan or test
  // status publishes. Those must not erase a model name someone is typing.
  useEffect(() => {
    const savedSource = state.knowledgeEmbeddingSource as EmbeddingSource;
    setSource(["off", "ollama", "cloud"].includes(savedSource) ? savedSource : "off");
    if (savedSource === "ollama") setOllamaModel(state.knowledgeEmbeddingModel);
    if (savedSource === "cloud") setCloudModel(state.knowledgeEmbeddingModel);
    setCloudProvider(state.knowledgeCloudProvider || "OpenAI");
    setConsent(state.knowledgeCloudOptIn);
  }, [
    state.knowledgeEmbeddingSource, state.knowledgeEmbeddingModel,
    state.knowledgeCloudProvider, state.knowledgeCloudOptIn,
  ]);

  // Show the models *installed in the running Ollama server*, rather than
  // guessing based on a local manifest or offering non-installed suggestions.
  useEffect(() => {
    transport.fireIntent("app-settings", "scanKnowledgeOllamaModels", []);
  }, [transport]);

  const capable = state.knowledgeOllamaCapableModels;
  const available = useMemo(
    () => [...state.knowledgeOllamaModels].sort((a, b) => {
      const aCapable = capable.includes(a);
      const bCapable = capable.includes(b);
      if (aCapable !== bCapable) return aCapable ? -1 : 1;
      const aSuggested = /^nomic-embed-text(?::|$)/i.test(a);
      const bSuggested = /^nomic-embed-text(?::|$)/i.test(b);
      if (aCapable && aSuggested !== bSuggested) return aSuggested ? -1 : 1;
      return a.localeCompare(b);
    }),
    [state.knowledgeOllamaModels, capable],
  );

  const localChoice = ollamaModel || suggestedModel(available, capable);
  const cloudProfiles = state.apiProviders.filter(
    (provider) => provider.id !== "Anthropic Claude" && provider.id !== "Google Gemini",
  );
  const usableCloudProvider = cloudProfiles.some((provider) => provider.id === cloudProvider)
    ? cloudProvider : cloudProfiles[0]?.id ?? "";
  const selectedModel = source === "ollama" ? localChoice : source === "cloud" ? cloudModel.trim() : "";
  const canSubmit = source === "off" || (
    source === "ollama" ? !!selectedModel && capable.includes(selectedModel) :
      !!selectedModel && !!usableCloudProvider && consent
  );
  const args: [string, string, string, boolean] = [
    source, selectedModel, source === "cloud" ? usableCloudProvider : "",
    source === "cloud" && consent,
  ];

  const selectSource = (id: string) => {
    if (id === "off" || id === "ollama" || id === "cloud") {
      setSource(id);
      // Do not carry consent to another source on an unsaved switch.
      if (id !== "cloud") setConsent(false);
    }
  };

  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        Choose how Graphlink searches ingested knowledge. Off is the default:
        keyword search stays on your machine. Embedding models add semantic
        retrieval, independently of your chat model selection.
      </p>

      <div className="settings-field">
        <span className="settings-field-label">Embedding source</span>
        <CustomSelect
          ariaLabel="Embedding source"
          options={SOURCE_OPTIONS}
          value={source}
          onChange={selectSource}
        />
      </div>

      {source === "off" && (
        <p className="settings-field-hint">
          Keyword-only search uses the local FTS5 index. No document embeddings
          are generated or sent to an embedding provider.
        </p>
      )}

      {source === "ollama" && (
        <fieldset className="settings-fieldset">
          <legend>Local Ollama embedding model</legend>
          <label className="settings-field">
            <span className="settings-field-label">Installed model</span>
            <select
              aria-label="Installed Ollama embedding model"
              className="settings-select"
              value={localChoice}
              onChange={(event) => setOllamaModel(event.target.value)}
            >
              {!localChoice && <option value="">Choose an installed model</option>}
              {localChoice && !available.includes(localChoice) && (
                <option value={localChoice} disabled>{localChoice} (not installed)</option>
              )}
              {available.map((model) => (
                <option key={model} value={model} disabled={!capable.includes(model)}>
                  {model}
                  {capable.includes(model)
                    ? (/^nomic-embed-text(?::|$)/i.test(model) ? " (recommended)" : " (embeddings)")
                    : " (does not support embeddings)"}
                </option>
              ))}
            </select>
          </label>
          <p className="settings-field-hint">
            Embedding-capable models appear first. nomic-embed-text is recommended
            when installed. Other installed models are shown but cannot be chosen.
          </p>
          <div className="settings-button-row">
            <button
              type="button"
              className="settings-button"
              disabled={state.knowledgeScanStatus === "running"}
              onClick={() => transport.fireIntent("app-settings", "scanKnowledgeOllamaModels", [])}
            >
              {state.knowledgeScanStatus === "running" ? "Scanning..." : "Refresh installed models"}
            </button>
          </div>
          {state.knowledgeScanStatus === "done" && capable.length === 0 && (
            <p className="settings-update-status">
              No embedding-capable model installed. You can pull nomic-embed-text
              on the Ollama (Local) page.
            </p>
          )}
        </fieldset>
      )}

      {source === "cloud" && (
        <fieldset className="settings-fieldset">
          <legend>OpenAI-compatible embedding service</legend>
          <p className="settings-field-hint">
            Uses an existing connection and API key from Settings &gt; API Endpoint.
            Embedding requests may incur provider charges.
          </p>
          <div className="settings-field">
            <span className="settings-field-label">API provider</span>
            <CustomSelect
              ariaLabel="Knowledge cloud API provider"
              value={usableCloudProvider}
              onChange={setCloudProvider}
              options={cloudProfiles.map((provider) => ({
                id: provider.id, label: provider.label || provider.id,
              }))}
            />
          </div>
          <label className="settings-field">
            <span className="settings-field-label">Embedding model ID</span>
            <input
              aria-label="Cloud embedding model ID"
              className="settings-select"
              type="text"
              value={cloudModel}
              placeholder="e.g. text-embedding-3-small"
              onChange={(event) => setCloudModel(event.target.value)}
            />
          </label>
          <label className="settings-checkbox-row">
            <input
              type="checkbox"
              checked={consent}
              onChange={(event) => setConsent(event.target.checked)}
            />
            I understand that document text leaves the machine.
          </label>
          <p className="settings-field-hint">
            This is an explicit opt-in: Graphlink will send document passages
            and search queries to your selected API endpoint for embeddings.
            Turning this option off stops future embedding requests.
          </p>
        </fieldset>
      )}

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button"
          disabled={!canSubmit || state.knowledgeTestStatus === "running"}
          onClick={() => transport.fireIntent("app-settings", "testKnowledgeEmbedding", args)}
        >
          {state.knowledgeTestStatus === "running" ? "Testing..." : "Test"}
        </button>
        <button
          type="button"
          className="settings-button settings-button-primary"
          disabled={!canSubmit}
          onClick={() => transport.fireIntent("app-settings", "setKnowledgeEmbedding", args)}
        >
          Save choice
        </button>
      </div>
      <p className="settings-field-hint">
        Test sends only a sample phrase, never a stored document.
        New or changed embedding sources build their own cache as needed.
      </p>
      {state.knowledgeNotice && (
        <p
          className="settings-update-status"
          role="status"
          data-level={
            state.knowledgeTestStatus === "error" || state.knowledgeScanStatus === "error"
              ? "error" : "info"
          }
        >
          {state.knowledgeNotice}
        </p>
      )}
    </div>
  );
}
