import { useEffect, useId, useRef, useState } from "react";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { AppBarIcon } from "./AppBarIcon";
import { SearchIcon } from "./SearchIcon";
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
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const listId = useId();
  const terms = query.trim().toLowerCase().split(/\s+/);
  const groups = new Map<string, Provider[]>();
  for (const provider of state.apiProviders) {
    const searchText = [provider.label, provider.id, provider.group, provider.description].join(" ").toLowerCase();
    if (!terms.every((term) => searchText.includes(term))) continue;
    const group = groups.get(provider.group) ?? [];
    group.push(provider);
    groups.set(provider.group, group);
  }
  const results = [...groups.values()].flat();
  const highlighted = results[Math.min(highlight, results.length - 1)];
  const optionId = (provider: Provider) => `${listId}-${provider.keySlot}`;
  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);
  useEffect(() => {
    if (open) list.current?.querySelector('[data-highlight="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [highlighted?.id, open]);

  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  const select = (provider: Provider) => {
    if (provider.id !== viewed.id) onSelect(provider.id);
    close();
  };

  return (
    <div className="cloud-provider-picker" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
    }}>
      <span className="settings-field-label">API Provider</span>
      <button ref={trigger} type="button" className="custom-select-trigger cloud-provider-trigger"
        aria-label={`Choose provider, ${viewed.label}`} aria-expanded={open}
        onClick={() => {
          setQuery("");
          setHighlight(0);
          setOpen(!open);
        }}>
        <span className="cloud-provider-choice">
          <span className="cloud-provider-group">{viewed.group}</span>
          <strong>{viewed.label}</strong>
          <span className="cloud-provider-description">{viewed.description}</span>
        </span>
        <AppBarIcon name="chevron" />
      </button>
      {open && (
        <div className="cloud-directory">
          <div className="cloud-provider-search">
            <SearchIcon />
            <input ref={input} type="text" role="combobox" aria-label="Search providers"
              aria-expanded aria-autocomplete="list" aria-controls={listId}
              aria-activedescendant={highlighted ? optionId(highlighted) : undefined}
              placeholder="Search providers or model families…" value={query}
              onChange={(event) => { setQuery(event.target.value); setHighlight(0); }}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  close();
                } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                  event.preventDefault();
                  const offset = event.key === "ArrowDown" ? 1 : -1;
                  setHighlight((index) => Math.max(0, Math.min(index + offset, results.length - 1)));
                } else if (event.key === "Enter" && highlighted && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  select(highlighted);
                }
              }} />
          </div>
          <div className="cloud-provider-results" ref={list} id={listId} role="listbox" aria-label="Providers">
            {[...groups].map(([group, providers]) => (
              <div key={group} role="group" aria-label={group}>
                <div className="cloud-provider-group" aria-hidden="true">{group}</div>
                {providers.map((provider) => {
                  const isActive = state.providerMode === "API Endpoint" && state.activeApiProvider === provider.id;
                  const hasKey = state.apiKeyConfigured[provider.keySlot] || state.apiKeySource[provider.keySlot] === "environment";
                  return (
                    <button key={provider.id} type="button" role="option" tabIndex={-1}
                      id={optionId(provider)} aria-selected={provider.id === viewed.id}
                      data-highlight={provider.id === highlighted?.id}
                      className="cloud-provider-option"
                      onMouseDown={(event) => event.preventDefault()} onClick={() => select(provider)}>
                      <span><strong>{provider.label}</strong><small>{provider.description}</small></span>
                      {isActive ? <span className="cloud-route-active">Active</span> :
                        hasKey ? <span className="cloud-credential-state">Key available</span> : null}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          {!results.length && <p role="status" className="cloud-empty">No providers match “{query}”. Try a lab or model family.</p>}
        </div>
      )}
    </div>
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
  const modelHintId = useId();
  const endpointHintId = useId();
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
      <section className="cloud-settings-section" aria-label="Connection">
        <div className="cloud-section-heading">
          <h3>Connection</h3>
          {provider.docsUrl && <a href={provider.docsUrl} target="_blank" rel="noreferrer">
            Setup guide
            <svg aria-hidden="true" viewBox="0 0 16 16" className="cloud-link-icon">
              <path d="M8.5 3.5h4v4m0-4-6 6M6 4H3.5v8.5H12V10" />
            </svg>
          </a>}
        </div>
        {provider.editableEndpoint ? (
          <label className="settings-field">
            <span className="settings-field-label">Base URL</span>
            <input aria-label="Base URL" aria-describedby={provider.endpointHint ? endpointHintId : undefined}
              className="settings-select cloud-id" type="url" spellCheck={false} autoComplete="off"
              placeholder={provider.defaultBaseUrl || "https://…"} value={endpoint}
              onChange={(event) => setEndpoint(event.target.value)} />
            {provider.endpointHint && <small id={endpointHintId} className="settings-field-hint">{provider.endpointHint}</small>}
          </label>
        ) : provider.defaultBaseUrl && (
          <div className="cloud-fixed-endpoint"><span>Endpoint</span><code>{provider.defaultBaseUrl}</code></div>
        )}
        <label className="settings-field">
          <span className="cloud-section-heading">
            <span className="settings-field-label">API Key</span>
            {source === "environment" && !endpointChanged && <span className="cloud-credential-state">From environment</span>}
            {source === "stored" && !endpointChanged && <span className="cloud-credential-state">Saved key</span>}
          </span>
          <input type="password" className="settings-select" autoComplete="new-password" spellCheck={false}
            aria-label="API Key" value={key}
            placeholder={existingKey && !endpointChanged ? "Leave blank to keep the current key" : "Enter your API key…"}
            onChange={(event) => setKey(event.target.value)} />
          {optionalKey ? <small className="settings-field-hint">Optional for this local endpoint.</small> :
            endpointChanged && existingKey ?
            <small className="settings-field-hint" role="status">Enter a key for the new endpoint.</small> :
            provider.envVar && (!provider.editableEndpoint || draftUrl === normalizedEndpoint(provider.defaultBaseUrl)) && (
              <small className="settings-field-hint">Environment variable: <code>{provider.envVar}</code></small>
            )}
        </label>
      </section>

      <section className="cloud-settings-section" aria-label="Models">
        <div className="cloud-section-heading">
          <h3>Models</h3>
          {provider.supportsCatalog && <button type="button" className="settings-button cloud-small-button"
            disabled={!canConnect || catalogLoading} onClick={() => transport.fireIntent(
              "app-settings", "loadApiModels", [provider.id, key.trim(), endpoint.trim()],
            )}>{catalogLoading ? "Loading models…" : "Load available models"}</button>}
        </div>
        {provider.supportsCatalog && state.apiCatalogStatus !== "idle" && (showCatalog || state.apiCatalogStatus !== "success") && (
          <p className="cloud-catalog-status" role="status" data-level={state.apiCatalogStatus}>{state.apiCatalogMessage}</p>
        )}
        <label className="settings-field">
          <span className="settings-field-label">Default text model</span>
          <input aria-label="Default text model" aria-describedby={modelHintId}
            className="settings-select cloud-id" list={textListId} spellCheck={false} autoComplete="off"
            value={mainModel} placeholder="Select or enter a model ID" onChange={(event) => setMainModel(event.target.value)} />
          <small id={modelHintId} className="settings-field-hint">Used for chat, titles, charts, and web tasks.</small>
        </label>
        <datalist id={textListId}>{textModels.map((id) => <option key={id} value={id} />)}</datalist>
      </section>

      <div className="cloud-model-overrides">
        <button type="button" className="cloud-overrides-toggle" aria-expanded={expanded} aria-controls={overridesId}
          onClick={() => setExpanded(!expanded)}>
          <AppBarIcon name="chevron" /><span>Per-task models</span>
          <small>{count ? `${count} assigned` : "Optional"}</small>
        </button>
        <div id={overridesId} hidden={!expanded}>
          {expanded && <div className="cloud-task-grid">
            {textTasks.map(({ id, label }) => (
              <label key={id} className="settings-field">
                <span className="settings-field-label">{label}</span>
                <input type="text" className="settings-select cloud-id" list={textListId} spellCheck={false}
                  autoComplete="off" value={overrides[id] ?? ""} placeholder="Use default text model"
                  onChange={(event) => setOverrides({ ...overrides, [id]: event.target.value })} />
              </label>
            ))}
            {provider.supportsImageGeneration && <label className="settings-field">
              <span className="settings-field-label">Image generation</span>
              <input className="settings-select cloud-id" list={imageListId} value={imageModel} spellCheck={false}
                autoComplete="off" placeholder="Enter an image model ID" onChange={(event) => setImageModel(event.target.value)} />
              <datalist id={imageListId}>{imageModels.map((id) => <option key={id} value={id} />)}</datalist>
            </label>}
          </div>}
        </div>
      </div>

      {confirmReset && <p className="cloud-catalog-status" data-level="error" role="alert">
        This clears all saved provider keys, endpoints, and model assignments. It cannot be undone.
      </p>}
      <div className="settings-button-row cloud-form-actions">
        {confirmReset ? <>
          <button type="button" className="settings-button" onClick={() => setConfirmReset(false)}>Cancel</button>
          <button type="button" className="settings-button" onClick={() => {
            transport.fireIntent("app-settings", "resetApiSettings", []);
            setKey(""); setEndpoint(provider.defaultBaseUrl); setMainModel(""); setOverrides({});
            setImageModel(""); setConfirmReset(false);
          }}>Confirm Reset</button>
        </> : <button type="button" className="cloud-reset-button" onClick={() => setConfirmReset(true)}>
          Reset API Settings
        </button>}
        <button type="button" className="settings-button settings-button-primary"
          disabled={!canConnect || !mainModel.trim() || saving || confirmReset} onClick={save}>
          {saving ? "Saving…" : "Save and activate"}
        </button>
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
      <div className="cloud-route">
        <span>Active route</span>
        <strong>{cloudActive ? active?.label ?? state.activeApiProvider : state.providerMode}</strong>
        {!cloudActive && active && state.apiKeySource[active.keySlot] && state.apiKeySource[active.keySlot] !== "none" && (
          <button type="button" className="cloud-use-saved" onClick={() =>
            transport.fireIntent("app-settings", "setProviderMode", ["API Endpoint"])}>Use saved {active.label}</button>
        )}
      </div>
      {viewed && <ProviderPicker state={state} viewed={viewed} onSelect={(id) =>
        transport.fireIntent("app-settings", "setViewingApiProvider", [id], undefined, true)} />}
      {viewed ? <ProviderForm key={viewed.id} state={state} provider={viewed} transport={transport} /> :
        <p role="status" className="settings-update-status">Provider settings are unavailable.</p>}
    </div>
  );
}
