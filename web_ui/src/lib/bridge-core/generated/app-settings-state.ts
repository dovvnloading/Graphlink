/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_app_settings_payload.py::AppSettingsStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface ApiModelDescriptor {
  modelId: string;
  provider: string;
  capabilities: string[];
  ready: boolean;
  available: boolean;
}

export interface AppSettingsState {
  schemaVersion: number;
  revision: number;
  activeSection: string;
  showTokenCounter: boolean;
  enableSystemPrompt: boolean;
  notificationPreferences: Record<string, boolean>;
  githubTokenConfigured: boolean;
  activeApiProvider: string;
  viewingApiProvider: string;
  apiBaseUrl: string;
  apiKeyConfigured: Record<string, boolean>;
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

function checkApiModelDescriptor(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["modelId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.modelId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.modelId` + ": expected string"); }
  }
  {
    const fieldValue = value["provider"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.provider: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.provider` + ": expected string"); }
  }
  {
    const fieldValue = value["capabilities"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.capabilities: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.capabilities` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.capabilities` + `[${i}]` + ": expected string"); }); }
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
}

function checkAppSettingsState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["activeSection"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.activeSection: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.activeSection` + ": expected string"); }
  }
  {
    const fieldValue = value["showTokenCounter"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.showTokenCounter: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.showTokenCounter` + ": expected boolean"); }
  }
  {
    const fieldValue = value["enableSystemPrompt"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.enableSystemPrompt: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.enableSystemPrompt` + ": expected boolean"); }
  }
  {
    const fieldValue = value["notificationPreferences"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.notificationPreferences: missing required field`);
    else { if (!isRecord(fieldValue)) errors.push(`${path}.notificationPreferences` + ": expected object");
    else Object.entries(fieldValue as Record<string, unknown>).forEach(([k, v]) => { if (typeof v !== "boolean") errors.push(`${path}.notificationPreferences` + `[${JSON.stringify(k)}]` + ": expected boolean"); }); }
  }
  {
    const fieldValue = value["githubTokenConfigured"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.githubTokenConfigured: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.githubTokenConfigured` + ": expected boolean"); }
  }
  {
    const fieldValue = value["activeApiProvider"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.activeApiProvider: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.activeApiProvider` + ": expected string"); }
  }
  {
    const fieldValue = value["viewingApiProvider"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.viewingApiProvider: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.viewingApiProvider` + ": expected string"); }
  }
  {
    const fieldValue = value["apiBaseUrl"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiBaseUrl: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.apiBaseUrl` + ": expected string"); }
  }
  {
    const fieldValue = value["apiKeyConfigured"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiKeyConfigured: missing required field`);
    else { if (!isRecord(fieldValue)) errors.push(`${path}.apiKeyConfigured` + ": expected object");
    else Object.entries(fieldValue as Record<string, unknown>).forEach(([k, v]) => { if (typeof v !== "boolean") errors.push(`${path}.apiKeyConfigured` + `[${JSON.stringify(k)}]` + ": expected boolean"); }); }
  }
  {
    const fieldValue = value["apiModels"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiModels: missing required field`);
    else { if (!isRecord(fieldValue)) errors.push(`${path}.apiModels` + ": expected object");
    else Object.entries(fieldValue as Record<string, unknown>).forEach(([k, v]) => { if (typeof v !== "string") errors.push(`${path}.apiModels` + `[${JSON.stringify(k)}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["apiModelCatalog"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiModelCatalog: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.apiModelCatalog` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkApiModelDescriptor(item, `${path}.apiModelCatalog` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["apiCatalogStatus"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiCatalogStatus: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.apiCatalogStatus` + ": expected string"); }
  }
  {
    const fieldValue = value["apiCatalogMessage"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiCatalogMessage: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.apiCatalogMessage` + ": expected string"); }
  }
  {
    const fieldValue = value["geminiStaticModels"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.geminiStaticModels: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.geminiStaticModels` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.geminiStaticModels` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["geminiStaticImageModels"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.geminiStaticImageModels: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.geminiStaticImageModels` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.geminiStaticImageModels` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["ollamaReasoningLevel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ollamaReasoningLevel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.ollamaReasoningLevel` + ": expected string"); }
  }
  {
    const fieldValue = value["ollamaCurrentModel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ollamaCurrentModel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.ollamaCurrentModel` + ": expected string"); }
  }
  {
    const fieldValue = value["ollamaModelAssignments"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ollamaModelAssignments: missing required field`);
    else { if (!isRecord(fieldValue)) errors.push(`${path}.ollamaModelAssignments` + ": expected object");
    else Object.entries(fieldValue as Record<string, unknown>).forEach(([k, v]) => { if (typeof v !== "string") errors.push(`${path}.ollamaModelAssignments` + `[${JSON.stringify(k)}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["ollamaScannedModels"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ollamaScannedModels: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.ollamaScannedModels` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.ollamaScannedModels` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["ollamaScanSummary"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ollamaScanSummary: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.ollamaScanSummary` + ": expected string"); }
  }
  {
    const fieldValue = value["ollamaScanStatus"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ollamaScanStatus: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.ollamaScanStatus` + ": expected string"); }
  }
  {
    const fieldValue = value["ollamaPullStatus"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ollamaPullStatus: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.ollamaPullStatus` + ": expected string"); }
  }
  {
    const fieldValue = value["ollamaNotice"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ollamaNotice: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.ollamaNotice` + ": expected string"); }
  }
  {
    const fieldValue = value["llamaCppReasoningLevel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppReasoningLevel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.llamaCppReasoningLevel` + ": expected string"); }
  }
  {
    const fieldValue = value["llamaCppChatModelPath"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppChatModelPath: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.llamaCppChatModelPath` + ": expected string"); }
  }
  {
    const fieldValue = value["llamaCppTitleModelPath"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppTitleModelPath: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.llamaCppTitleModelPath` + ": expected string"); }
  }
  {
    const fieldValue = value["llamaCppChatFormat"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppChatFormat: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.llamaCppChatFormat` + ": expected string"); }
  }
  {
    const fieldValue = value["llamaCppNCtx"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppNCtx: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.llamaCppNCtx` + ": expected number"); }
  }
  {
    const fieldValue = value["llamaCppNGpuLayers"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppNGpuLayers: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.llamaCppNGpuLayers` + ": expected number"); }
  }
  {
    const fieldValue = value["llamaCppNThreads"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppNThreads: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.llamaCppNThreads` + ": expected number"); }
  }
  {
    const fieldValue = value["llamaCppScannedModels"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppScannedModels: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.llamaCppScannedModels` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.llamaCppScannedModels` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["llamaCppScanSummary"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppScanSummary: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.llamaCppScanSummary` + ": expected string"); }
  }
  {
    const fieldValue = value["llamaCppScanStatus"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppScanStatus: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.llamaCppScanStatus` + ": expected string"); }
  }
  {
    const fieldValue = value["llamaCppNotice"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppNotice: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.llamaCppNotice` + ": expected string"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateAppSettingsState(value: unknown): ValidationResult<AppSettingsState> {
  const errors: string[] = [];
  checkAppSettingsState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as AppSettingsState }
    : { ok: false, errors };
}
