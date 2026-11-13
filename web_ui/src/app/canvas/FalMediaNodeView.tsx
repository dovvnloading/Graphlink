import type { Node, NodeProps } from "@xyflow/react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { withAuthToken } from "../../lib/auth/token";
import { NodeShell } from "./NodeShell";
import { FalMediaUploader } from "./FalMediaUploader";
import { useLodVisibility } from "./useLodVisibility";
import {
  MEDIA_OPERATIONS, comparableGroup, editJsonValue, formatMediaPrice,
  isMediaBusy, parseMediaCatalog, parseMediaSchema, parseMediaState,
  sortMediaModels, type MediaCatalog, type MediaMode, type MediaModel,
  type MediaSchema, type MediaState, type PriceSort,
} from "./falMediaStudioModel";
import "./FalMediaNodeView.css";

interface StudioDraft {
  mode: MediaMode;
  operation: string;
  modelId: string;
  modelName: string;
  prompt: string;
  argumentsJson: string;
}
type StudioInput = StudioDraft;
export interface FalMediaNodeData extends Record<string, unknown> {
  mediaJson: string;
  catalogJson: string;
  schemaJson: string;
  onSaveDraft: (draft: StudioInput) => void;
  onSearch: (mode: MediaMode, operation: string, query: string, cursor?: string) => void;
  onInspect: (modelId: string) => void;
  onFavorite: (modelId: string, enabled: boolean) => void;
  onPrepare: (draft: StudioInput) => void;
  onGenerate: (approvalId: string) => void;
  onRefresh: () => void;
  onCancel: () => void;
  onAcknowledge: () => void;
  onDelete: () => void;
}
export type FalMediaFlowNode = Node<FalMediaNodeData, "fal_media">;

function fieldsFromMedia(media: MediaState): StudioDraft {
  return {
    mode: media.mode, operation: media.operation, modelId: media.modelId,
    modelName: media.modelName, prompt: media.prompt,
    argumentsJson: media.argumentsJson,
  };
}
function normalizedJson(value: string): string {
  // Python stores compact JSON, but the editor shows pretty-printed JSON.
  // Comparing raw strings kept the draft permanently "dirty" and blocked
  // the confirmation button despite an unchanged, server-approved quote.
  try { return JSON.stringify(JSON.parse(value)); } catch { return value; }
}
function equalDraft(a: StudioDraft, b: StudioDraft): boolean {
  return a.mode === b.mode && a.operation === b.operation &&
    a.modelId === b.modelId && a.modelName === b.modelName &&
    a.prompt === b.prompt && normalizedJson(a.argumentsJson) === normalizedJson(b.argumentsJson);
}
function safeAssetUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && !!parsed.hostname &&
      !parsed.username && !parsed.password && (!parsed.port || parsed.port === "443");
  } catch { return false; }
}
function unitName(unit: string) {
  return unit.replace(/_/g, " ") || "billing unit";
}
function safeInlineMediaUrl(url: string): boolean {
  if (!safeAssetUrl(url)) return false;
  // Browser <video>/<audio> load without a click. Keep that automatic
  // traffic on Fal-owned media hosts only; non-Fal result links are still
  // available by explicit user action rather than silently tracking users.
  const host = new URL(url).hostname.toLowerCase();
  return host === "fal.media" || host.endsWith(".fal.media");
}
function iconFor(mode: MediaMode): string {
  return mode === "image" ? "▧" : mode === "video" ? "▣" : "♫";
}
function dateString(raw: string): string {
  const date = new Date(raw);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString();
}
function ModelCard({
  model, selected, favorite, onSelect, onFavorite, compact = false, disabled = false,
}: {
  model: MediaModel; selected: boolean; favorite: boolean;
  onSelect: () => void; onFavorite: () => void; compact?: boolean; disabled?: boolean;
}) {
  return (
    <div className={`fal-studio-model-card${selected ? " is-selected" : ""}${compact ? " is-compact" : ""}`}>
      <button type="button" className="fal-studio-model-pick" onClick={onSelect} disabled={disabled}
        aria-label={`Select ${model.name}`} aria-pressed={selected}>
        <span className="fal-studio-model-avatar" aria-hidden="true">
          {model.category.includes("video") ? "▣" : model.category.includes("audio") || model.category.includes("speech") ? "♫" : "◈"}
        </span>
        <span className="fal-studio-model-text">
          <strong title={model.id}>{model.name}</strong>
          <small title={model.id}>{model.id}</small>
          {!compact && !!model.description && <span className="fal-studio-model-desc">{model.description}</span>}
        </span>
        <span className="fal-studio-model-price" aria-label={
          model.price === null ? "Price unavailable" :
          `${formatMediaPrice(model.price, model.currency)} per ${unitName(model.unit)}`
        }>
          <strong>{formatMediaPrice(model.price, model.currency)}</strong>
          <small>{model.price === null ? "Unverified" : `/ ${unitName(model.unit)}`}</small>
        </span>
      </button>
      <button type="button" className={`fal-studio-star${favorite ? " is-favorite" : ""}`}
        onClick={onFavorite} aria-label={favorite ? `Remove ${model.name} from favorites` : `Favorite ${model.name}`}
        aria-pressed={favorite} title={favorite ? "Remove from favorites" : "Add to favorites"}>
        {favorite ? "★" : "☆"}
      </button>
    </div>
  );
}

