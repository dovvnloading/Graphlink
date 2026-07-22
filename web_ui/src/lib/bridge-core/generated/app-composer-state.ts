/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_app_composer_payload.py::AppComposerStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface AppComposerDraft {
  id: string;
  text: string;
  contextMode: string;
  sendMode: "enter_to_send" | "ctrl_enter_to_send";
  restored: boolean;
}

export interface AppComposerContext {
  anchor?: AppComposerContextAnchor | null;
  items: AppComposerAttachment[];
  totalTokens: number;
  reviewAvailable: boolean;
}

export interface AppComposerContextAnchor {
  id: string;
  label: string;
  type: string;
}

export interface AppComposerAttachment {
  id: string;
  name: string;
  kind: string;
  tokenCount: number;
  preparationState: string;
  contextLabel: string;
}

export interface AppComposerRoute {
  mode: "cloud" | "ollama" | "llamacpp" | "unknown";
  provider: string;
  modelId: string;
  modelLabel: string;
  modelOptions: AppComposerModelOption[];
  reasoning: AppComposerReasoning;
  label: string;
  available: boolean;
  canChange: boolean;
  modelValue?: string | null;
}

export interface AppComposerModelOption {
  id: string;
  label: string;
  provider: string;
  source: string;
  active: boolean;
  ready: boolean;
  available: boolean;
  capabilities: string[];
}

export interface AppComposerReasoning {
  level: string;
  label: string;
  options: AppComposerReasoningOption[];
}

export interface AppComposerReasoningOption {
  id: string;
  label: string;
  description: string;
}

export interface AppComposerRequest {
  id?: string | null;
  state: "idle" | "preparing" | "uploading" | "waiting" | "generating" | "finalizing" | "canceled" | "failed" | "succeeded";
  message: string;
  canSend: boolean;
  canCancel: boolean;
  canRetry: boolean;
}

export interface AppComposerCapabilities {
  attachments: boolean;
  contextReview: boolean;
  routeSelection: boolean;
  modelSelection: boolean;
  reasoningSelection: boolean;
  settingsShortcut: boolean;
  cancellation: boolean;
}

export interface AppComposerState {
  schemaVersion: number;
  revision: number;
  draft: AppComposerDraft;
  context: AppComposerContext;
  route: AppComposerRoute;
  request: AppComposerRequest;
  capabilities: AppComposerCapabilities;
  minCompatibleSchemaVersion?: number | null;
}

export type ValidationResult<T> =
  | { ok: true; value: T }
  | { ok: false; errors: string[] };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// Unknown keys are tolerated on purpose. The JSON Schema marks the contract
// additionalProperties:false because Python and the schema must not drift, but
// an incoming payload carrying a field this build has never heard of is the
// normal, expected shape of a NEWER compatible sender - rejecting it here would
// defeat the additive-forward-compatibility the version negotiation exists to
// provide. Missing or wrongly-typed KNOWN fields are still hard errors.

function checkAppComposerDraft(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["text"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.text: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.text` + ": expected string"); }
  }
  {
    const fieldValue = value["contextMode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.contextMode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.contextMode` + ": expected string"); }
  }
  {
    const fieldValue = value["sendMode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sendMode: missing required field`);
    else { if (!["enter_to_send", "ctrl_enter_to_send"].includes(fieldValue as string)) errors.push(`${path}.sendMode` + `: ${JSON.stringify(fieldValue)} is not one of [` + "enter_to_send, ctrl_enter_to_send" + `]`); }
  }
  {
    const fieldValue = value["restored"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.restored: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.restored` + ": expected boolean"); }
  }
}

function checkAppComposerContext(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["anchor"];
    if (fieldValue !== undefined && fieldValue !== null) { checkAppComposerContextAnchor(fieldValue, `${path}.anchor`, errors); }
  }
  {
    const fieldValue = value["items"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.items: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.items` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkAppComposerAttachment(item, `${path}.items` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["totalTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.totalTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.totalTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["reviewAvailable"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.reviewAvailable: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.reviewAvailable` + ": expected boolean"); }
  }
}

function checkAppComposerContextAnchor(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["label"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.label: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.label` + ": expected string"); }
  }
  {
    const fieldValue = value["type"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.type: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.type` + ": expected string"); }
  }
}

function checkAppComposerAttachment(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["name"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.name: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.name` + ": expected string"); }
  }
  {
    const fieldValue = value["kind"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.kind: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.kind` + ": expected string"); }
  }
  {
    const fieldValue = value["tokenCount"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.tokenCount: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.tokenCount` + ": expected number"); }
  }
  {
    const fieldValue = value["preparationState"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.preparationState: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.preparationState` + ": expected string"); }
  }
  {
    const fieldValue = value["contextLabel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.contextLabel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.contextLabel` + ": expected string"); }
  }
}

