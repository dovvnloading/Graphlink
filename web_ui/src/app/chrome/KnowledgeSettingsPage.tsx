import { useEffect, useMemo, useState } from "react";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import { WsTimeoutError, WsUnavailableError, type WsTransport } from "../../lib/ws/transport";
import { Select } from "../ui/Select";
import { TextField } from "../ui/TextField";
import { Switch } from "../ui/Switch";
import { Button } from "../ui/Button";
import { FieldGroup, FormRow } from "../ui/Field";
import { Spinner } from "../ui/Spinner";

type EmbeddingSource = "off" | "ollama" | "cloud";

// A cold local Ollama embedding model may take minutes to load before replying.
const EMBEDDING_TEST_TIMEOUT_MS = 5 * 60_000;

const SOURCE_OPTIONS = [
  { value: "off", label: "Off (keyword search)", description: "Local keyword search only, without embeddings." },
  { value: "ollama", label: "Ollama (local)", description: "Use an installed embedding-capable Ollama model." },
  { value: "cloud", label: "Cloud (OpenAI-compatible)", description: "Requires consent to send indexed documents, saved graph/chat text, and queries." },
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

  // Share Settings > Ollama's inventory and refresh. Offline folder
  // scans have unknown capabilities, so they cannot enable embeddings.
  useEffect(() => {
    if (source === "ollama") {
      transport.fireIntent("app-settings", "scanOllamaSystem", []);
    }
  }, [source, transport]);

  const capable = useMemo(
    () => state.ollamaInventory.filter((entry) => entry.capabilities.includes("embeddings")).map((entry) => entry.name),
    [state.ollamaInventory],
  );
  const available = useMemo(
    () => state.ollamaInventory.map((entry) => entry.name).sort((a, b) => {
      const aCapable = capable.includes(a);
      const bCapable = capable.includes(b);
      if (aCapable !== bCapable) return aCapable ? -1 : 1;
      const aSuggested = /^nomic-embed-text(?::|$)/i.test(a);
      const bSuggested = /^nomic-embed-text(?::|$)/i.test(b);
      if (aCapable && aSuggested !== bSuggested) return aSuggested ? -1 : 1;
      return a.localeCompare(b);
    }),
    [state.ollamaInventory, capable],
  );

  const localChoice = ollamaModel || suggestedModel(available, capable);
  const localMetadata = state.ollamaInventory.find((entry) => entry.name === localChoice);
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
    ).catch((error: unknown) => {
      // A missing reply does not mean the test failed: its eventual result
      // still arrives through app-settings. Connection loss already has its
      // own indicator; keep genuine protocol errors visible in the usual way.
      if (error instanceof WsTimeoutError || error instanceof WsUnavailableError) return;
      transport.intent("notification", "showError", [
        error instanceof Error ? error.message : "Could not start embedding test.",
      ]);
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
      <h2 className="settings-page-title">Knowledge</h2>
      <p className="settings-page-description">
        Choose how Graphlink searches ingested knowledge. Off is the default:
        keyword search stays on your machine. Embedding models add semantic
        retrieval, independently of your chat model selection.
      </p>

      <FieldGroup title="Embedding source">
        <FormRow label="Embedding source" layout="inline">
          <Select ariaLabel="Embedding source" options={SOURCE_OPTIONS} value={source} onChange={selectSource} />
        </FormRow>
      </FieldGroup>

      {source === "off" && (
        <p className="settings-field-hint">
          Keyword-only search uses the local FTS5 index. No document embeddings
          are generated or sent to an embedding provider.
        </p>
      )}

      {source === "ollama" && (
        <FieldGroup title="Local Ollama embedding model">
          <FormRow label="Installed model" layout="inline">
            <Select ariaLabel="Installed Ollama embedding model"
              value={localChoice}
              onChange={setOllamaModel}
              options={[
                ...(!localChoice ? [{ value: "", label: "Choose an installed model" }] : []),
                ...(localChoice && !available.includes(localChoice) ? [{
                  value: localChoice, label: localChoice + " (not installed)", disabled: true,
                }] : []),
                ...available.map((model) => ({
                  value: model,
                  label: model,
                  description: capable.includes(model)
                    ? (/^nomic-embed-text(?::|$)/i.test(model) ? "Recommended" : "Embeddings")
                    : state.ollamaInventory.find((entry) => entry.name === model)?.capabilities.length
                      ? "Does not support embeddings" : "Embedding support unverified",
                  disabled: !capable.includes(model),
                })),
              ]} />
          </FormRow>
          <p className="settings-field-hint">
            Embedding-capable models appear first. nomic-embed-text is recommended
            when installed. Other installed models are shown but cannot be chosen.
          </p>
          {localMetadata && (
            <p className="settings-field-hint">
              {localMetadata.family || "Unknown family"} ·
              {" "}{localMetadata.parameterSize || "Unknown parameters"} ·
              {" "}Trained context: {localMetadata.trainedContextLength?.toLocaleString() ?? "Unknown"} tokens
            </p>
          )}
          {state.ollamaInventorySource === "folder" && (
            <p className="settings-field-hint">
              Folder-scanned models cannot verify embedding support. Refresh from the running server.
            </p>
          )}
          <div className="settings-button-row">
            <Button variant="secondary"
              disabled={state.ollamaScanStatus === "running"}
              onClick={() => transport.fireIntent("app-settings", "scanOllamaSystem", [])}
            >
              {state.ollamaScanStatus === "running" ? <><Spinner size={14} decorative /> Scanning…</> : "Refresh installed models"}
            </Button>
          </div>
          {state.knowledgeScanStatus === "done" && state.ollamaServerReachable === true && capable.length === 0 && (
            <p className="settings-update-status">
              No embedding-capable model confirmed. You can pull nomic-embed-text
              on the Ollama (Local) page.
            </p>
          )}
        </FieldGroup>
      )}

      {source === "cloud" && (
        <FieldGroup title="OpenAI-compatible embedding service">
          <p className="settings-field-hint">
            Uses an existing connection and API key from Settings &gt; API Endpoint.
            Embedding requests may incur provider charges.
          </p>
          <FormRow label="API provider" layout="inline">
            <Select ariaLabel="Knowledge cloud API provider"
              value={usableCloudProvider} onChange={setCloudProvider}
              options={cloudProfiles.map((provider) => ({
                value: provider.id, label: provider.label || provider.id,
              }))} />
          </FormRow>
          <TextField label="Cloud embedding model ID" type="text" value={cloudModel}
            placeholder="e.g. text-embedding-3-small" mono onValueChange={setCloudModel} />
          <Switch label="I consent to sending indexed content and search queries to the selected cloud API endpoint."
            checked={consent} onCheckedChange={setConsent} />
          <p className="settings-field-hint">
            With this opt-in, Graphlink sends full text chunks from indexed
            files and saved graphs (including chat messages, code, prompts, and
            notes), plus search queries, to your selected API endpoint.
            Embedding requests send text, not original file bytes or paths.
            Remote endpoints require HTTPS (HTTP is allowed only for local loopback).
            Consent is bound to this API endpoint; changing its URL requires
            a new opt-in. Turning this off stops future requests, but cannot
            recall text already sent to the provider.
          </p>
        </FieldGroup>
      )}

      <p className="settings-field-hint">
        This setting controls embedding requests only. When Knowledge is
        enabled in a chat using a cloud chat model, cited passages may
        separately be sent to that provider even when embeddings are Off.
      </p>
      <div className="settings-button-row">
        <Button variant="secondary"
          disabled={!canSubmit || state.knowledgeTestStatus === "running"}
          onClick={testEmbedding}
        >
          {state.knowledgeTestStatus === "running" ? "Testing…" : "Test"}
        </Button>
        <Button variant="primary"
          disabled={!canSubmit}
          onClick={() => transport.fireIntent("app-settings", "setKnowledgeEmbedding", args)}
        >
          Save choice
        </Button>
      </div>
      <p className="settings-field-hint">
        Test sends only a sample phrase, never a stored document.
        New or changed embedding sources build their own cache as needed.
      </p>
      {state.knowledgeTestStatus === "running" && (
        <p className="settings-update-status" role="status">
          Testing embedding connection… A cold local model may take several minutes to load.
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
