import { useId, useState } from "react";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { Select } from "../ui/Select";
import { TextField } from "../ui/TextField";
import { Button } from "../ui/Button";
import { FieldGroup, FormRow } from "../ui/Field";
import { InlineAlert } from "../ui/InlineAlert";
import "./CloudProviderSettings.css";

type Provider = AppSettingsState["apiProviders"][number];
const textTasks = [
  { id: "task_title", label: "Chat naming" },
  { id: "task_chart", label: "Chart generation" },
  { id: "task_web_validate", label: "Web validation" },
  { id: "task_web_summarize", label: "Web summarization" },
];

function normalizedEndpoint(url: string): string {
  return url.trim().replace(/\/+$/, "");
}

function isLocalCustomEndpoint(provider: Provider, endpoint: string): boolean {
  if (provider.id !== "OpenAI-Compatible") return false;
  try {
    const url = new URL(endpoint);
    return ["http:", "https:"].includes(url.protocol) && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
  } catch {
    return false;
  }
}

function ProviderPicker({ state, viewed, onSelect }: {
  state: AppSettingsState;
  viewed: Provider;
  onSelect: (id: string) => void;
}) {
  return (
    <FieldGroup {...{ title: "API provider" }} description="Choose a provider or a custom endpoint.">
      <FormRow label="API provider" layout="inline">
        <Select ariaLabel="API provider" value={viewed.id} onChange={onSelect}
          options={state.apiProviders.map((provider) => ({
            value: provider.id, label: provider.label,
            description: provider.description,
            group: provider.group,
          }))} />
      </FormRow>
    </FieldGroup>
  );
}

