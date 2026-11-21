/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_app_settings_payload.py::AppSettingsStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface ApiProvider {
  id: string;
  label: string;
  group: string;
  description: string;
  docsUrl: string;
  defaultBaseUrl: string;
  editableEndpoint: boolean;
  endpointHint: string;
  supportsCatalog: boolean;
  supportsImageGeneration: boolean;
  keySlot: string;
  envVar: string;
}

export interface ApiModelDescriptor {
  modelId: string;
  provider: string;
  capabilities: string[];
  ready: boolean;
  available: boolean;
}

export interface OllamaInventoryModel {
  name: string;
  sizeBytes?: number | null;
  family: string;
  parameterSize: string;
  quantization: string;
  capabilities: string[];
  trainedContextLength?: number | null;
  loaded?: boolean | null;
  memoryBytes?: number | null;
  vramBytes?: number | null;
  source: string;
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
  falApiKeySource: string;
  falJobsJson: string;
  falNotice: string;
  falBusy: boolean;
  falSyncResultJson: string;
  falDiscoveryJson: string;
  falUploadUrl: string;
  secretsEncryptedAtRest: boolean;
  logLevel: string;
  autoModelPolicy: string;
  theme: string;
  hasCompletedOnboarding: boolean;
  providerMode: string;
  activeApiProvider: string;
  viewingApiProvider: string;
  apiProviders: ApiProvider[];
  apiBaseUrl: string;
  apiKeyConfigured: Record<string, boolean>;
  apiKeySource: Record<string, string>;
  apiModels: Record<string, string>;
  apiModelCatalog: ApiModelDescriptor[];
  apiCatalogBaseUrl: string;
  apiCatalogStatus: string;
  apiCatalogMessage: string;
  geminiStaticModels: string[];
  geminiStaticImageModels: string[];
  ollamaReasoningLevel: string;
  ollamaCurrentModel: string;
  ollamaModelAssignments: Record<string, string>;
  ollamaContextWindows: Record<string, number>;
  ollamaContextModel: string;
  ollamaContextStatus: string;
  ollamaContextNotice: string;
  ollamaContextTrainedLength: number;
  ollamaContextModelSizeBytes: number;
  ollamaContextKvBytesPerToken: number;
  ollamaContextAvailableMemoryBytes: number;
  ollamaScannedModels: string[];
  ollamaInventory: OllamaInventoryModel[];
  ollamaInventorySource: string;
  ollamaServerReachable?: boolean | null;
  ollamaScanSummary: string;
  ollamaScanStatus: string;
  ollamaPullStatus: string;
  ollamaNotice: string;
  knowledgeEmbeddingSource: string;
  knowledgeEmbeddingModel: string;
  knowledgeCloudProvider: string;
  knowledgeCloudOptIn: boolean;
  knowledgeOllamaModels: string[];
  knowledgeOllamaCapableModels: string[];
  knowledgeScanStatus: string;
  knowledgeTestStatus: string;
  knowledgeNotice: string;
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
  harnessTrustedDirs: string[];
  mcpServers: McpServerConfig[];
  minCompatibleSchemaVersion?: number | null;
}

const API_PROVIDER_FIELDS: WireFields = [
  ["id", "s", 0],
  ["label", "s", 0],
  ["group", "s", 0],
  ["description", "s", 0],
  ["docsUrl", "s", 0],
  ["defaultBaseUrl", "s", 0],
  ["editableEndpoint", "b", 0],
  ["endpointHint", "s", 0],
  ["supportsCatalog", "b", 0],
  ["supportsImageGeneration", "b", 0],
  ["keySlot", "s", 0],
  ["envVar", "s", 0],
];

const API_MODEL_DESCRIPTOR_FIELDS: WireFields = [
  ["modelId", "s", 0],
  ["provider", "s", 0],
  ["capabilities", { a: "s" }, 0],
  ["ready", "b", 0],
  ["available", "b", 0],
];

const OLLAMA_INVENTORY_MODEL_FIELDS: WireFields = [
  ["name", "s", 0],
  ["sizeBytes", "n", 1],
  ["family", "s", 0],
  ["parameterSize", "s", 0],
  ["quantization", "s", 0],
  ["capabilities", { a: "s" }, 0],
  ["trainedContextLength", "n", 1],
  ["loaded", "b", 1],
  ["memoryBytes", "n", 1],
  ["vramBytes", "n", 1],
  ["source", "s", 0],
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
  ["falApiKeySource", "s", 0],
  ["falJobsJson", "s", 0],
  ["falNotice", "s", 0],
  ["falBusy", "b", 0],
  ["falSyncResultJson", "s", 0],
  ["falDiscoveryJson", "s", 0],
  ["falUploadUrl", "s", 0],
  ["secretsEncryptedAtRest", "b", 0],
  ["logLevel", "s", 0],
  ["autoModelPolicy", "s", 0],
  ["theme", "s", 0],
  ["hasCompletedOnboarding", "b", 0],
  ["providerMode", "s", 0],
  ["activeApiProvider", "s", 0],
  ["viewingApiProvider", "s", 0],
  ["apiProviders", { a: { o: API_PROVIDER_FIELDS } }, 0],
  ["apiBaseUrl", "s", 0],
  ["apiKeyConfigured", { d: "b" }, 0],
  ["apiKeySource", { d: "s" }, 0],
  ["apiModels", { d: "s" }, 0],
  ["apiModelCatalog", { a: { o: API_MODEL_DESCRIPTOR_FIELDS } }, 0],
  ["apiCatalogBaseUrl", "s", 0],
  ["apiCatalogStatus", "s", 0],
  ["apiCatalogMessage", "s", 0],
  ["geminiStaticModels", { a: "s" }, 0],
  ["geminiStaticImageModels", { a: "s" }, 0],
  ["ollamaReasoningLevel", "s", 0],
  ["ollamaCurrentModel", "s", 0],
  ["ollamaModelAssignments", { d: "s" }, 0],
  ["ollamaContextWindows", { d: "n" }, 0],
  ["ollamaContextModel", "s", 0],
  ["ollamaContextStatus", "s", 0],
  ["ollamaContextNotice", "s", 0],
  ["ollamaContextTrainedLength", "n", 0],
  ["ollamaContextModelSizeBytes", "n", 0],
  ["ollamaContextKvBytesPerToken", "n", 0],
  ["ollamaContextAvailableMemoryBytes", "n", 0],
  ["ollamaScannedModels", { a: "s" }, 0],
  ["ollamaInventory", { a: { o: OLLAMA_INVENTORY_MODEL_FIELDS } }, 0],
  ["ollamaInventorySource", "s", 0],
  ["ollamaServerReachable", "b", 1],
  ["ollamaScanSummary", "s", 0],
  ["ollamaScanStatus", "s", 0],
  ["ollamaPullStatus", "s", 0],
  ["ollamaNotice", "s", 0],
  ["knowledgeEmbeddingSource", "s", 0],
  ["knowledgeEmbeddingModel", "s", 0],
  ["knowledgeCloudProvider", "s", 0],
  ["knowledgeCloudOptIn", "b", 0],
  ["knowledgeOllamaModels", { a: "s" }, 0],
  ["knowledgeOllamaCapableModels", { a: "s" }, 0],
  ["knowledgeScanStatus", "s", 0],
  ["knowledgeTestStatus", "s", 0],
  ["knowledgeNotice", "s", 0],
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
  ["harnessTrustedDirs", { a: "s" }, 0],
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
