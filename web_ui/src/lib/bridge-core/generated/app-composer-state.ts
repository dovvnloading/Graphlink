/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_app_composer_payload.py::AppComposerStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface AppComposerDraft {
  id: string;
  text: string;
  contextMode: string;
  sendMode: "enter_to_send" | "ctrl_enter_to_send";
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
  byteSize: number;
  contextLabel: string;
  tokenCount: number;
}

export interface AppComposerRoute {
  mode: "ollama" | "api" | "llama_cpp";
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

const APP_COMPOSER_DRAFT_FIELDS: WireFields = [
  ["id", "s", 0],
  ["text", "s", 0],
  ["contextMode", "s", 0],
  ["sendMode", { e: ["enter_to_send", "ctrl_enter_to_send"] }, 0],
];

const APP_COMPOSER_CONTEXT_ANCHOR_FIELDS: WireFields = [
  ["id", "s", 0],
  ["label", "s", 0],
  ["type", "s", 0],
];

const APP_COMPOSER_ATTACHMENT_FIELDS: WireFields = [
  ["id", "s", 0],
  ["name", "s", 0],
  ["kind", "s", 0],
  ["byteSize", "n", 0],
  ["contextLabel", "s", 0],
  ["tokenCount", "n", 0],
];

const APP_COMPOSER_CONTEXT_FIELDS: WireFields = [
  ["anchor", { o: APP_COMPOSER_CONTEXT_ANCHOR_FIELDS }, 1],
  ["items", { a: { o: APP_COMPOSER_ATTACHMENT_FIELDS } }, 0],
  ["totalTokens", "n", 0],
  ["reviewAvailable", "b", 0],
];

const APP_COMPOSER_MODEL_OPTION_FIELDS: WireFields = [
  ["id", "s", 0],
  ["label", "s", 0],
];

const APP_COMPOSER_REASONING_OPTION_FIELDS: WireFields = [
  ["id", "s", 0],
  ["label", "s", 0],
  ["description", "s", 0],
];

const APP_COMPOSER_REASONING_FIELDS: WireFields = [
  ["level", "s", 0],
  ["label", "s", 0],
  ["options", { a: { o: APP_COMPOSER_REASONING_OPTION_FIELDS } }, 0],
];

const APP_COMPOSER_ROUTE_FIELDS: WireFields = [
  ["mode", { e: ["ollama", "api", "llama_cpp"] }, 0],
  ["provider", "s", 0],
  ["modelId", "s", 0],
  ["modelLabel", "s", 0],
  ["modelOptions", { a: { o: APP_COMPOSER_MODEL_OPTION_FIELDS } }, 0],
  ["reasoning", { o: APP_COMPOSER_REASONING_FIELDS }, 0],
  ["label", "s", 0],
  ["available", "b", 0],
  ["canChange", "b", 0],
  ["modelValue", "s", 1],
];

const APP_COMPOSER_REQUEST_FIELDS: WireFields = [
  ["id", "s", 1],
  ["state", { e: ["idle", "preparing", "uploading", "waiting", "generating", "finalizing", "canceled", "failed", "succeeded"] }, 0],
  ["message", "s", 0],
  ["canSend", "b", 0],
  ["canCancel", "b", 0],
  ["canRetry", "b", 0],
];

const APP_COMPOSER_CAPABILITIES_FIELDS: WireFields = [
  ["attachments", "b", 0],
  ["contextReview", "b", 0],
  ["routeSelection", "b", 0],
  ["modelSelection", "b", 0],
  ["reasoningSelection", "b", 0],
  ["settingsShortcut", "b", 0],
  ["cancellation", "b", 0],
];

const APP_COMPOSER_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["draft", { o: APP_COMPOSER_DRAFT_FIELDS }, 0],
  ["context", { o: APP_COMPOSER_CONTEXT_FIELDS }, 0],
  ["route", { o: APP_COMPOSER_ROUTE_FIELDS }, 0],
  ["request", { o: APP_COMPOSER_REQUEST_FIELDS }, 0],
  ["capabilities", { o: APP_COMPOSER_CAPABILITIES_FIELDS }, 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkAppComposerState = compileFields(APP_COMPOSER_STATE_FIELDS);

export function validateAppComposerState(value: unknown): ValidationResult<AppComposerState> {
  const errors: string[] = [];
  checkAppComposerState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as AppComposerState }
    : { ok: false, errors };
}
