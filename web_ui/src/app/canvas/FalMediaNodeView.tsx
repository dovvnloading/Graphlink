import type { Node, NodeProps } from "@xyflow/react";
import { memo, useEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import { withAuthToken } from "../../lib/auth/token";
import { NodeShell } from "./NodeShell";
import { FalMediaUploader } from "./FalMediaUploader";
import { useLodVisibility } from "./useLodVisibility";
import {
  MEDIA_OPERATIONS, comparableGroup, editJsonValue, formatMediaPrice,
  isMediaBusy, parseMediaCatalog, parseMediaSchema, parseMediaState,
  sortMediaModels, type MediaCatalog, type MediaMode, type MediaModel,
  type MediaSchema, type MediaState, type MediaJob, type PriceSort,
} from "./falMediaStudioModel";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Disclosure } from "../ui/Disclosure";
import { EmptyState } from "../ui/EmptyState";
import { IconButton } from "../ui/IconButton";
import { InlineAlert } from "../ui/InlineAlert";
import { NumberField } from "../ui/NumberField";
import { Select } from "../ui/Select";
import { Spinner } from "../ui/Spinner";
import { TextArea } from "../ui/TextArea";
import { TextField } from "../ui/TextField";
import { ToggleButton } from "../ui/ToggleButton";
import { useConfirm } from "../ui/ConfirmDialog";
import { formatCount } from "../ui/format";
import { Icon } from "../ui/icons/Icon";
import type { IconName } from "../ui/icons/registry";
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
function iconFor(mode: MediaMode): IconName {
  return mode === "image" ? "image" : mode === "video" ? "video" : mode === "audio" ? "audio" : "cube";
}
function categoryIcon(category: string): IconName {
  return category.includes("video") ? "video" :
    category.includes("audio") || category.includes("speech") ? "audio" :
      category.includes("3d") || category.includes("mesh") ? "cube" : "image";
}
/** Adapters use label for a kit component's heading, never a native tooltip attribute. */
function StudioDisclosure({ label, ...rest }: Omit<ComponentProps<typeof Disclosure>, "title"> & { label: string }) {
  return <Disclosure {...rest} {...{ title: label }} />;
}
function StudioAlert({ label, ...rest }: Omit<ComponentProps<typeof InlineAlert>, "title"> & { label: string }) {
  return <InlineAlert {...rest} {...{ title: label }} />;
}
function StudioEmpty({ label, ...rest }: Omit<ComponentProps<typeof EmptyState>, "title"> & { label: string }) {
  return <EmptyState {...rest} {...{ title: label }} />;
}
function dateString(raw: string): string {
  const date = new Date(raw);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString();
}