function checkAppComposerRoute(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["mode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.mode: missing required field`);
    else { if (!["cloud", "ollama", "llamacpp", "unknown"].includes(fieldValue as string)) errors.push(`${path}.mode` + `: ${JSON.stringify(fieldValue)} is not one of [` + "cloud, ollama, llamacpp, unknown" + `]`); }
  }
  {
    const fieldValue = value["provider"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.provider: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.provider` + ": expected string"); }
  }
  {
    const fieldValue = value["modelId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.modelId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.modelId` + ": expected string"); }
  }
  {
    const fieldValue = value["modelLabel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.modelLabel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.modelLabel` + ": expected string"); }
  }
  {
    const fieldValue = value["modelOptions"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.modelOptions: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.modelOptions` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkAppComposerModelOption(item, `${path}.modelOptions` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["reasoning"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.reasoning: missing required field`);
    else { checkAppComposerReasoning(fieldValue, `${path}.reasoning`, errors); }
  }
  {
    const fieldValue = value["label"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.label: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.label` + ": expected string"); }
  }
  {
    const fieldValue = value["available"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.available: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.available` + ": expected boolean"); }
  }
  {
    const fieldValue = value["canChange"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.canChange: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.canChange` + ": expected boolean"); }
  }
  {
    const fieldValue = value["modelValue"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.modelValue` + ": expected string"); }
  }
}

function checkAppComposerModelOption(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["label"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.label: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.label` + ": expected string"); }
  }
  {
    const fieldValue = value["provider"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.provider: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.provider` + ": expected string"); }
  }
  {
    const fieldValue = value["source"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.source: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.source` + ": expected string"); }
  }
  {
    const fieldValue = value["active"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.active: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.active` + ": expected boolean"); }
  }
  {
    const fieldValue = value["ready"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ready: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.ready` + ": expected boolean"); }
  }
  {
    const fieldValue = value["available"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.available: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.available` + ": expected boolean"); }
  }
  {
    const fieldValue = value["capabilities"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.capabilities: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.capabilities` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.capabilities` + `[${i}]` + ": expected string"); }); }
  }
}

function checkAppComposerReasoning(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["level"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.level: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.level` + ": expected string"); }
  }
  {
    const fieldValue = value["label"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.label: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.label` + ": expected string"); }
  }
  {
    const fieldValue = value["options"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.options: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.options` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkAppComposerReasoningOption(item, `${path}.options` + `[${i}]`, errors); }); }
  }
}

function checkAppComposerReasoningOption(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["label"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.label: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.label` + ": expected string"); }
  }
  {
    const fieldValue = value["description"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.description: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.description` + ": expected string"); }
  }
}

function checkAppComposerRequest(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["state"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.state: missing required field`);
    else { if (!["idle", "preparing", "uploading", "waiting", "generating", "finalizing", "canceled", "failed", "succeeded"].includes(fieldValue as string)) errors.push(`${path}.state` + `: ${JSON.stringify(fieldValue)} is not one of [` + "idle, preparing, uploading, waiting, generating, finalizing, canceled, failed, succeeded" + `]`); }
  }
  {
    const fieldValue = value["message"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.message: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.message` + ": expected string"); }
  }
  {
    const fieldValue = value["canSend"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.canSend: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.canSend` + ": expected boolean"); }
  }
  {
    const fieldValue = value["canCancel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.canCancel: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.canCancel` + ": expected boolean"); }
  }
  {
    const fieldValue = value["canRetry"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.canRetry: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.canRetry` + ": expected boolean"); }
  }
}

function checkAppComposerCapabilities(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["attachments"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.attachments: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.attachments` + ": expected boolean"); }
  }
  {
    const fieldValue = value["contextReview"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.contextReview: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.contextReview` + ": expected boolean"); }
  }
  {
    const fieldValue = value["routeSelection"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.routeSelection: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.routeSelection` + ": expected boolean"); }
  }
  {
    const fieldValue = value["modelSelection"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.modelSelection: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.modelSelection` + ": expected boolean"); }
  }
  {
    const fieldValue = value["reasoningSelection"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.reasoningSelection: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.reasoningSelection` + ": expected boolean"); }
  }
  {
    const fieldValue = value["settingsShortcut"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.settingsShortcut: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.settingsShortcut` + ": expected boolean"); }
  }
  {
    const fieldValue = value["cancellation"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.cancellation: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.cancellation` + ": expected boolean"); }
  }
}

function checkAppComposerState(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["schemaVersion"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.schemaVersion: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.schemaVersion` + ": expected number"); }
  }
  {
    const fieldValue = value["revision"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.revision: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.revision` + ": expected number"); }
  }
  {
    const fieldValue = value["draft"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.draft: missing required field`);
    else { checkAppComposerDraft(fieldValue, `${path}.draft`, errors); }
  }
  {
    const fieldValue = value["context"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.context: missing required field`);
    else { checkAppComposerContext(fieldValue, `${path}.context`, errors); }
  }
  {
    const fieldValue = value["route"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.route: missing required field`);
    else { checkAppComposerRoute(fieldValue, `${path}.route`, errors); }
  }
  {
    const fieldValue = value["request"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.request: missing required field`);
    else { checkAppComposerRequest(fieldValue, `${path}.request`, errors); }
  }
  {
    const fieldValue = value["capabilities"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.capabilities: missing required field`);
    else { checkAppComposerCapabilities(fieldValue, `${path}.capabilities`, errors); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateAppComposerState(value: unknown): ValidationResult<AppComposerState> {
  const errors: string[] = [];
  checkAppComposerState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as AppComposerState }
    : { ok: false, errors };
}
