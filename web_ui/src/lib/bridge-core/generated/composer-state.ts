/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_composer_payload.py::ComposerStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface ComposerDraft {
  id: string;
  text: string;
  contextMode: string;
  sendMode: string;
  restored: boolean;
}

export interface ComposerContext {
  anchor?: ComposerContextAnchor | null;
  items: ComposerAttachment[];
  totalTokens: number;
  reviewAvailable: boolean;
}

export interface ComposerContextAnchor {
  id: string;
  label: string;
  type: string;
}

export interface ComposerAttachment {
  id: string;
  name: string;
  kind: string;
  tokenCount: number;
  preparationState: string;
  contextLabel: string;
}

export interface ComposerRoute {
  mode: "cloud" | "ollama" | "llamacpp" | "unknown";
  provider: string;
  modelId: string;
  modelLabel: string;
  modelOptions: ComposerModelOption[];
  reasoning: ComposerReasoning;
  label: string;
  available: boolean;
  canChange: boolean;
  modelValue?: string | null;
}

export interface ComposerModelOption {
  id: string;
  label: string;
  provider: string;
  source: string;
  active: boolean;
  ready: boolean;
  available: boolean;
  capabilities: string[];
}

export interface ComposerReasoning {
  level: string;
  label: string;
  options: ComposerReasoningOption[];
}

export interface ComposerReasoningOption {
  id: string;
  label: string;
  description: string;
}

export interface ComposerRequest {
  id?: string | null;
  state: "idle" | "preparing" | "uploading" | "waiting" | "generating" | "finalizing" | "canceled" | "failed" | "succeeded";
  message: string;
  canSend: boolean;
  canCancel: boolean;
  canRetry: boolean;
}

export interface ComposerCapabilities {
  attachments: boolean;
  contextReview: boolean;
  routeSelection: boolean;
  modelSelection: boolean;
  reasoningSelection: boolean;
  settingsShortcut: boolean;
  cancellation: boolean;
}

export interface ComposerTheme {
  mode: string;
  name: string;
  cssVariables: Record<string, string>;
  palette: ComposerThemePalette;
  semantic: ComposerThemeSemantic;
  neutralButton: ComposerThemeNeutralButton;
  graphNode: ComposerThemeGraphNode;
}

export interface ComposerThemePalette {
  userNode: string;
  aiNode: string;
  selection: string;
  navHighlight: string;
}

export interface ComposerThemeSemantic {
  searchHighlight: string;
  statusInfo: string;
  statusSuccess: string;
  statusError: string;
  statusWarning: string;
  artifact: string;
  conversationUserBubble: string;
  conversationAiBubble: string;
  default: string;
}

export interface ComposerThemeNeutralButton {
  background: string;
  hover: string;
  pressed: string;
  border: string;
  icon: string;
  mutedIcon: string;
}

export interface ComposerThemeGraphNode {
  border: string;
  header: string;
  dot: string;
  hoverDot: string;
  hoverOutline: string;
  selectedOutline: string;
  bodyStart: string;
  bodyEnd: string;
  headerStart: string;
  headerEnd: string;
  badgeFill: string;
  panelFill: string;
  panelBorder: string;
}

export interface ComposerState {
  schemaVersion: number;
  revision: number;
  draft: ComposerDraft;
  context: ComposerContext;
  route: ComposerRoute;
  request: ComposerRequest;
  capabilities: ComposerCapabilities;
  theme: ComposerTheme;
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

function checkComposerDraft(value: unknown, path: string, errors: string[]): void {
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
    else { if (typeof fieldValue !== "string") errors.push(`${path}.sendMode` + ": expected string"); }
  }
  {
    const fieldValue = value["restored"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.restored: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.restored` + ": expected boolean"); }
  }
}

function checkComposerContext(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["anchor"];
    if (fieldValue !== undefined && fieldValue !== null) { checkComposerContextAnchor(fieldValue, `${path}.anchor`, errors); }
  }
  {
    const fieldValue = value["items"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.items: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.items` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkComposerAttachment(item, `${path}.items` + `[${i}]`, errors); }); }
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

