/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_settings_payload.py::SettingsStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface SettingsState {
  schemaVersion: number;
  revision: number;
  activeSection: string;
  theme: string;
  showTokenCounter: boolean;
  enableSystemPrompt: boolean;
  notificationPreferences: Record<string, boolean>;
  updateNotificationsEnabled: boolean;
  updateStatusMessage: string;
  updateStatusLevel: string;
  updateLastCheckedAt: string;
  updateAvailable: boolean;
  updateLatestVersion: string;
  updateCheckInProgress: boolean;
  githubTokenConfigured: boolean;
  apiProvider: string;
  apiBaseUrl: string;
  openaiKeyConfigured: boolean;
  anthropicKeyConfigured: boolean;
  geminiKeyConfigured: boolean;
  apiTaskModels: Record<string, string>;
  apiAvailableModels: string[];
  apiLoadStatus: string;
  ollamaReasoningMode: string;
  ollamaCurrentModel: string;
  ollamaModelAssignments: Record<string, string>;
  ollamaScannedModels: string[];
  ollamaScanSummary: string;
  ollamaScanStatus: string;
  ollamaPullStatus: string;
  llamaCppReasoningMode: string;
  llamaCppChatModelPath: string;
  llamaCppTitleModelPath: string;
  llamaCppChatFormat: string;
  llamaCppNCtx: number;
  llamaCppNGpuLayers: number;
  llamaCppNThreads: number;
  llamaCppScannedModels: string[];
  llamaCppScanSummary: string;
  llamaCppScanStatus: string;
  notice?: string | null;
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

function checkSettingsState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["theme"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.theme: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.theme` + ": expected string"); }
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
    const fieldValue = value["updateNotificationsEnabled"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.updateNotificationsEnabled: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.updateNotificationsEnabled` + ": expected boolean"); }
  }
  {
    const fieldValue = value["updateStatusMessage"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.updateStatusMessage: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.updateStatusMessage` + ": expected string"); }
  }
  {
    const fieldValue = value["updateStatusLevel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.updateStatusLevel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.updateStatusLevel` + ": expected string"); }
  }
  {
    const fieldValue = value["updateLastCheckedAt"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.updateLastCheckedAt: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.updateLastCheckedAt` + ": expected string"); }
  }
  {
    const fieldValue = value["updateAvailable"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.updateAvailable: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.updateAvailable` + ": expected boolean"); }
  }
  {
    const fieldValue = value["updateLatestVersion"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.updateLatestVersion: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.updateLatestVersion` + ": expected string"); }
  }
  {
    const fieldValue = value["updateCheckInProgress"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.updateCheckInProgress: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.updateCheckInProgress` + ": expected boolean"); }
  }
  {
    const fieldValue = value["githubTokenConfigured"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.githubTokenConfigured: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.githubTokenConfigured` + ": expected boolean"); }
  }
  {
    const fieldValue = value["apiProvider"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiProvider: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.apiProvider` + ": expected string"); }
  }
  {
    const fieldValue = value["apiBaseUrl"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiBaseUrl: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.apiBaseUrl` + ": expected string"); }
  }
  {
    const fieldValue = value["openaiKeyConfigured"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.openaiKeyConfigured: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.openaiKeyConfigured` + ": expected boolean"); }
  }
  {
    const fieldValue = value["anthropicKeyConfigured"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.anthropicKeyConfigured: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.anthropicKeyConfigured` + ": expected boolean"); }
  }
  {
    const fieldValue = value["geminiKeyConfigured"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.geminiKeyConfigured: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.geminiKeyConfigured` + ": expected boolean"); }
  }
  {
    const fieldValue = value["apiTaskModels"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiTaskModels: missing required field`);
    else { if (!isRecord(fieldValue)) errors.push(`${path}.apiTaskModels` + ": expected object");
    else Object.entries(fieldValue as Record<string, unknown>).forEach(([k, v]) => { if (typeof v !== "string") errors.push(`${path}.apiTaskModels` + `[${JSON.stringify(k)}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["apiAvailableModels"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiAvailableModels: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.apiAvailableModels` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.apiAvailableModels` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["apiLoadStatus"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.apiLoadStatus: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.apiLoadStatus` + ": expected string"); }
  }
  {
    const fieldValue = value["ollamaReasoningMode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.ollamaReasoningMode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.ollamaReasoningMode` + ": expected string"); }
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
    const fieldValue = value["llamaCppReasoningMode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.llamaCppReasoningMode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.llamaCppReasoningMode` + ": expected string"); }
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
    const fieldValue = value["notice"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.notice` + ": expected string"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateSettingsState(value: unknown): ValidationResult<SettingsState> {
  const errors: string[] = [];
  checkSettingsState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as SettingsState }
    : { ok: false, errors };
}
