/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_app_settings_payload.py::AppSettingsStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface ApiModelDescriptor {
  modelId: string;
  provider: string;
  capabilities: string[];
  ready: boolean;
  available: boolean;
}

export interface McpServerConfig {
  id: string;
  name: string;
  command: string;
  args: string[];
  scopes: string[];
  approval: string;
  enabledTools: string[];
  enabled: boolean;
  timeout: number;
  envKeys: string[];
}

export interface AppSettingsState {
  schemaVersion: number;
  revision: number;
  activeSection: string;
  showTokenCounter: boolean;
  enableSystemPrompt: boolean;
  notificationPreferences: Record<string, boolean>;
  githubTokenConfigured: boolean;
  secretsEncryptedAtRest: boolean;
  logLevel: string;
  autoModelPolicy: string;
  theme: string;
  hasCompletedOnboarding: boolean;
  providerMode: string;
  activeApiProvider: string;
  viewingApiProvider: string;
  apiBaseUrl: string;
  apiKeyConfigured: Record<string, boolean>;
  apiKeySource: Record<string, string>;
  apiModels: Record<string, string>;
  apiModelCatalog: ApiModelDescriptor[];
  apiCatalogStatus: string;
  apiCatalogMessage: string;
  geminiStaticModels: string[];
  geminiStaticImageModels: string[];
  ollamaReasoningLevel: string;
  ollamaCurrentModel: string;
  ollamaModelAssignments: Record<string, string>;
  ollamaScannedModels: string[];
  ollamaScanSummary: string;
  ollamaScanStatus: string;
  ollamaPullStatus: string;
  ollamaNotice: string;
  llamaCppReasoningLevel: string;
  llamaCppChatModelPath: string;
  llamaCppTitleModelPath: string;
  llamaCppChatFormat: string;
  llamaCppNCtx: number;
  llamaCppNGpuLayers: number;
  llamaCppNThreads: number;
  llamaCppScannedModels: string[];
  llamaCppScanSummary: string;
  llamaCppScanStatus: string;
  llamaCppNotice: string;
  mcpServers: McpServerConfig[];
  minCompatibleSchemaVersion?: number | null;
}

const API_MODEL_DESCRIPTOR_FIELDS: WireFields = [
  ["modelId", "s", 0],
  ["provider", "s", 0],
  ["capabilities", { a: "s" }, 0],
  ["ready", "b", 0],
  ["available", "b", 0],
];

const MCP_SERVER_CONFIG_FIELDS: WireFields = [
  ["id", "s", 0],
  ["name", "s", 0],
  ["command", "s", 0],
  ["args", { a: "s" }, 0],
  ["scopes", { a: "s" }, 0],
  ["approval", "s", 0],
  ["enabledTools", { a: "s" }, 0],
  ["enabled", "b", 0],
  ["timeout", "n", 0],
  ["envKeys", { a: "s" }, 0],
];

const APP_SETTINGS_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["activeSection", "s", 0],
  ["showTokenCounter", "b", 0],
  ["enableSystemPrompt", "b", 0],
  ["notificationPreferences", { d: "b" }, 0],
  ["githubTokenConfigured", "b", 0],
  ["secretsEncryptedAtRest", "b", 0],
  ["logLevel", "s", 0],
  ["autoModelPolicy", "s", 0],
  ["theme", "s", 0],
  ["hasCompletedOnboarding", "b", 0],
  ["providerMode", "s", 0],
  ["activeApiProvider", "s", 0],
  ["viewingApiProvider", "s", 0],
  ["apiBaseUrl", "s", 0],
  ["apiKeyConfigured", { d: "b" }, 0],
  ["apiKeySource", { d: "s" }, 0],
  ["apiModels", { d: "s" }, 0],
  ["apiModelCatalog", { a: { o: API_MODEL_DESCRIPTOR_FIELDS } }, 0],
  ["apiCatalogStatus", "s", 0],
  ["apiCatalogMessage", "s", 0],
  ["geminiStaticModels", { a: "s" }, 0],
  ["geminiStaticImageModels", { a: "s" }, 0],
  ["ollamaReasoningLevel", "s", 0],
  ["ollamaCurrentModel", "s", 0],
  ["ollamaModelAssignments", { d: "s" }, 0],
  ["ollamaScannedModels", { a: "s" }, 0],
  ["ollamaScanSummary", "s", 0],
  ["ollamaScanStatus", "s", 0],
  ["ollamaPullStatus", "s", 0],
  ["ollamaNotice", "s", 0],
  ["llamaCppReasoningLevel", "s", 0],
  ["llamaCppChatModelPath", "s", 0],
  ["llamaCppTitleModelPath", "s", 0],
  ["llamaCppChatFormat", "s", 0],
  ["llamaCppNCtx", "n", 0],
  ["llamaCppNGpuLayers", "n", 0],
  ["llamaCppNThreads", "n", 0],
  ["llamaCppScannedModels", { a: "s" }, 0],
  ["llamaCppScanSummary", "s", 0],
  ["llamaCppScanStatus", "s", 0],
  ["llamaCppNotice", "s", 0],
  ["mcpServers", { a: { o: MCP_SERVER_CONFIG_FIELDS } }, 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkAppSettingsState = compileFields(APP_SETTINGS_STATE_FIELDS);

export function validateAppSettingsState(value: unknown): ValidationResult<AppSettingsState> {
  const errors: string[] = [];
  checkAppSettingsState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as AppSettingsState }
    : { ok: false, errors };
}