function ModelLibrary({
  draft, catalog, isBusy, onSearch, onSelect, onFavorite,
}: {
  draft: StudioDraft; catalog: MediaCatalog; isBusy: boolean;
  onSearch: FalMediaNodeData["onSearch"];
  onSelect: (model: MediaModel) => void;
  onFavorite: FalMediaNodeData["onFavorite"];
}) {
  const [query, setQuery] = useState("");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [sortOrder, setSortOrder] = useState<PriceSort>("economy");
  const [showLibrary, setShowLibrary] = useState(true);
  const searchSignature = `${draft.mode}|${draft.operation}|${query}`;
  useEffect(() => {
    // Query is a deliberate network read; debounce each keystroke.
    const timer = window.setTimeout(() => {
      onSearch(draft.mode, draft.operation, query);
    }, query.trim() ? 450 : 180);
    return () => window.clearTimeout(timer);
  }, [draft.mode, draft.operation, query, searchSignature, onSearch]);
  const loaded = catalog.mode === draft.mode && catalog.operation === draft.operation &&
    catalog.query === query;
  const favorites = useMemo(() => new Set(catalog.favorites), [catalog.favorites]);
  const visible = useMemo(() => sortMediaModels(
    (loaded ? catalog.models : []).filter((model) => !favoritesOnly || favorites.has(model.id)),
    sortOrder,
  ), [catalog.models, loaded, favoritesOnly, favorites, sortOrder]);
  let lastGroup = "";
  return (
    <section className="fal-studio-library" aria-label="Fal model catalog">
      <div className="fal-studio-section-heading">
        <div>
          <span className="fal-studio-kicker">01 / MODEL LIBRARY</span>
          <h4>Find the right model <span>{loaded ? catalog.models.length : "—"}</span></h4>
        </div>
        <button type="button" className="fal-studio-icon-button" aria-label={showLibrary ? "Collapse catalog" : "Expand catalog"}
          onClick={() => setShowLibrary(!showLibrary)}>{showLibrary ? "⌃" : "⌄"}</button>
      </div>
      {showLibrary && <>
        <label className="fal-studio-search-field">
          <span aria-hidden="true">⌕</span>
          <input className="nodrag nopan" type="search" value={query}
            placeholder="Search models, creators, capabilities…"
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search Fal media models" />
          {(loaded && catalog.status === "loading") && <span className="fal-studio-loading-dot" role="status" aria-label="Searching"/>}
        </label>
        <div className="fal-studio-library-controls">
          <div className="fal-studio-choice" aria-label="Model filter">
            <button type="button" aria-pressed={!favoritesOnly} onClick={() => setFavoritesOnly(false)}>
              All models
            </button>
            <button type="button" aria-pressed={favoritesOnly} onClick={() => setFavoritesOnly(true)}>
              <span aria-hidden="true">★</span> Favorites
            </button>
          </div>
          <label className="fal-studio-sort">
            <span className="sr-only">Sort model catalog</span>
            <select value={sortOrder} onChange={(event) => setSortOrder(event.target.value as PriceSort)}>
              <option value="economy">Economy first</option>
              <option value="premium">Premium first</option>
              <option value="name">Name A–Z</option>
            </select>
          </label>
        </div>
        <div className="fal-studio-compare-disclosure">
          Prices are live unit rates, grouped by billing unit. Final cost varies by output.
        </div>
        <div className="fal-studio-model-list nowheel nodrag" role="region" aria-label="Available Fal models" tabIndex={0}>
          {!loaded && <p className="fal-studio-quiet">Loading models for this category…</p>}
          {loaded && catalog.status === "error" && (
            <div className="fal-studio-inline-alert" role="alert">
              <strong>Catalog unavailable</strong>
              <p>{catalog.error || "We could not reach the Fal catalog. Check your Fal API key in Settings."}</p>
              <button type="button" onClick={() => onSearch(draft.mode, draft.operation, query)}>Retry catalog</button>
            </div>
          )}
          {loaded && catalog.pricingWarning && (
            <p className="fal-studio-inline-warning">{catalog.pricingWarning}</p>
          )}
          {visible.map((model) => {
            const group = comparableGroup(model);
            const newGroup = sortOrder !== "name" && group !== lastGroup;
            lastGroup = group;
            return (
              <div key={model.id}>
                {newGroup && <div className="fal-studio-price-group">{group === "Unpriced" ? "Price unavailable" : `Billed in ${group}`}</div>}
                <ModelCard model={model} selected={draft.modelId === model.id}
                  favorite={favorites.has(model.id)} onSelect={() => onSelect(model)}
                  onFavorite={() => onFavorite(model.id, !favorites.has(model.id))}
                  compact={favoritesOnly} disabled={isBusy} />
              </div>
            );
          })}
          {loaded && catalog.status !== "loading" && visible.length === 0 && (
            <div className="fal-studio-empty-catalog">
              <span aria-hidden="true">{favoritesOnly ? "☆" : "⌕"}</span>
              <strong>{favoritesOnly ? "No saved models in these results" : "No matching models"}</strong>
              <p>{favoritesOnly ? "Use the star beside a model to save it. Favorites stay available across sessions." :
                "Try another search or generation category."}</p>
            </div>
          )}
          {loaded && catalog.hasMore && !favoritesOnly && (
            <button type="button" className="fal-studio-loadmore"
              disabled={catalog.status === "loading" || isBusy || !catalog.nextCursor}
              onClick={() => onSearch(draft.mode, draft.operation, query, catalog.nextCursor)}>
              {catalog.status === "loading" ? "Loading…" : "Show more models"}
            </button>
          )}
        </div>
        {!!catalog.favorites.length && favoritesOnly && visible.length === 0 && (
          <p className="fal-studio-quiet">You have {catalog.favorites.length} saved model{catalog.favorites.length === 1 ? "" : "s"} across your library. Switch categories to view other favorites.</p>
        )}
      </>}
    </section>
  );
}

