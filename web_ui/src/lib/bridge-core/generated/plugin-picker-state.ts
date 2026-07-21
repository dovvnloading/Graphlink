/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_plugin_picker_payload.py::PluginPickerStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface PluginCategory {
  name: string;
  description: string;
  plugins: PluginEntry[];
}

export interface PluginEntry {
  name: string;
  description: string;
}

export interface PluginPickerState {
  schemaVersion: number;
  revision: number;
  categories: PluginCategory[];
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

function checkPluginCategory(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["name"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.name: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.name` + ": expected string"); }
  }
  {
    const fieldValue = value["description"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.description: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.description` + ": expected string"); }
  }
  {
    const fieldValue = value["plugins"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.plugins: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.plugins` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkPluginEntry(item, `${path}.plugins` + `[${i}]`, errors); }); }
  }
}

function checkPluginEntry(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["name"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.name: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.name` + ": expected string"); }
  }
  {
    const fieldValue = value["description"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.description: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.description` + ": expected string"); }
  }
}

function checkPluginPickerState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["categories"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.categories: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.categories` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkPluginCategory(item, `${path}.categories` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validatePluginPickerState(value: unknown): ValidationResult<PluginPickerState> {
  const errors: string[] = [];
  checkPluginPickerState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as PluginPickerState }
    : { ok: false, errors };
}
