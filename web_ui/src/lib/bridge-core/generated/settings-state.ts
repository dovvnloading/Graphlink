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