function checkComposerContextAnchor(value: unknown, path: string, errors: string[]): void {
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

function checkComposerAttachment(value: unknown, path: string, errors: string[]): void {
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

function checkComposerRoute(value: unknown, path: string, errors: string[]): void {
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
    else (fieldValue as unknown[]).forEach((item, i) => { checkComposerModelOption(item, `${path}.modelOptions` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["reasoning"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.reasoning: missing required field`);
    else { checkComposerReasoning(fieldValue, `${path}.reasoning`, errors); }
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

function checkComposerModelOption(value: unknown, path: string, errors: string[]): void {
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

function checkComposerReasoning(value: unknown, path: string, errors: string[]): void {
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
    else (fieldValue as unknown[]).forEach((item, i) => { checkComposerReasoningOption(item, `${path}.options` + `[${i}]`, errors); }); }
  }
}

function checkComposerReasoningOption(value: unknown, path: string, errors: string[]): void {
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

function checkComposerRequest(value: unknown, path: string, errors: string[]): void {
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

function checkComposerCapabilities(value: unknown, path: string, errors: string[]): void {
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

function checkComposerTheme(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["mode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.mode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.mode` + ": expected string"); }
  }
  {
    const fieldValue = value["name"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.name: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.name` + ": expected string"); }
  }
  {
    const fieldValue = value["cssVariables"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.cssVariables: missing required field`);
    else { if (!isRecord(fieldValue)) errors.push(`${path}.cssVariables` + ": expected object");
    else Object.entries(fieldValue as Record<string, unknown>).forEach(([k, v]) => { if (typeof v !== "string") errors.push(`${path}.cssVariables` + `.${k}` + ": expected string"); }); }
  }
  {
    const fieldValue = value["palette"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.palette: missing required field`);
    else { checkComposerThemePalette(fieldValue, `${path}.palette`, errors); }
  }
  {
    const fieldValue = value["semantic"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.semantic: missing required field`);
    else { checkComposerThemeSemantic(fieldValue, `${path}.semantic`, errors); }
  }
  {
    const fieldValue = value["neutralButton"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.neutralButton: missing required field`);
    else { checkComposerThemeNeutralButton(fieldValue, `${path}.neutralButton`, errors); }
  }
  {
    const fieldValue = value["graphNode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.graphNode: missing required field`);
    else { checkComposerThemeGraphNode(fieldValue, `${path}.graphNode`, errors); }
  }
}

function checkComposerThemePalette(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["userNode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.userNode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.userNode` + ": expected string"); }
  }
  {
    const fieldValue = value["aiNode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.aiNode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.aiNode` + ": expected string"); }
  }
  {
    const fieldValue = value["selection"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.selection: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.selection` + ": expected string"); }
  }
  {
    const fieldValue = value["navHighlight"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.navHighlight: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.navHighlight` + ": expected string"); }
  }
}

function checkComposerThemeSemantic(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["searchHighlight"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.searchHighlight: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.searchHighlight` + ": expected string"); }
  }
  {
    const fieldValue = value["statusInfo"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.statusInfo: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.statusInfo` + ": expected string"); }
  }
  {
    const fieldValue = value["statusSuccess"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.statusSuccess: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.statusSuccess` + ": expected string"); }
  }
  {
    const fieldValue = value["statusError"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.statusError: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.statusError` + ": expected string"); }
  }
  {
    const fieldValue = value["statusWarning"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.statusWarning: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.statusWarning` + ": expected string"); }
  }
  {
    const fieldValue = value["artifact"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.artifact: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.artifact` + ": expected string"); }
  }
  {
    const fieldValue = value["conversationUserBubble"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.conversationUserBubble: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.conversationUserBubble` + ": expected string"); }
  }
  {
    const fieldValue = value["conversationAiBubble"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.conversationAiBubble: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.conversationAiBubble` + ": expected string"); }
  }
  {
    const fieldValue = value["default"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.default: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.default` + ": expected string"); }
  }
}

function checkComposerThemeNeutralButton(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["background"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.background: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.background` + ": expected string"); }
  }
  {
    const fieldValue = value["hover"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.hover: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.hover` + ": expected string"); }
  }
  {
    const fieldValue = value["pressed"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.pressed: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.pressed` + ": expected string"); }
  }
  {
    const fieldValue = value["border"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.border: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.border` + ": expected string"); }
  }
  {
    const fieldValue = value["icon"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.icon: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.icon` + ": expected string"); }
  }
  {
    const fieldValue = value["mutedIcon"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.mutedIcon: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.mutedIcon` + ": expected string"); }
  }
}

function checkComposerThemeGraphNode(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["border"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.border: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.border` + ": expected string"); }
  }
  {
    const fieldValue = value["header"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.header: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.header` + ": expected string"); }
  }
  {
    const fieldValue = value["dot"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.dot: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.dot` + ": expected string"); }
  }
  {
    const fieldValue = value["hoverDot"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.hoverDot: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.hoverDot` + ": expected string"); }
  }
  {
    const fieldValue = value["hoverOutline"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.hoverOutline: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.hoverOutline` + ": expected string"); }
  }
  {
    const fieldValue = value["selectedOutline"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.selectedOutline: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.selectedOutline` + ": expected string"); }
  }
  {
    const fieldValue = value["bodyStart"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.bodyStart: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.bodyStart` + ": expected string"); }
  }
  {
    const fieldValue = value["bodyEnd"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.bodyEnd: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.bodyEnd` + ": expected string"); }
  }
  {
    const fieldValue = value["headerStart"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.headerStart: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.headerStart` + ": expected string"); }
  }
  {
    const fieldValue = value["headerEnd"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.headerEnd: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.headerEnd` + ": expected string"); }
  }
  {
    const fieldValue = value["badgeFill"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.badgeFill: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.badgeFill` + ": expected string"); }
  }
  {
    const fieldValue = value["panelFill"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.panelFill: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.panelFill` + ": expected string"); }
  }
  {
    const fieldValue = value["panelBorder"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.panelBorder: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.panelBorder` + ": expected string"); }
  }
}

function checkComposerState(value: unknown, path: string, errors: string[]): void {
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
    else { checkComposerDraft(fieldValue, `${path}.draft`, errors); }
  }
  {
    const fieldValue = value["context"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.context: missing required field`);
    else { checkComposerContext(fieldValue, `${path}.context`, errors); }
  }
  {
    const fieldValue = value["route"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.route: missing required field`);
    else { checkComposerRoute(fieldValue, `${path}.route`, errors); }
  }
  {
    const fieldValue = value["request"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.request: missing required field`);
    else { checkComposerRequest(fieldValue, `${path}.request`, errors); }
  }
  {
    const fieldValue = value["capabilities"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.capabilities: missing required field`);
    else { checkComposerCapabilities(fieldValue, `${path}.capabilities`, errors); }
  }
  {
    const fieldValue = value["theme"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.theme: missing required field`);
    else { checkComposerTheme(fieldValue, `${path}.theme`, errors); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateComposerState(value: unknown): ValidationResult<ComposerState> {
  const errors: string[] = [];
  checkComposerState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as ComposerState }
    : { ok: false, errors };
}
