import { useEffect, useMemo, useState } from "react";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { CustomSelect } from "./CustomSelect";

type EmbeddingSource = "off" | "ollama" | "cloud";

// A cold local Ollama embedding model may take minutes to load before replying.
const EMBEDDING_TEST_TIMEOUT_MS = 5 * 60_000;

const SOURCE_OPTIONS = [
  { id: "off", label: "Off (keyword search)", description: "Local keyword search only, without embeddings." },
  { id: "ollama", label: "Ollama (local)", description: "Use an installed embedding-capable Ollama model." },
  { id: "cloud", label: "Cloud (OpenAI-compatible)", description: "Requires consent to send indexed documents, saved graph/chat text, and queries." },
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
  // The parent keys this page by its *persisted* settings tuple: saving
  // or changing preferences in another window remounts this draft, while
  // a scan/test status publish never erases partially typed input.
  const savedSource = state.knowledgeEmbeddingSource;
  const [source, setSource] = useState<EmbeddingSource>(
    savedSource === "ollama" || savedSource === "cloud" ? savedSource : "off",
  );
  const [ollamaModel, setOllamaModel] = useState(
    savedSource === "ollama" ? state.knowledgeEmbeddingModel : "",
  );
  const [cloudModel, setCloudModel] = useState(
    savedSource === "cloud" ? state.knowledgeEmbeddingModel : "",
  );
  const [cloudProvider, setCloudProvider] = useState(state.knowledgeCloudProvider || "OpenAI");
  const [consent, setConsent] = useState(state.knowledgeCloudOptIn);

  // Show the models *installed in the running Ollama server*, rather than
  // guessing based on a local manifest or offering non-installed suggestions.
  useEffect(() => {
    if (source === "ollama") {
      transport.fireIntent("app-settings", "scanKnowledgeOllamaModels", []);
    }
  }, [source, transport]);

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

  const testEmbedding = () => {
    // The backend publishes the authoritative running/done/error status and
    // notice to app-settings. Unlike fireIntent(), request() does not turn a
    // missing reply into a global error toast: a cold Ollama model may finish
    // successfully even if this longer reply deadline is exceeded.
    void transport.request(
      "app-settings", "testKnowledgeEmbedding", args, EMBEDDING_TEST_TIMEOUT_MS,
    ).catch(() => {
      // The status publish, including a late success or a backend test error,
      // is what the user sees. Never leave a speculative timeout toast behind.
    });
  };

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
            I consent to sending indexed content and search queries to the selected cloud service.
          </label>
          <p className="settings-field-hint">
            With this opt-in, Graphlink sends full text chunks from indexed
            files and saved graphs (including chat messages, code, prompts, and
            notes), plus search queries, to your selected API endpoint.
            Embedding requests send text, not original file bytes or paths.
            Turning this off stops future requests, but cannot recall text
            already sent to the provider.
          </p>
        </fieldset>
      )}

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button"
          disabled={!canSubmit || state.knowledgeTestStatus === "running"}
          onClick={testEmbedding}
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
      {state.knowledgeTestStatus === "running" && (
        <p className="settings-update-status" role="status">
          Testing embedding connection... A cold local model may take several minutes to load.
        </p>
      )}
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