function ModelInputs({
  draft, schema, busy, uploadDisabled, setPrompt, setJson, onInspect, onUploadBusy,
}: {
  draft: StudioDraft; schema: MediaSchema; busy: boolean; uploadDisabled: boolean;
  onUploadBusy: (value: boolean) => void;
  setPrompt: (text: string) => void; setJson: (text: string) => void;
  onInspect: () => void;
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  let parsedInputs: Record<string, unknown> = {};
  let jsonError = "";
  try {
    const parsed: unknown = JSON.parse(draft.argumentsJson || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      jsonError = "Model parameters must be a JSON object.";
    } else {
      parsedInputs = parsed as Record<string, unknown>;
    }
  } catch {
    jsonError = "Correct the JSON syntax before generating.";
  }
  const fields = schema.modelId === draft.modelId ? schema.fields.filter((field) => field.name !== "prompt") : [];
  const updateField = (name: string, value: unknown) => {
    const next = editJsonValue(draft.argumentsJson, name, value);
    if (next !== null) setJson(next);
  };
  return (
    <section className="fal-studio-inputs" aria-label="Generation inputs">
      <div className="fal-studio-section-heading">
        <div>
          <span className="fal-studio-kicker">02 / CREATE</span>
          <h4>Describe your creation</h4>
        </div>
        {draft.modelId && <button type="button" className="fal-studio-mini-link" onClick={onInspect}
          disabled={schema.status === "loading"}>↻ Model schema</button>}
      </div>
      <label className="fal-studio-field">
        <span>Prompt <small>Describe the outcome you want</small></span>
        <textarea className="fal-studio-prompt nodrag nopan nowheel"
          value={draft.prompt} disabled={busy} rows={3} maxLength={16000}
          placeholder={draft.mode === "image" ? "A moody editorial photograph, soft daylight, sculptural architecture…" :
            draft.mode === "video" ? "A slow cinematic dolly shot through a glowing glass forest…" :
            "Warm, expressive narration with natural pauses and a calm voice…"}
          onChange={(event) => setPrompt(event.target.value)}
          aria-label="Media generation prompt" />
        <small className="fal-studio-field-meta">{draft.prompt.length.toLocaleString()} / 16,000</small>
      </label>
      {draft.modelId && schema.status === "loading" && (
        <p className="fal-studio-quiet">Checking required inputs for this model…</p>
      )}
      {schema.warning && schema.modelId === draft.modelId && (
        <p className="fal-studio-inline-warning">{schema.warning}</p>
      )}
      {!!fields.length && (
        <div className="fal-studio-schema-grid">
          {fields.map((field) => {
            const val = parsedInputs[field.name] ?? field.default;
            const value = val === null || val === undefined ? "" : String(val);
            return (
              <label key={field.name} className="fal-studio-field">
                <span>{field.name.replace(/_/g, " ")} {field.required && <em>Required</em>}</span>
                {!!field.description && <small title={field.description}>{field.description}</small>}
                {!!field.enum?.length ? (
                  <select className="fal-studio-control nodrag nopan" disabled={busy}
                    value={value}
                    onChange={(event) => {
                      const option = field.enum.find((item) => String(item) === event.target.value);
                      updateField(field.name, option ?? event.target.value);
                    }}>
                    <option value="">Choose a value</option>
                    {field.enum.map((option) => <option key={String(option)} value={String(option)}>{String(option)}</option>)}
                  </select>
                ) : field.type === "boolean" ? (
                  <input type="checkbox" disabled={busy} checked={val === true}
                    onChange={(event) => updateField(field.name, event.target.checked)} />
                ) : (
                  <input className="fal-studio-control nodrag nopan" disabled={busy}
                    type={field.type === "number" || field.type === "integer" ? "number" : "text"}
                    step={field.type === "integer" ? 1 : "any"}
                    value={field.type === "json" && val != null ? JSON.stringify(val) : value}
                    placeholder={field.format === "uri" || field.name.endsWith("_url") ? "https://…" : field.required ? "Required" : "Optional"}
                    onChange={(event) => {
                      const next = event.target.value;
                      const number = Number(next);
                      updateField(field.name,
                        field.type === "number" || field.type === "integer" ? (next && Number.isFinite(number) ? number : next) :
                          field.type === "json" ? (() => { try { return JSON.parse(next); } catch { return next; } })() : next);
                    }} />
                )}
              </label>
            );
          })}
        </div>
      )}
      <FalMediaUploader mode={draft.mode} operation={draft.operation}
        fields={fields.filter((field) => field.name.endsWith("_url") || field.format === "uri").map((field) => field.name)}
        argumentsJson={draft.argumentsJson} disabled={uploadDisabled}
        onJsonChange={setJson} onBusyChange={onUploadBusy} />
      <details className="fal-studio-advanced" open={showAdvanced} onToggle={(event) => {
        setShowAdvanced((event.currentTarget as HTMLDetailsElement).open);
      }}>
        <summary>Advanced model JSON <small>Full API control</small></summary>
        <p>Set model-specific options such as image dimensions, duration, seed, or reference media URLs. JSON fields take precedence over the prompt box.</p>
        <textarea className="fal-studio-code nodrag nopan nowheel" aria-label="Advanced Fal model JSON"
          spellCheck={false} rows={5} disabled={busy} value={draft.argumentsJson}
          onChange={(event) => setJson(event.target.value)} />
        {jsonError && <p className="fal-studio-field-error" role="alert">{jsonError}</p>}
      </details>
    </section>
  );
}

function OutputGallery({ media, id }: { media: MediaState; id: string }) {
  const jobs = media.history || [];
  return (
    <section className="fal-studio-gallery" aria-label="Generated media library">
      <div className="fal-studio-section-heading">
        <div><span className="fal-studio-kicker">04 / OUTPUTS</span><h4>Your creations <span>{jobs.length}</span></h4></div>
      </div>
      {jobs.length === 0 ? (
        <div className="fal-studio-gallery-empty">
          <span aria-hidden="true">◈</span>
          <strong>Made here, saved here.</strong>
          <p>Your finished images, videos and audio will appear in this studio's history.</p>
        </div>
      ) : (
        <div className="fal-studio-gallery-list nowheel nodrag">
          {jobs.map((job, j) => (
            <div className="fal-studio-job" key={job.requestId || String(j)}>
              <div className="fal-studio-job-heading">
                <span>{iconFor(job.mode)} <strong>{job.modelName || job.modelId}</strong></span>
                <time>{dateString(job.createdAt)}</time>
              </div>
              {job.prompt && <p className="fal-studio-job-prompt" title={job.prompt}>{job.prompt}</p>}
              <div className="fal-studio-asset-grid">
                {job.outputs?.map((item, i) => {
                  if (!safeAssetUrl(item.url)) return null;
                  const proxyUrl = withAuthToken(`/api/fal-media/preview/${encodeURIComponent(id)}/${j}/${i}`);
                  return (
                    <div key={item.url} className="fal-studio-asset">
                      {item.kind === "image" && safeInlineMediaUrl(item.url) ? (
                        <img src={proxyUrl} alt={job.prompt || "Generated image"} loading="lazy"
                          referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.hidden = true; }} />
                      ) : item.kind === "video" && safeInlineMediaUrl(item.url) ? (
                        <video src={item.url} controls playsInline preload="metadata" className="nowheel" />
                      ) : item.kind === "audio" && safeInlineMediaUrl(item.url) ? (
                        <audio src={item.url} controls preload="none" className="nowheel" />
                      ) : (
                        <p className="fal-studio-quiet">Inline preview unavailable. Open the source link to view this asset.</p>
                      )}
                      <a href={item.url} target="_blank" rel="noreferrer"
                        aria-label={`Open generated ${item.kind} ${i+1} in a new tab`}>↗ Open {item.kind}</a>
                    </div>
                  );
                })}
                {(!job.outputs || job.outputs.length === 0) && <p className="fal-studio-quiet">The model returned no downloadable media URLs.</p>}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function FalMediaNodeViewImpl({ id, selected, data }: NodeProps<FalMediaFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const media = useMemo(() => parseMediaState(data.mediaJson), [data.mediaJson]);
  const catalog = useMemo(() => parseMediaCatalog(data.catalogJson), [data.catalogJson]);
  const schema = useMemo(() => parseMediaSchema(data.schemaJson), [data.schemaJson]);
  const [draft, setDraft] = useState<StudioDraft>(() => fieldsFromMedia(media));
  const [localError, setLocalError] = useState("");
  const [uploadBusy, setUploadBusy] = useState(false);
  const dirtyRef = useRef(false);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const serverDraft = fieldsFromMedia(media);
  const busy = isMediaBusy(media.status);

  useEffect(() => {
    if (equalDraft(serverDraft, draftRef.current)) dirtyRef.current = false;
    if (!dirtyRef.current) setDraft(serverDraft);
  // Object identity of serverDraft is rederived from media every render.
  // Only actual server JSON changes should trigger reconciliation.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.mediaJson]);

  const updateDraft = (patch: Partial<StudioDraft>) => {
    if (busy) return;
    dirtyRef.current = true;
    setDraft((prev) => ({ ...prev, ...patch }));
    setLocalError("");
  };
  useEffect(() => {
    if (!dirtyRef.current || busy) return;
    const timer = window.setTimeout(() => {
      // Autosave after editing settles. Generation submission ALSO sends
      // its complete draft; billing can never race an outdated autosave.
      data.onSaveDraft(draft);
    }, 800);
    return () => window.clearTimeout(timer);
  }, [data.onSaveDraft, draft, busy]);

  useEffect(() => {
    if (!["queued", "running", "cancelling", "stalled"].includes(media.status)) return;
    // Resume after reopening/restarting. Keep polling bounded even when a
    // remote job is slow; backend deduplicates concurrent polls by node ID.
    data.onRefresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") data.onRefresh();
    }, 6000);
    return () => window.clearInterval(timer);
  }, [data.onRefresh, media.status, media.requestId]);

  const quote = media.quote && media.quote.status === "ready" ? media.quote : null;
  const currentQuote = quote && !dirtyRef.current && quote.modelId === draft.modelId &&
    (quote.expiresAt ?? 0) > Date.now() / 1000 ? quote : null;

  const chooseMode = (mode: MediaMode) => {
    if (mode === draft.mode || busy || uploadBusy) return;
    updateDraft({ mode, operation: MEDIA_OPERATIONS[mode][0].id,
      modelId: "", modelName: "", argumentsJson: "{}" });
  };
  const chooseOperation = (operation: string) => {
    if (operation === draft.operation || busy || uploadBusy) return;
    updateDraft({ operation, modelId: "", modelName: "", argumentsJson: "{}" });
  };
  const chooseModel = (model: MediaModel) => {
    if (busy || uploadBusy) return;
    const nextDraft = { ...draftRef.current, modelId: model.id, modelName: model.name, argumentsJson: "{}" };
    dirtyRef.current = true;
    setDraft(nextDraft);
    data.onSaveDraft(nextDraft);
    data.onInspect(model.id);
    setLocalError("");
  };
  const prepare = () => {
    if (uploadBusy) { setLocalError("Finish the reference upload before generating."); return; }
    if (!draft.modelId) { setLocalError("Choose a model from the catalog first."); return; }
    try {
      const params: unknown = JSON.parse(draft.argumentsJson);
      if (!params || Array.isArray(params) || typeof params !== "object") throw Error("not an object");
      if (!draft.prompt.trim() && !Object.keys(params).length) {
        setLocalError("Add a prompt or model-specific parameters before generating.");
        return;
      }
    } catch {
      setLocalError("Fix the advanced JSON before reviewing the generation.");
      return;
    }
    data.onPrepare(draft);
    setLocalError("");
  };
  const confirmGenerate = () => {
    if (!currentQuote?.id || busy || uploadBusy) return;
    data.onGenerate(currentQuote.id);
  };
  const showQueue = ["submitting", "queued", "running", "cancelling", "stalled"].includes(media.status);
  const statusText = media.status === "submitting" ? "Submitting to Fal…" :
    media.status === "queued" ? "In the Fal queue" :
    media.status === "running" ? "Generating" :
    media.status === "cancelling" ? "Cancellation requested" :
    media.status === "stalled" ? "Connection interrupted" :
    media.status === "failed" ? "Generation failed" :
    media.status === "unconfirmed" ? "Check Fal dashboard" :
    media.status === "cancelled" ? "Job cancelled" :
    media.status === "completed" ? "Creation ready" : "Ready to create";

  return (
    <NodeShell kindClassName="fal-studio-node" selected={!!selected}
      collapsed={lodCollapsed} bodyClassName="fal-studio-body"
      header={<div className="fal-studio-header">
        <div className="fal-studio-header-icon" aria-hidden="true">◈</div>
        <div className="fal-studio-header-copy">
          <span className="fal-studio-eyebrow">FAL.AI · CREATIVE WORKSPACE</span>
          <strong>Media Studio</strong>
        </div>
        <div className="fal-studio-status" data-status={media.status}>
          <i aria-hidden="true" />{statusText}
        </div>
        <button type="button" className="fal-studio-icon-button fal-studio-delete"
          title="Remove studio node" aria-label="Delete Fal Media Studio node"
          onClick={() => {
            if (!showQueue || window.confirm("This job may continue on Fal. Remove the Studio node anyway?")) {
              data.onDelete();
            }
          }}>×</button>
      </div>}>
      <div className="fal-studio-app nodrag nopan">
        <div className="fal-studio-hero">
          <p>IDEA TO ASSET</p>
          <h3>Create without limits<span>.</span></h3>
          <span>One studio for image, motion, and sound.</span>
        </div>
        <div className="fal-studio-mode-tabs" role="group" aria-label="Media generation type">
          {(["image", "video", "audio"] as const).map((mode) => (
            <button key={mode} type="button" aria-pressed={draft.mode === mode}
              disabled={busy || uploadBusy} onClick={() => chooseMode(mode)}>
              <span aria-hidden="true">{iconFor(mode)}</span> {mode[0].toUpperCase()+mode.slice(1)}
            </button>
          ))}
        </div>
        <div className="fal-studio-operation-tabs" role="group" aria-label="Generation workflow">
          {MEDIA_OPERATIONS[draft.mode].map((op) => (
            <button key={op.id} type="button" aria-pressed={draft.operation === op.id}
              disabled={busy || uploadBusy} onClick={() => chooseOperation(op.id)}>{op.label}</button>
          ))}
        </div>
        <ModelLibrary draft={draft} catalog={catalog} isBusy={busy || uploadBusy}
          onSearch={data.onSearch} onSelect={chooseModel} onFavorite={data.onFavorite} />
        <div className="fal-studio-rule" />
        <ModelInputs draft={draft} schema={schema} busy={busy || uploadBusy}
          uploadDisabled={busy || !draft.modelId} onUploadBusy={setUploadBusy}
          setPrompt={(prompt) => updateDraft({ prompt })}
          setJson={(argumentsJson) => updateDraft({ argumentsJson })}
          onInspect={() => draft.modelId && data.onInspect(draft.modelId)} />
        <div className="fal-studio-rule" />
        <section className="fal-studio-generate" aria-label="Cost review and generation">
          <div className="fal-studio-section-heading">
            <div><span className="fal-studio-kicker">03 / REVIEW & GENERATE</span><h4>Make it real</h4></div>
          </div>
          {draft.modelId ? (
            <div className="fal-studio-selected-summary">
              <span>Selected model</span>
              <strong>{draft.modelName || draft.modelId}</strong>
              <small>{draft.modelId}</small>
            </div>
          ) : <p className="fal-studio-quiet">Choose a model to review its cost and generate.</p>}
          {!!(localError || (media.status === "unconfirmed" ? "" : media.error)) && (
            <div role="alert" className="fal-studio-inline-alert">
              <strong>{media.status === "failed" ? "The generation did not complete" : "Action needed"}</strong>
              <p>{localError || media.error}</p>
            </div>
          )}
          {media.status === "unconfirmed" ? (
            <div className="fal-studio-job-progress" role="alert">
              <strong>Submission outcome unknown</strong>
              <p>Fal may have accepted and charged your last request, but Graphlink did not receive a job ID.
                Do not submit it again until you have checked your Fal account.</p>
              <a href="https://fal.ai/dashboard" target="_blank" rel="noreferrer"
                className="fal-studio-dashboard-link">Open Fal dashboard ↗</a>
              <div className="fal-studio-action-row">
                <button type="button" className="fal-studio-button-outline"
                  onClick={() => {
                    if (window.confirm(
                      "Have you checked the Fal dashboard for this generation? Only unlock if you have verified the previous submission's outcome. This will not cancel a remote job."
                    )) data.onAcknowledge();
                  }}>I've checked — unlock the Studio</button>
              </div>
              <small>Recovery requires your confirmation. Graphlink will never automatically retry an uncertain paid request.</small>
            </div>
          ) : showQueue ? (
            <div className="fal-studio-job-progress" role="status" aria-live="polite">
              <div className="fal-studio-progress-dots"><i/><i/><i/></div>
              <strong>{statusText}</strong>
              <p>{media.queuePosition > 0 ? `Queue position ${media.queuePosition}` :
                media.status === "stalled" ? "You can retry checking the status without resubmitting." :
                "Your request is safe in the remote queue. You can keep working."}</p>
              <div className="fal-studio-action-row">
                <button className="fal-studio-button-secondary" type="button" onClick={data.onRefresh}>↻ Check status</button>
                {media.requestId && media.status !== "cancelling" && (
                  <button className="fal-studio-button-outline" type="button"
                    onClick={() => {
                      if (window.confirm("Ask Fal to cancel this request? Jobs already running may still incur charges.")) data.onCancel();
                    }}>Cancel request</button>
                )}
              </div>
              {media.requestId && <small>Job ID · {media.requestId}</small>}
            </div>
          ) : <>
            {quote && !currentQuote && <p className="fal-studio-inline-warning">Pricing review expired or inputs changed. Refresh your estimate to generate.</p>}
            {!!currentQuote && (
              <div className="fal-studio-quote">
                <div className="fal-studio-quote-head"><span>Cost review</span><strong>Before you generate</strong></div>
                <div className="fal-studio-quote-row"><span>Live model rate</span>
                  <strong>{formatMediaPrice(currentQuote.price, currentQuote.currency)}{currentQuote.price != null ? ` / ${unitName(currentQuote.unit || "")}` : ""}</strong>
                </div>
                <div className="fal-studio-quote-row"><span>Estimated request</span>
                  <strong>{formatMediaPrice(currentQuote.estimate, currentQuote.currency)}</strong>
                </div>
                <p>Estimates are informational, not spending limits. Actual billing depends on duration, size and output. Your approval authorizes one request.</p>
                {!!currentQuote.warning && <small className="fal-studio-inline-warning">{currentQuote.warning}</small>}
              </div>
            )}
            <div className="fal-studio-action-row">
              <button type="button" className="fal-studio-button-primary"
                disabled={!draft.modelId || uploadBusy || media.quote?.status === "loading"}
                onClick={currentQuote ? confirmGenerate : prepare}>
                {media.quote?.status === "loading" ? "Checking price…" :
                  currentQuote ? "Confirm & generate ↗" : "Review cost & generate ↗"}
              </button>
              {!!currentQuote && <button type="button" className="fal-studio-button-secondary"
                onClick={prepare}>Refresh quote</button>}
            </div>
            {currentQuote && <small className="fal-studio-consent">One confirmation · No automatic repeat charges</small>}
          </>}
        </section>
        <div className="fal-studio-rule" />
        <OutputGallery media={media} id={id} />
        <div className="fal-studio-footer"><span>Powered by fal.ai</span><span>Catalog prices refresh as models change · Media URLs may expire</span></div>
      </div>
    </NodeShell>
  );
}
function equalProps(prev: Readonly<NodeProps<FalMediaFlowNode>>, next: Readonly<NodeProps<FalMediaFlowNode>>) {
  return prev.id === next.id && prev.selected === next.selected &&
    prev.data.mediaJson === next.data.mediaJson &&
    prev.data.catalogJson === next.data.catalogJson &&
    prev.data.schemaJson === next.data.schemaJson &&
    prev.data.onSaveDraft === next.data.onSaveDraft &&
    prev.data.onSearch === next.data.onSearch &&
    prev.data.onInspect === next.data.onInspect &&
    prev.data.onFavorite === next.data.onFavorite &&
    prev.data.onPrepare === next.data.onPrepare &&
    prev.data.onGenerate === next.data.onGenerate &&
    prev.data.onRefresh === next.data.onRefresh &&
    prev.data.onCancel === next.data.onCancel &&
    prev.data.onAcknowledge === next.data.onAcknowledge &&
    prev.data.onDelete === next.data.onDelete;
}
export const FalMediaNodeView = memo(FalMediaNodeViewImpl, equalProps);