function ProviderForm({ state, provider, transport }: {
  state: AppSettingsState;
  provider: Provider;
  transport: WsTransport;
}) {
  const [key, setKey] = useState("");
  const [endpoint, setEndpoint] = useState(state.apiBaseUrl);
  const [mainModel, setMainModel] = useState(state.apiModels.task_chat ?? "");
  const [overrides, setOverrides] = useState<Record<string, string>>(() => Object.fromEntries(
    textTasks.map(({ id }) => [id, state.apiModels[id] ?? ""]),
  ));
  const [imageModel, setImageModel] = useState(state.apiModels.task_image_gen ?? "");
  const [expanded, setExpanded] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [saving, setSaving] = useState(false);
  const textListId = useId();
  const imageListId = useId();
  const overridesId = useId();
  const source = state.apiKeySource[provider.keySlot];
  const existingKey = state.apiKeyConfigured[provider.keySlot] || source === "environment";
  const draftUrl = normalizedEndpoint(endpoint);
  const endpointChanged = provider.editableEndpoint && draftUrl !== normalizedEndpoint(state.apiBaseUrl);
  const optionalKey = isLocalCustomEndpoint(provider, draftUrl);
  const canConnect = (!provider.editableEndpoint || !!endpoint.trim()) && (
    !!key.trim() || optionalKey || (existingKey && !endpointChanged)
  );
  const bedrockRuntime = provider.id === "Amazon Bedrock" &&
    /^https:\/\/bedrock-runtime\.[a-z0-9-]+\.amazonaws\.com\/openai\/v1$/.test(draftUrl);
  const catalogLoading = state.apiCatalogStatus === "loading";
  // A list fetched for a draft deployment must stay bound to that endpoint.
  const catalogUrl = normalizedEndpoint(state.apiCatalogBaseUrl || state.apiBaseUrl);
  const showCatalog = !provider.editableEndpoint || draftUrl === catalogUrl;
  const catalog = showCatalog ? state.apiModelCatalog : [];
  const isGemini = provider.id === "Google Gemini";
  const textModels = [...new Set([
    ...catalog.filter((m) => !m.capabilities.length || m.capabilities.includes("text")).map((m) => m.modelId),
    ...(isGemini ? state.geminiStaticModels : []),
  ])];
  const imageModels = [...new Set([
    ...(isGemini ? state.geminiStaticImageModels : []),
    ...catalog.filter((m) => !m.capabilities.length || m.capabilities.includes("image")).map((m) => m.modelId),
  ])];
  const count = Object.values(overrides).filter((value) => value.trim()).length + Number(!!imageModel.trim());
  const save = () => {
    const models: Record<string, string> = { task_chat: mainModel.trim() };
    for (const { id } of textTasks) {
      const value = overrides[id]?.trim();
      if (value) models[id] = value;
    }
    if (provider.supportsImageGeneration && imageModel.trim()) models.task_image_gen = imageModel.trim();
    setSaving(true);
    transport.fireIntent("app-settings", "saveApiConfiguration",
      [provider.id, endpoint.trim(), key.trim(), models], undefined, false, undefined, () => setSaving(false));
    setKey("");
  };
  return (
    <>
      <FieldGroup {...{ title: "Connection" }}>
        <div className="cloud-section-heading">
          {provider.docsUrl && <a href={provider.docsUrl} target="_blank" rel="noreferrer">
            Setup guide

          </a>}
        </div>
        {provider.editableEndpoint ? (
          <TextField label="Base URL" type="url" spellCheck={false}
            autoComplete="off" mono description={provider.endpointHint}
            placeholder={provider.defaultBaseUrl || "https://…"} value={endpoint}
            onValueChange={setEndpoint} />
        ) : provider.defaultBaseUrl && (
          <div className="cloud-fixed-endpoint"><span>Endpoint</span><code>{provider.defaultBaseUrl}</code></div>
        )}
        <TextField label="API key" type="password" suffix={{ type: "password" }}
          autoComplete="new-password" spellCheck={false}
          placeholder={existingKey && !endpointChanged ? "Leave blank to keep the current key" : "Enter your API key…"}
          value={key} onValueChange={setKey} />
        {source === "environment" && !endpointChanged && <span className="cloud-credential-state">From environment</span>}
        {source === "stored" && !endpointChanged && <span className="cloud-credential-state">Saved key</span>}
        {optionalKey ? <small className="settings-field-hint">Optional for this local endpoint.</small> :
          endpointChanged && existingKey ?
          <small className="settings-field-hint" role="status">Enter a key for the new endpoint.</small> :
          provider.envVar && (!provider.editableEndpoint || draftUrl === normalizedEndpoint(provider.defaultBaseUrl)) && (
            <small className="settings-field-hint">Environment variable: <code>{provider.envVar}</code></small>
          )}
      </FieldGroup>

      <FieldGroup {...{ title: "Models" }}>
        <div className="cloud-section-heading">

          {provider.supportsCatalog && !bedrockRuntime && <Button variant="secondary" size="sm" className="cloud-small-button"
            disabled={!canConnect || catalogLoading} onClick={() => transport.fireIntent(
              "app-settings", "loadApiModels", [provider.id, key.trim(), endpoint.trim()],
            )}>{catalogLoading ? "Loading models…" : "Load available models"}</Button>}
        </div>
        {bedrockRuntime && <p className="cloud-catalog-status" role="status">Bedrock Runtime does not provide a models list. Enter the model or inference profile ID from AWS.</p>}
        {provider.supportsCatalog && !bedrockRuntime && state.apiCatalogStatus !== "idle" && (showCatalog || state.apiCatalogStatus !== "success") && (
          <p className="cloud-catalog-status" role="status" data-level={state.apiCatalogStatus}>{state.apiCatalogMessage}</p>
        )}
        <TextField label="Default text model" list={textListId} mono spellCheck={false}
          autoComplete="off" placeholder="Select or enter a model ID"
          description="Used for chat, titles, charts, and web tasks."
          value={mainModel} onValueChange={setMainModel} />
        <datalist id={textListId}>{textModels.map((id) => <option key={id} value={id} />)}</datalist>
      </FieldGroup>

      <div className="cloud-model-overrides">
        <Button variant="ghost" className="cloud-overrides-toggle" aria-expanded={expanded} aria-controls={overridesId}
          onClick={() => setExpanded(!expanded)}>
          <span>Per-task models</span>
          <small>{count ? `${count} assigned` : "Optional"}</small>
        </Button>
        <div id={overridesId} hidden={!expanded}>
          {expanded && <div className="cloud-task-grid">
            {textTasks.map(({ id, label }) => (
              <TextField key={id} label={label} type="text" mono list={textListId}
                autoComplete="off" value={overrides[id] ?? ""} placeholder="Use default text model"
                onValueChange={(value) => setOverrides({ ...overrides, [id]: value })} />
            ))}
            {provider.supportsImageGeneration && <>
              <TextField label="Image generation" list={imageListId} mono spellCheck={false}
                autoComplete="off" value={imageModel} placeholder="Enter an image model ID"
                onValueChange={setImageModel} />
              <datalist id={imageListId}>{imageModels.map((id) => <option key={id} value={id} />)}</datalist>
            </>}
          </div>}
        </div>
      </div>

      {confirmReset && <div role="alert">
        <InlineAlert variant="warning" {...{ title: "Reset API settings?" }}
          body="This clears all saved provider keys, endpoints, and model assignments. It cannot be undone." />
      </div>}
      <div className="settings-button-row cloud-form-actions">
        {confirmReset ? <>
          <Button variant="secondary" onClick={() => setConfirmReset(false)}>Cancel</Button>
          <Button variant="secondary" onClick={() => {
            transport.fireIntent("app-settings", "resetApiSettings", []);
            setKey(""); setEndpoint(provider.defaultBaseUrl); setMainModel(""); setOverrides({});
            setImageModel(""); setConfirmReset(false);
          }}>Confirm reset</Button>
        </> : <Button variant="ghost" size="sm" className="cloud-reset-button" onClick={() => setConfirmReset(true)}>
          Reset API settings
        </Button>}
        <Button variant="primary"
          disabled={!canConnect || !mainModel.trim() || saving || confirmReset} onClick={save}>
          {saving ? "Saving…" : "Save and activate"}
        </Button>
      </div>
    </>
  );
}

export function CloudProviderSettings({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  const viewed = state.apiProviders.find((provider) => provider.id === state.viewingApiProvider);
  const active = state.apiProviders.find((provider) => provider.id === state.activeApiProvider);
  const cloudActive = state.providerMode === "API Endpoint";
  return (
    <div className="settings-general-page cloud-settings">
      <h2 className="settings-page-title">API endpoint</h2>
      <p className="settings-page-description">Connect hosted models and configure per-task defaults.</p>
      <div className="cloud-route">
        <span>Active route</span>
        <strong>{cloudActive ? active?.label ?? state.activeApiProvider : state.providerMode}</strong>
        {!cloudActive && active && state.apiKeySource[active.keySlot] && state.apiKeySource[active.keySlot] !== "none" && (
          <Button variant="ghost" size="sm" className="cloud-use-saved" onClick={() =>
            transport.fireIntent("app-settings", "setProviderMode", ["API Endpoint"])}>Use saved {active.label}</Button>
        )}
      </div>
      {viewed && <ProviderPicker state={state} viewed={viewed} onSelect={(id) =>
        transport.fireIntent("app-settings", "setViewingApiProvider", [id], undefined, true)} />}
      {viewed ? <ProviderForm key={viewed.id} state={state} provider={viewed} transport={transport} /> :
        <p role="status" className="settings-update-status">Provider settings are unavailable.</p>}
    </div>
  );
}