function ModelCard({
  model, selected, favorite, onSelect, onFavorite, disabled = false,
}: {
  model: MediaModel; selected: boolean; favorite: boolean;
  onSelect: () => void; onFavorite: () => void; disabled?: boolean;
}) {
  return (
    <div className={"fal-studio-model-card" + (selected ? " is-selected" : "")}>
      <Button variant="ghost" size="sm" className="fal-studio-model-pick nodrag"
        aria-label={"Select " + model.name} aria-pressed={selected}
        disabled={disabled} onClick={onSelect}>
        <span className="fal-studio-model-avatar" aria-hidden="true">
          <Icon name={categoryIcon(model.category)} size="lg" />
        </span>
        <span className="fal-studio-model-text">
          <strong>{model.name}</strong>
          <small>{model.id}</small>
          {model.description && <span className="fal-studio-model-desc">{model.description}</span>}
        </span>
        <span className="fal-studio-model-price"
          aria-label={model.price === null ? "Price unavailable" :
            formatMediaPrice(model.price, model.currency) + " per " + unitName(model.unit)}>
          <strong>{formatMediaPrice(model.price, model.currency)}</strong>
          <small>{model.price === null ? "Unverified" : "/ " + unitName(model.unit)}</small>
        </span>
      </Button>
      <ToggleButton size="sm" className="fal-studio-star nodrag"
        icon={favorite ? "star-filled" : "star"} pressed={favorite}
        disabled={disabled} onPressedChange={onFavorite}
        aria-label={favorite ? "Remove " + model.name + " from favorites" : "Favorite " + model.name} />
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
  useEffect(() => {
    const timer = window.setTimeout(() => onSearch(draft.mode, draft.operation, query), query.trim() ? 450 : 180);
    return () => window.clearTimeout(timer);
  }, [draft.mode, draft.operation, query, onSearch]);
  const loaded = catalog.mode === draft.mode && catalog.operation === draft.operation && catalog.query === query;
  const favorites = useMemo(() => new Set(catalog.favorites), [catalog.favorites]);
  const visible = useMemo(() => sortMediaModels(
    (loaded ? catalog.models : []).filter((model) => !favoritesOnly || favorites.has(model.id)),
    sortOrder,
  ), [catalog.models, loaded, favoritesOnly, favorites, sortOrder]);
  const renderModels = (models: MediaModel[]) => models.map((model, index) => {
    const group = comparableGroup(model);
    const previous = index ? comparableGroup(models[index - 1]) : "";
    return (
      <div key={model.id}>
        {sortOrder !== "name" && group !== previous &&
          <div className="fal-studio-price-group">{group === "Unpriced" ? "Price unavailable" : "Billed in " + group}</div>}
        <ModelCard model={model} selected={draft.modelId === model.id}
          favorite={favorites.has(model.id)} disabled={isBusy}
          onSelect={() => onSelect(model)}
          onFavorite={() => onFavorite(model.id, !favorites.has(model.id))} />
      </div>
    );
  });
  return (
    <section className="fal-studio-library" aria-label="Fal model catalog">
      <div className="fal-studio-section-heading">
        <h4>Model catalog <Badge>{loaded ? formatCount(catalog.models.length) : "—"}</Badge></h4>
        <IconButton size="sm" icon={showLibrary ? "chevron-up" : "chevron-down"}
          label={showLibrary ? "Collapse catalog" : "Expand catalog"}
          onClick={() => setShowLibrary(!showLibrary)} />
      </div>
      {showLibrary && <>
        <div className="fal-studio-search nodrag nopan">
          <TextField label="Search Fal media models" type="search" size="sm"
            prefixIcon="search" value={query} onValueChange={setQuery}
            placeholder="Search models, creators, capabilities…" />
          {loaded && catalog.status === "loading" && <Spinner size={12} label="Searching" />}
        </div>
        <div className="fal-studio-library-controls">
          <div className="fal-studio-choice" role="group" aria-label="Model filter">
            <ToggleButton size="sm" label="All results" pressed={!favoritesOnly}
              onPressedChange={() => setFavoritesOnly(false)} />
            <ToggleButton size="sm" label="Favorites" icon={favoritesOnly ? "star-filled" : "star"}
              pressed={favoritesOnly} onPressedChange={() => setFavoritesOnly(true)} />
          </div>
          <Select ariaLabel="Sort model catalog" value={sortOrder}
            onChange={(next) => setSortOrder(next as PriceSort)}
            options={[{value: "economy", label: "Economy first"},
              {value: "premium", label: "Premium first"}, {value: "name", label: "Name A–Z"}]} />
        </div>
        <StudioDisclosure label="How pricing compares" className="fal-studio-pricing-info">
          Prices are live unit rates grouped by billing unit. Final cost varies by output.
        </StudioDisclosure>
        <div className="fal-studio-model-list" role="region" aria-label="Available Fal models">
          {!loaded && <p className="fal-studio-quiet">Loading models for this category…</p>}
          {loaded && catalog.status === "error" &&
            <StudioAlert variant="error" label="Catalog unavailable"
              body={catalog.error || "We could not reach the Fal catalog. Check your Fal API key in Settings."}
              action={{label: "Retry catalog", onClick: () => onSearch(draft.mode, draft.operation, query)}} />}
          {loaded && catalog.pricingWarning &&
            <StudioAlert variant="warning" label="Pricing information" body={catalog.pricingWarning} />}
          {renderModels(visible.slice(0, 4))}
          {loaded && catalog.status !== "loading" && visible.length === 0 &&
            <StudioEmpty icon={favoritesOnly ? "star" : "search"}
              label={favoritesOnly ? "No saved models in these results" : "No matching models"}
              body={favoritesOnly ? "Star a model to save it across sessions." :
                "Try another search or generation category."} />}
          {visible.length > 4 && <StudioDisclosure className="fal-studio-extra-models"
            label={"More models (" + formatCount(visible.length - 4) + ")"}>
            {renderModels(visible.slice(4))}
          </StudioDisclosure>}
          <div className="fal-studio-pagination">
            {loaded && draft.mode === "all" && !!catalog.currentCursor && !favoritesOnly &&
              <Button size="sm" variant="ghost" disabled={catalog.status === "loading"}
                onClick={() => onSearch(draft.mode, draft.operation, query)}>First page</Button>}
            {loaded && catalog.hasMore && !favoritesOnly &&
              <Button size="sm" variant="secondary"
                disabled={catalog.status === "loading" || isBusy || !catalog.nextCursor}
                onClick={() => onSearch(draft.mode, draft.operation, query, catalog.nextCursor)}>
                {catalog.status === "loading" ? "Loading…" : draft.mode === "all" ? "Next page" : "Show more models"}
              </Button>}
          </div>
        </div>
        {!!catalog.favorites.length && favoritesOnly && visible.length === 0 &&
          <p className="fal-studio-quiet">You have {formatCount(catalog.favorites.length)} saved models across your library. Switch categories to view other favorites.</p>}
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
  const primaryField = draft.mode === "all" ? null : draft.operation === "text-to-speech" ? "text" : "prompt";
  const fields = schema.modelId === draft.modelId
    ? schema.fields.filter((field) => field.name !== primaryField) : [];
  const updateField = (name: string, value: unknown) => {
    const next = editJsonValue(draft.argumentsJson, name, value);
    if (next !== null) setJson(next);
  };
  const renderField = (field: MediaSchema["fields"][number]) => {
    const val = parsedInputs[field.name] ?? field.default;
    const value = val === null || val === undefined ? "" : String(val);
    const label = field.name.replace(/_/g, " ");
    const description = field.description || (field.required ? "Required" : undefined);
    if (field.enum?.length) {
      return <Select key={field.name} label={label} className="fal-studio-schema-control"
        disabled={busy} value={value}
        options={[{value: "", label: "Choose a value"},
          ...field.enum.map((option) => ({value: String(option), label: String(option)}))]}
        onChange={(next) => {
          const selected = field.enum.find((option) => String(option) === next);
          updateField(field.name, selected ?? next);
        }} />;
    }
    if (field.type === "boolean") {
      return <Checkbox key={field.name} label={label} description={description}
        disabled={busy} checked={val === true}
        onChange={(event) => updateField(field.name, event.currentTarget.checked)} />;
    }
    if (field.type === "number" || field.type === "integer") {
      const numeric = val === null || val === undefined || val === "" ? "" : Number(val);
      return <NumberField key={field.name} label={label} description={description}
        disabled={busy} value={numeric !== "" && !Number.isFinite(numeric) ? "" : numeric}
        step={field.type === "integer" ? 1 : 0.01}
        onValueChange={(next) => updateField(field.name, next)} />;
    }
    return <TextField key={field.name} label={label} description={description}
      className="nodrag nopan" disabled={busy}
      type="text" value={field.type === "json" && val != null ? JSON.stringify(val) : value}
      placeholder={field.format === "uri" || field.name.endsWith("_url") ? "https://…" :
        field.required ? "Required" : "Optional"}
      onValueChange={(next) => updateField(field.name,
        field.type === "json" ? (() => { try { return JSON.parse(next); } catch { return next; } })() : next)} />;
  };
  return (
    <section className="fal-studio-inputs" aria-label="Generation inputs">
      <div className="fal-studio-section-heading">
        <h4>{draft.mode === "all" ? "Model inputs" :
          draft.operation === "text-to-speech" ? "Write what to say" : "Describe your creation"}</h4>
        {draft.modelId && <Button variant="ghost" size="sm" iconStart="refresh"
          onClick={onInspect} disabled={schema.status === "loading"}>Model schema</Button>}
      </div>
      {primaryField && <>
        <TextArea className="fal-studio-prompt nodrag nopan nowheel"
          label={primaryField === "text" ? "Speech text" : "Prompt"}
          description={primaryField === "text" ? "The exact words to speak" : "Describe the outcome you want"}
          aria-label={primaryField === "text" ? "Text to speak" : "Media generation prompt"}
          value={draft.prompt} onValueChange={setPrompt}
          disabled={busy} minRows={3} maxRows={5} maxLength={16000}
          placeholder={draft.mode === "image" ? "A moody editorial photograph, soft daylight, sculptural architecture…" :
            draft.mode === "video" ? "A slow cinematic dolly shot through a glowing glass forest…" :
              draft.operation === "text-to-speech" ? "Hello. Welcome to our studio. This is the text to narrate…" :
                "Warm, expressive music, ambience or a sound effect…"} />
        <small className="fal-studio-field-meta">{formatCount(draft.prompt.length)} / {formatCount(16000)}</small>
      </>}
      {draft.modelId && schema.status === "loading" &&
        <p className="fal-studio-quiet">Checking required inputs for this model…</p>}
      {schema.warning && schema.modelId === draft.modelId &&
        <StudioAlert variant="warning" label="Model inputs" body={schema.warning} />}
      {!!fields.length && <div className="fal-studio-schema-grid">
        {fields.slice(0, 4).map(renderField)}
      </div>}
      {fields.length > 4 &&
        <StudioDisclosure label={"More model inputs (" + formatCount(fields.length - 4) + ")"}>
          <div className="fal-studio-schema-grid">{fields.slice(4).map(renderField)}</div>
        </StudioDisclosure>}
      <StudioDisclosure label="Reference media">
        <FalMediaUploader key={draft.modelId} mode={draft.mode} operation={draft.operation}
          fields={fields.filter((field) => field.name.endsWith("_url") || field.format === "uri").map((field) => field.name)}
          argumentsJson={draft.argumentsJson} disabled={uploadDisabled}
          onJsonChange={setJson} onBusyChange={onUploadBusy} />
      </StudioDisclosure>
      <StudioDisclosure className="fal-studio-advanced" label="Advanced model JSON"
        open={showAdvanced} onOpenChange={setShowAdvanced}>
        <p>{draft.mode === "all" ? "Enter the input object from this model’s API schema." :
          "Set model-specific options such as dimensions, duration, seed, or reference URLs. JSON values take precedence over the prompt."}</p>
        <TextArea className="fal-studio-code nodrag nopan nowheel" label="Advanced Fal model JSON"
          aria-label="Advanced Fal model JSON" spellCheck={false} minRows={5} maxRows={9}
          disabled={busy} value={draft.argumentsJson} onValueChange={setJson} />
        {jsonError && <StudioAlert variant="error" label="Invalid model JSON" body={jsonError} />}
      </StudioDisclosure>
    </section>
  );
}

function OutputGallery({ media, id }: { media: MediaState; id: string }) {
  const jobs = media.history || [];
  const renderJob = (job: MediaJob, index: number) => (
    <div className="fal-studio-job" key={job.requestId || String(index)}>
      <div className="fal-studio-job-heading">
        <span><Icon name={iconFor(job.mode)} size="sm" /> <strong>{job.modelName || job.modelId}</strong></span>
        <time>{dateString(job.createdAt)}</time>
      </div>
      {job.prompt && <p className="fal-studio-job-prompt">{job.prompt}</p>}
      <div className="fal-studio-asset-grid">
        {job.outputs?.map((item, i) => {
          if (!safeAssetUrl(item.url)) return null;
          const proxyUrl = withAuthToken("/api/fal-media/preview/" + encodeURIComponent(id) + "/" + index + "/" + i);
          return <div key={item.url} className="fal-studio-asset">
            {item.kind === "image" && safeInlineMediaUrl(item.url) ? (
              <img src={proxyUrl} alt={job.prompt || "Generated image"} loading="lazy"
                referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.hidden = true; }} />
            ) : item.kind === "video" && safeInlineMediaUrl(item.url) ? (
              // Fal does not guarantee a caption track for generated video.
              // eslint-disable-next-line jsx-a11y/media-has-caption
              <video src={item.url} controls playsInline preload="metadata" className="nowheel"
                aria-label={job.prompt || "Generated video"} />
            ) : item.kind === "audio" && safeInlineMediaUrl(item.url) ? (
              // Audio generation has no guaranteed transcript.
              // eslint-disable-next-line jsx-a11y/media-has-caption
              <audio src={item.url} controls preload="none" className="nowheel"
                aria-label={job.prompt || "Generated audio"} />
            ) : <p className="fal-studio-quiet">Inline preview unavailable. Open the source link to view this asset.</p>}
            <a href={item.url} target="_blank" rel="noreferrer"
              aria-label={"Open generated " + item.kind + " " + (i + 1) + " in a new tab"}>
              <Icon name="external-link" size="xs" /> Open {item.kind}
            </a>
          </div>;
        })}
        {!job.resultPreview && (!job.outputs || job.outputs.length === 0) &&
          <p className="fal-studio-quiet">The model returned no downloadable media URLs.</p>}
      </div>
      {typeof job.resultPreview === "string" && job.resultPreview.length > 0 &&
        <StudioDisclosure className="fal-studio-result" label="Model response" defaultOpen={!job.outputs?.length}>
          <pre className="nowheel nodrag">{job.resultPreview}</pre>
        </StudioDisclosure>}
    </div>
  );
  return (
    <section className="fal-studio-gallery" aria-label="Generated media library">
      <div className="fal-studio-section-heading"><h4>Outputs <Badge>{formatCount(jobs.length)}</Badge></h4></div>
      {jobs.length === 0 ? (
        <StudioEmpty icon="image" label="Made here, saved here."
          body="Completed jobs appear here with their output files or model response." />
      ) : <>
        <div className="fal-studio-gallery-list">{jobs.slice(0, 2).map(renderJob)}</div>
        {jobs.length > 2 && <StudioDisclosure label={"Previous outputs (" + formatCount(jobs.length - 2) + ")"}>
          <div className="fal-studio-gallery-list">{jobs.slice(2).map((job, index) => renderJob(job, index + 2))}</div>
        </StudioDisclosure>}
      </>}
    </section>
  );
}

function FalMediaNodeViewImpl({ id, selected, data }: NodeProps<FalMediaFlowNode>) {
  const lodCollapsed = useLodVisibility();
  const requestApproval = useConfirm();
  const media = useMemo(() => parseMediaState(data.mediaJson), [data.mediaJson]);
  const catalog = useMemo(() => parseMediaCatalog(data.catalogJson), [data.catalogJson]);
  const schema = useMemo(() => parseMediaSchema(data.schemaJson), [data.schemaJson]);
  const [draft, setDraft] = useState<StudioDraft>(() => fieldsFromMedia(media));
  const [localError, setLocalError] = useState("");
  const [uploadBusy, setUploadBusy] = useState(false);
  const [clockSeconds, setClockSeconds] = useState(() => Math.floor(Date.now() / 1000));
  const dirtyRef = useRef(false);
  const draftRef = useRef(draft);
  useEffect(() => { draftRef.current = draft; }, [draft]);
  const serverDraft = fieldsFromMedia(media);
  const busy = isMediaBusy(media.status);
  const onSaveDraft = data.onSaveDraft;
  const onRefresh = data.onRefresh;

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
      onSaveDraft(draft);
    }, 800);
    return () => window.clearTimeout(timer);
  }, [onSaveDraft, draft, busy]);

  useEffect(() => {
    if (!["submitting", "queued", "running", "cancelling", "stalled"].includes(media.status)) return;
    // Resume after reopening/restarting. Keep polling bounded even when a
    // remote job is slow; backend deduplicates concurrent polls by node ID.
    onRefresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") onRefresh();
    }, 6000);
    return () => window.clearInterval(timer);
  }, [onRefresh, media.status, media.requestId]);

  useEffect(() => {
    if (media.quote?.status !== "ready") return;
    const timer = window.setInterval(() => setClockSeconds(Math.floor(Date.now() / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [media.quote?.id, media.quote?.status]);

  const quote = media.quote && media.quote.status === "ready" ? media.quote : null;
  const currentQuote = quote && equalDraft(draft, serverDraft) && quote.modelId === draft.modelId &&
    (quote.expiresAt ?? 0) > clockSeconds ? quote : null;

  const chooseMode = (mode: MediaMode) => {
    if (mode === draft.mode || busy || uploadBusy) return;
    updateDraft({ mode, operation: MEDIA_OPERATIONS[mode][0].id,
      modelId: "", modelName: "", argumentsJson: "{}", prompt: mode === "all" ? "" : draft.prompt });
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
      if (draft.mode !== "all" && !draft.prompt.trim() && !Object.keys(params).length) {
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
  const confirmGenerate = async () => {
    if (!currentQuote?.id || busy || uploadBusy) return;
    const approvedQuote = currentQuote;
    const accepted = await requestApproval({
      title: "Generate media?",
      body: "This request is billable on Fal.ai. Estimates are historical averages, not spending limits. Actual cost depends on the output. This approves one request.",
      confirmLabel: "Generate media",
      billing: { estimate: formatMediaPrice(approvedQuote.estimate, approvedQuote.currency) },
    });
    if (!accepted) return;
    if ((approvedQuote.expiresAt ?? 0) <= Math.floor(Date.now() / 1000)) {
      setLocalError("Cost review expired. Refresh the estimate before generating.");
      return;
    }
    data.onGenerate(approvedQuote.id);
  };
  const showQueue = ["submitting", "queued", "running", "cancelling", "stalled"].includes(media.status);
  const deleteNode = async () => {
    if (!showQueue || await requestApproval({
      title: "Delete Fal Media Studio node?",
      body: "This job may continue on Fal after the node is removed.",
      confirmLabel: "Delete node", destructive: true,
    })) data.onDelete();
  };
  const acknowledge = async () => {
    if (await requestApproval({
      title: "Unlock studio?",
      body: "Have you checked the Fal dashboard and verified the previous submission's outcome? This will not cancel a remote job or refund charges.",
      confirmLabel: "Unlock studio",
    })) data.onAcknowledge();
  };
  const cancelRequest = async () => {
    if (await requestApproval({
      title: "Cancel Fal request?",
      body: "Ask Fal to cancel this request? Jobs already running may still incur charges.",
      confirmLabel: "Cancel request", destructive: true,
    })) data.onCancel();
  };
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
        <h3>Fal Media Studio</h3>
        <Badge className="fal-studio-status">
          {showQueue ? <Spinner size={12} decorative /> :
            <Icon name={media.status === "completed" ? "status-success" :
              media.status === "failed" ? "status-error" :
                media.status === "cancelled" ? "status-cancelled" : "status-info"} size="sm" />}
          {statusText}
        </Badge>
        <IconButton size="sm" className="fal-studio-delete nodrag" icon="trash"
          label="Delete Fal Media Studio node" onClick={deleteNode} />
      </div>}>
      <div className="fal-studio-app nodrag nopan">
        <div className="fal-studio-mode-tabs" role="group" aria-label="Media generation type">
          {(["image", "video", "audio", "all"] as const).map((mode) =>
            <ToggleButton key={mode} size="sm" className="fal-studio-mode-button"
              pressed={draft.mode === mode} disabled={busy || uploadBusy}
              onPressedChange={() => chooseMode(mode)} icon={iconFor(mode)}
              label={mode === "all" ? "All models" : mode[0].toUpperCase() + mode.slice(1)} />)}
        </div>
        {draft.mode !== "all" && <div className="fal-studio-operation-tabs" role="group" aria-label="Generation workflow">
          {MEDIA_OPERATIONS[draft.mode].map((op) =>
            <ToggleButton key={op.id} size="sm" label={op.label}
              pressed={draft.operation === op.id} disabled={busy || uploadBusy}
              onPressedChange={() => chooseOperation(op.id)} />)}
        </div>}
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
          <div className="fal-studio-section-heading"><h4>Review and generate</h4></div>
          {draft.modelId ? <div className="fal-studio-selected-summary">
            <span>Selected model</span>
            <strong>{draft.modelName || draft.modelId}</strong>
            <small>{draft.modelId}</small>
          </div> : <p className="fal-studio-quiet">Choose a model to review its cost and generate.</p>}
          {!!(localError || (media.status === "unconfirmed" ? "" : media.error)) &&
            <StudioAlert variant="error" label={media.status === "failed" ? "The generation did not complete" : "Action needed"}
              body={localError || media.error} />}
          {media.status === "unconfirmed" ? <div className="fal-studio-job-progress" role="alert">
            <strong>Submission outcome unknown</strong>
            <p>Fal may have accepted and charged your last request, but Graphlink did not receive a job ID.
              Do not submit it again until you have checked your Fal account.</p>
            <a href="https://fal.ai/dashboard" target="_blank" rel="noreferrer" className="fal-studio-dashboard-link">
              <Icon name="external-link" size="sm" /> Open Fal dashboard
            </a>
            <div className="fal-studio-action-row">
              <Button size="sm" variant="secondary" onClick={acknowledge}>I've checked — unlock the Studio</Button>
            </div>
            <small>Recovery requires your confirmation. Graphlink will never automatically retry an uncertain paid request.</small>
          </div> : showQueue ? <div className="fal-studio-job-progress" role="status" aria-live="polite">
            <Spinner size={16} decorative />
            <strong>{statusText}</strong>
            <p>{media.queuePosition > 0 ? "Queue position " + formatCount(media.queuePosition) :
              media.status === "stalled" ? "You can retry checking the status without resubmitting." :
                "Your request is safe in the remote queue. You can keep working."}</p>
            <div className="fal-studio-action-row">
              <Button size="sm" variant="secondary" iconStart="refresh" onClick={data.onRefresh}>Check status</Button>
              {media.requestId && media.status !== "cancelling" &&
                <Button size="sm" variant="ghost" onClick={cancelRequest}>Cancel request</Button>}
            </div>
            {media.requestId && <small>Job ID · {media.requestId}</small>}
          </div> : <>
            {quote && !currentQuote &&
              <StudioAlert variant="warning" label="Pricing review expired"
                body="Inputs changed or the review expired. Refresh the estimate to generate." />}
            {!!currentQuote && <div className="fal-studio-quote">
              <div className="fal-studio-quote-head"><span>Cost review</span><strong>Before you generate</strong></div>
              <div className="fal-studio-quote-row"><span>Live model rate</span>
                <strong>{formatMediaPrice(currentQuote.price, currentQuote.currency)}
                  {currentQuote.price != null ? " / " + unitName(currentQuote.unit || "") : ""}</strong>
              </div>
              <div className="fal-studio-quote-row"><span>Historical one-call estimate</span>
                <strong>{formatMediaPrice(currentQuote.estimate, currentQuote.currency)}</strong>
              </div>
              <p>Estimates are historical averages, not parameter-specific quotations or spending limits.
                Actual billing depends on duration, size and output. Your approval authorizes one request.</p>
              {currentQuote.warning && <StudioAlert variant="warning" label="Estimate warning" body={currentQuote.warning} />}
            </div>}
            <div className="fal-studio-action-row">
              <Button size="sm" variant="primary"
                disabled={!draft.modelId || uploadBusy || media.quote?.status === "loading"}
                onClick={currentQuote ? confirmGenerate : prepare}>
                {media.quote?.status === "loading" ? "Checking price…" :
                  currentQuote ? "Confirm & generate" : "Review cost & generate"}
              </Button>
              {!!currentQuote && <Button size="sm" variant="secondary" onClick={prepare}>Refresh quote</Button>}
            </div>
            {currentQuote && <small className="fal-studio-consent">One confirmation · No automatic repeat charges</small>}
          </>}
        </section>
        <div className="fal-studio-rule" />
        <OutputGallery media={media} id={id} />
        <div className="fal-studio-footer">
          <span>Powered by Fal.ai</span>
          <span>Catalog prices refresh as models change · Media URLs may expire</span>
        </div>
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
