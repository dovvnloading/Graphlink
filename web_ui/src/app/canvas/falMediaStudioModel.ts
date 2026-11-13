/** Fal Media Studio view-model helpers.

Models are ranked by VERIFIED unit rates only within identical billing units:
a cheap $/megapixel model cannot be claimed to be cheaper per finished image
than a $/image model. Unpriced endpoints always sort last.
*/
export type MediaMode = "image" | "video" | "audio";
export const MEDIA_OPERATIONS: Record<MediaMode, { id: string; label: string }[]> = {
  image: [
    { id: "text-to-image", label: "Text to image" },
    { id: "image-to-image", label: "Image edit" },
  ],
  video: [
    { id: "text-to-video", label: "Text to video" },
    { id: "image-to-video", label: "Animate image" },
    { id: "video-to-video", label: "Video edit" },
  ],
  audio: [
    { id: "text-to-audio", label: "Music & sound" },
    { id: "text-to-speech", label: "Speech" },
    { id: "audio-to-audio", label: "Audio edit" },
  ],
};

export interface MediaAsset {
  url: string;
  kind: MediaMode;
}
export interface MediaJob {
  modelId: string;
  modelName: string;
  mode: MediaMode;
  prompt: string;
  requestId: string;
  createdAt: string;
  outputs: MediaAsset[];
}
export interface MediaQuote {
  status: "loading" | "ready";
  id?: string;
  modelId?: string;
  fingerprint?: string;
  expiresAt?: number;
  price?: number | null;
  unit?: string;
  currency?: string;
  estimate?: number | null;
  warning?: string;
}
export interface MediaState {
  mode: MediaMode;
  operation: string;
  modelId: string;
  modelName: string;
  prompt: string;
  argumentsJson: string;
  status: string;
  requestId: string;
  queuePosition: number;
  error: string;
  history: MediaJob[];
  quote: MediaQuote | null;
}
export interface MediaModel {
  id: string;
  name: string;
  category: string;
  description: string;
  thumbnailUrl: string;
  tags: string[];
  price: number | null;
  unit: string;
  currency: string;
}
export interface MediaCatalog {
  status: "idle" | "loading" | "ready" | "error";
  models: MediaModel[];
  favorites: string[];
  hasMore: boolean;
  nextCursor: string;
  mode: MediaMode;
  operation: string;
  query: string;
  error: string;
  pricingWarning: string;
}
export interface MediaSchemaField {
  name: string;
  type: "string" | "integer" | "number" | "boolean" | "json";
  description: string;
  required: boolean;
  format: string;
  default: unknown;
  enum: (string | number | boolean)[];
}
export interface MediaSchema {
  status: string;
  modelId: string;
  fields: MediaSchemaField[];
  warning: string;
}
const initialMedia: MediaState = {
  mode: "image", operation: "text-to-image", modelId: "", modelName: "",
  prompt: "", argumentsJson: "{}", status: "idle", requestId: "",
  queuePosition: 0, error: "", history: [], quote: null,
};
const initialCatalog: MediaCatalog = {
  status: "idle", models: [], favorites: [], hasMore: false,
  nextCursor: "", mode: "image", operation: "text-to-image", query: "",
  error: "", pricingWarning: "",
};
const initialSchema: MediaSchema = { status: "idle", modelId: "", fields: [], warning: "" };

