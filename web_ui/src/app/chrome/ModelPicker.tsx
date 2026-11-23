import { useEffect, useRef, useState, useSyncExternalStore, type KeyboardEvent } from "react";
import { Popover, useOverlays } from "../overlays/overlays";
import { Icon } from "../ui/icons/Icon";
import { MenuButton, MenuGroup, MenuItem, MenuSeparator } from "../ui/Menu";
import type { ComposerStore, PickerModel } from "./composerStore";

const CAPABILITY_BADGES = ["tools", "vision", "embeddings", "thinking"] as const;

function formatSize(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(0)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KiB`;
  return `${bytes} B`;
}

function canChat(model: PickerModel): boolean {
  // Unknown capabilities are not proof of incompatibility (offline scans).
  return !(model.capabilities.includes("embeddings") && !model.capabilities.includes("chat"));
}

/** The same searchable, provider-qualified model picker in Composer and Agent. */
export function ModelPicker({ store, name = "model" }: { store: ComposerStore; name?: string }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const overlays = useOverlays();
  const [loadResult, setLoadResult] = useState<{
    store: ComposerStore;
    retryCount: number;
    models: PickerModel[];
    error: string;
  } | null>(null);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    let live = true;
    void store.loadPickerModels().then((rows) => {
      if (live) setLoadResult({ store, retryCount, models: rows, error: "" });
    }).catch(() => {
      if (live) {
        setLoadResult({
          store,
          retryCount,
          models: [],
          error: "Could not load available models. Check the Ollama server or cloud connection, then retry.",
        });
      }
    });
    return () => { live = false; };
  }, [store, retryCount]);

  // Derive pending/error from the current request, rather than resetting
  // state synchronously in the effect. Retry keeps search text and focus.
  const current = loadResult?.store === store && loadResult.retryCount === retryCount ? loadResult : null;
  const models = current?.models ?? [];
  const loading = current === null;
  const error = current?.error ?? "";

  const search = query.trim().toLocaleLowerCase();
  const matches = models.filter((model) =>
    [model.provider, model.modelId, model.label].some((text) => text.toLocaleLowerCase().includes(search)),
  );
  const selectable = matches.filter(canChat);
  const activeProvider = composer.route.mode === "ollama" ? "Ollama" : composer.route.provider;

  const choose = (model: PickerModel) => {
    if (!canChat(model)) return;
    store.selectPickerModel(model.provider, model.modelId);
    overlays.close();
  };

  const focusOption = (from: HTMLElement, action: "first" | "last" | "next" | "previous") => {
    const panel = from.closest('[role="dialog"]');
    const buttons = Array.from(panel?.querySelectorAll<HTMLButtonElement>(".model-picker-results button:not(:disabled)") ?? []);
    if (!buttons.length) return;
    const index = buttons.indexOf(from as HTMLButtonElement);
    const next = action === "first" ? 0 : action === "last" ? buttons.length - 1 :
      action === "next" ? (index + 1) % buttons.length :
        (index - 1 + buttons.length) % buttons.length;
    buttons[next].focus();
  };

  const onOptionKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const action = event.key === "ArrowDown" ? "next" :
      event.key === "ArrowUp" ? "previous" :
        event.key === "Home" ? "first" : event.key === "End" ? "last" : null;
    if (action) {
      event.preventDefault();
      event.stopPropagation();
      focusOption(event.currentTarget, action);
    }
  };

  return (
    <Popover anchored name={name} label="Choose a model" className="reasoning-popover model-picker-popover">
      <input
        ref={searchRef}
        type="search"
        className="model-picker-search"
        aria-label="Search models"
        placeholder="Search local and cloud models…"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            event.stopPropagation();
            focusOption(event.currentTarget, "first");
          } else if (event.key === "Enter" && !event.nativeEvent.isComposing && selectable.length === 1) {
            event.preventDefault();
            choose(selectable[0]);
          }
        }}
      />
      {loading && <p className="model-picker-empty" role="status">Loading local and cloud models…</p>}
      {!!error && (
        <>
          <p className="model-picker-empty" role="alert">{error}</p>
          <button type="button" className="model-picker-setup"
            onClick={() => {
              searchRef.current?.focus();
              setRetryCount((count) => count + 1);
            }}>Retry model list</button>
        </>
      )}
      <div className="model-picker-results" aria-label="Model choices">
        {matches.map((model) => {
          const selected = activeProvider === model.provider && model.modelId === composer.route.modelId;
          return (
            <button
              key={JSON.stringify([model.provider, model.modelId])}
              type="button"
              className={`reasoning-option model-picker-option${selected ? " active" : ""}`}
              aria-pressed={selected}
              disabled={!canChat(model)}
              aria-label={!canChat(model)
                ? `${model.label} · Embedding-only · unavailable for chat or agent tasks`
                : model.label}
              onClick={() => choose(model)}
              onKeyDown={onOptionKeyDown}
            >
              <span className="model-picker-choice">
                <span className="reasoning-option-label">{model.label}</span>
                <span className="model-picker-meta">
                  {model.source === "local" && <span className="model-picker-origin">Local</span>}
                  {CAPABILITY_BADGES.filter((capability) => model.capabilities.includes(capability)).map((capability) => (
                    <span key={capability} className="model-picker-badge">{capability}</span>
                  ))}
                  {model.sizeBytes !== null && (
                    <span className="model-picker-size">{formatSize(model.sizeBytes)}</span>
                  )}
                  {!canChat(model) && (
                    <span className="model-picker-badge">
                      Embedding-only · unavailable for chat or agent tasks
                    </span>
                  )}
                </span>
              </span>
              {selected && <Icon name="check" size="sm" />}
            </button>
          );
        })}
        {!loading && !error && matches.length === 0 && (
          <p className="model-picker-empty" role="status">
            {search ? "No matching models. Try another search." :
              "No models available. Start Ollama and download a chat model, or configure a cloud provider in Settings."}
          </p>
        )}
        {!loading && !error && matches.length > 0 && selectable.length === 0 && (
          <p className="model-picker-empty" role="status">
            None of these models supports chat. Download a chat-capable model to use the composer or agent.
          </p>
        )}
      </div>
      <button
        type="button"
        className="model-picker-setup"
        onClick={() => {
          store.openOllamaSettings();
          overlays.open("settings", "dialog");
        }}
      >
        Set up a local model
      </button>
    </Popover>
  );
}

// The composer uses the kit menu. The legacy anchored popover above remains for
// agent nodes, which share the same provider-qualified model selection intent.
export function ComposerModelMenu({ store, disabled = false }: { store: ComposerStore; disabled?: boolean }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const overlays = useOverlays();
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{
    store: ComposerStore; retry: number; models: PickerModel[]; error: string;
  } | null>(null);
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const [openEpoch, setOpenEpoch] = useState(0);
  useEffect(() => {
    let current = true;
    void store.loadPickerModels().then((models) => {
      if (current) setResult({ store, retry, models, error: "" });
    }, () => {
      if (current) setResult({
        store, retry, models: [],
        error: "Could not load available models. Check the connection and retry.",
      });
    });
    return () => { current = false; };
  }, [store, retry]);

  // Focus search after MenuSurface's layout effect initially focuses its first item.
  useEffect(() => { if (openEpoch > 0) input.current?.focus(); }, [openEpoch, result]);

  // Menu's capture-phase typeahead precedes input handlers. Search owns its
  // printable edits while focused; paste and screen-reader input still use
  // the ordinary onChange path below.
  useEffect(() => {
    const handleInput = (event: globalThis.KeyboardEvent) => {
      if (document.activeElement !== input.current || event.ctrlKey || event.altKey || event.metaKey) return;
      if (event.key.length === 1) {
        event.preventDefault();
        event.stopImmediatePropagation();
        setQuery((value) => value + event.key);
      } else if (event.key === "Backspace") {
        event.preventDefault();
        event.stopImmediatePropagation();
        setQuery((value) => value.slice(0, -1));
      }
    };
    document.addEventListener("keydown", handleInput, true);
    return () => document.removeEventListener("keydown", handleInput, true);
  }, []);

  const current = result?.store === store && result.retry === retry ? result : null;
  const models = current?.models ?? [];
  const normalized = query.trim().toLocaleLowerCase();
  const matches = models.filter((model) => [model.label, model.provider, model.modelId]
    .some((part) => part.toLocaleLowerCase().includes(normalized)));
  const providers = [...new Set(matches.map((model) => model.provider))];
  const modelLabel = composer.route.modelLabel || composer.route.modelId || "Select a model";
  const providerIcon = composer.route.mode === "ollama" ? "cpu" : "cloud";

  return (
    <span className="composer-model-menu">
      <Icon name={providerIcon} size="sm" />
      <MenuButton label={modelLabel} aria-label={`${composer.route.provider} model: ${modelLabel}`}
        className="composer-menu-button" data-overlay-trigger="model"
        disabled={disabled} menuLabel="Choose a model"
        onClick={() => { setQuery(""); setOpenEpoch((n) => n + 1); }}>
        {models.length > 8 && (
          <input ref={input} autoFocus type="search" className="model-picker-search"
            placeholder="Search local and cloud models…" aria-label="Search models"
            value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                e.currentTarget.closest('[role="menu"]')
                  ?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled),[role="menuitemradio"]:not(:disabled)')
                  ?.focus();
              }
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                const options = matches.filter(canChat);
                if (options.length === 1) {
                  store.selectPickerModel(options[0].provider, options[0].modelId);
                  // The open menu normally dismisses via MenuItem; when Enter
                  // selects from search, request a trigger click to close it.
                  (e.currentTarget.closest('[role="menu"]') as HTMLElement | null)?.dispatchEvent(
                    new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
                  );
                }
              }
            }} />
        )}
        {!current && <span className="model-picker-empty" role="status">Loading local and cloud models…</span>}
        {current?.error && (
          <>
            <span className="model-picker-empty" role="alert">{current.error}</span>
            <MenuItem label="Retry model list" icon="refresh" onSelect={() => setRetry((x) => x + 1)} />
          </>
        )}
        {!current?.error && providers.map((provider) => (
          <MenuGroup key={provider} label={provider}>
            {matches.filter((model) => model.provider === provider).map((model) => {
              const selected = (composer.route.mode === "ollama" ? "Ollama" : composer.route.provider)
                === model.provider && model.modelId === composer.route.modelId;
              const available = canChat(model);
              return (
                <MenuItem key={JSON.stringify([model.provider, model.modelId])}
                  label={model.label} icon={model.source === "local" ? "cpu" : "cloud"}
                  kind="radio" checked={selected} disabled={!available}
                  disabledReason={available ? undefined : "Embedding-only model; unavailable for chat"}
                  description={available
                    ? [model.source === "local" ? "Ready · Local" : "Ready · API",
                      ...CAPABILITY_BADGES.filter((cap) => model.capabilities.includes(cap)),
                      model.sizeBytes === null ? "" : formatSize(model.sizeBytes)].filter(Boolean).join(" · ")
                    : "Embedding-only · unavailable for chat or agent tasks"}
                  onSelect={() => store.selectPickerModel(model.provider, model.modelId)} />
              );
            })}
          </MenuGroup>
        ))}
        {current && !current.error && !matches.length && (
          <span className="model-picker-empty" role="status">
            {normalized ? "No matching models. Try another search." : "No models available. Configure a model in Settings."}
          </span>
        )}
        <MenuSeparator />
        <MenuItem label="Manage models…" icon="settings" onSelect={() => {
          store.openOllamaSettings();
          overlays.open("settings", "dialog");
        }} />
      </MenuButton>
    </span>
  );
}
