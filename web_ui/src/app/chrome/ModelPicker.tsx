import { useEffect, useState, useSyncExternalStore, type KeyboardEvent } from "react";
import { Popover, useOverlays } from "../overlays/overlays";
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
  const [models, setModels] = useState<PickerModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    void store.loadPickerModels().then((rows) => {
      if (live) setModels(rows);
    }).catch(() => {
      if (live) {
        setModels([]);
        setError("Could not load available models. Check the Ollama server or cloud connection, then retry.");
      }
    }).finally(() => {
      if (live) setLoading(false);
    });
    return () => { live = false; };
  }, [store, retryCount]);

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
              title={!canChat(model) ? "Embedding-only model; cannot be used for chat" : undefined}
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
              {selected && <span aria-hidden="true">✓</span>}
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