function asObject(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}
function safeDecode(value: string): Record<string, unknown> | null {
  if (!value || value.length > 250_000) return null;
  try { return asObject(JSON.parse(value)); } catch { return null; }
}
function validMode(value: unknown): value is MediaMode {
  return value === "image" || value === "video" || value === "audio";
}
export function parseMediaState(raw: string): MediaState {
  const value = safeDecode(raw);
  if (!value) return initialMedia;
  const mode = validMode(value.mode) ? value.mode : "image";
  const operation = MEDIA_OPERATIONS[mode].some((item) => item.id === value.operation)
    ? String(value.operation) : MEDIA_OPERATIONS[mode][0].id;
  const history = Array.isArray(value.history) ? value.history.filter(
    (entry) => asObject(entry) !== null,
  ).slice(0, 12) as MediaJob[] : [];
  return {
    mode, operation,
    modelId: typeof value.modelId === "string" ? value.modelId : "",
    modelName: typeof value.modelName === "string" ? value.modelName : "",
    prompt: typeof value.prompt === "string" ? value.prompt : "",
    argumentsJson: typeof value.argumentsJson === "string" ? value.argumentsJson : "{}",
    status: typeof value.status === "string" ? value.status : "idle",
    requestId: typeof value.requestId === "string" ? value.requestId : "",
    queuePosition: typeof value.queuePosition === "number" ? value.queuePosition : 0,
    error: typeof value.error === "string" ? value.error : "",
    history,
    quote: asObject(value.quote) as MediaQuote | null,
  };
}
export function parseMediaCatalog(raw: string): MediaCatalog {
  const value = safeDecode(raw);
  if (!value) return initialCatalog;
  return {
    status: value.status === "loading" || value.status === "ready" || value.status === "error"
      ? value.status : "idle",
    models: (Array.isArray(value.models) ? value.models : []).filter(
      (v) => asObject(v) !== null && typeof v.id === "string",
    ).slice(0, 120) as MediaModel[],
    favorites: Array.isArray(value.favorites)
      ? value.favorites.filter((v): v is string => typeof v === "string").slice(0, 500) : [],
    hasMore: value.hasMore === true,
    nextCursor: typeof value.nextCursor === "string" ? value.nextCursor : "",
    mode: validMode(value.mode) ? value.mode : "image",
    operation: typeof value.operation === "string" ? value.operation : "text-to-image",
    query: typeof value.query === "string" ? value.query : "",
    error: typeof value.error === "string" ? value.error : "",
    pricingWarning: typeof value.pricingWarning === "string" ? value.pricingWarning : "",
  };
}
export function parseMediaSchema(raw: string): MediaSchema {
  const value = safeDecode(raw);
  if (!value) return initialSchema;
  return {
    status: typeof value.status === "string" ? value.status : "idle",
    modelId: typeof value.modelId === "string" ? value.modelId : "",
    fields: Array.isArray(value.fields)
      ? value.fields.filter((v) => asObject(v) !== null && typeof v.name === "string").slice(0, 45) as MediaSchemaField[]
      : [],
    warning: typeof value.warning === "string" ? value.warning : "",
  };
}
export function comparableGroup(model: MediaModel): string {
  return model.price === null ? "Unpriced" :
    `${model.currency || "USD"} / ${model.unit || "unit (unspecified)"}`;
}
export type PriceSort = "economy" | "premium" | "name";
export function sortMediaModels(models: MediaModel[], order: PriceSort): MediaModel[] {
  return [...models].sort((a, b) => {
    if (order === "name") return a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
    if ((a.price === null) !== (b.price === null)) return a.price === null ? 1 : -1;
    if (a.price === null && b.price === null) return a.name.localeCompare(b.name);
    const group = comparableGroup(a).localeCompare(comparableGroup(b));
    if (group) return group;
    const diff = (a.price ?? 0) - (b.price ?? 0);
    return (order === "economy" ? diff : -diff) || a.name.localeCompare(b.name);
  });
}
export function formatMediaPrice(value: number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0) return "Price unavailable";
  const text = new Intl.NumberFormat("en-US", { maximumFractionDigits: value < 0.01 ? 6 : 4,
    minimumFractionDigits: value > 0 && value < 0.0001 ? 5 : 0 }).format(value);
  return currency === "USD" || !currency ? `$${text}` : `${text} ${currency}`;
}
export function isMediaBusy(status: string): boolean {
  return ["submitting", "queued", "running", "cancelling", "stalled", "unconfirmed"].includes(status);
}
export function editJsonValue(raw: string, key: string, value: unknown): string | null {
  try {
    const obj = JSON.parse(raw);
    if (!asObject(obj)) return null;
    const updated = { ...obj, [key]: value };
    return JSON.stringify(updated, null, 2);
  } catch {
    return null;
  }
}
