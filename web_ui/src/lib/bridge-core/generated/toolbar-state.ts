/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_toolbar_payload.py::ToolbarStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface ToolbarState {
  schemaVersion: number;
  revision: number;
  pinsChecked: boolean;
  activeSurface: string;
  modeOptions: string[];
  currentMode: string;
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

function checkToolbarState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["pinsChecked"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.pinsChecked: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.pinsChecked` + ": expected boolean"); }
  }
  {
    const fieldValue = value["activeSurface"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.activeSurface: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.activeSurface` + ": expected string"); }
  }
  {
    const fieldValue = value["modeOptions"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.modeOptions: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.modeOptions` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.modeOptions` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["currentMode"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.currentMode: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.currentMode` + ": expected string"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateToolbarState(value: unknown): ValidationResult<ToolbarState> {
  const errors: string[] = [];
  checkToolbarState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as ToolbarState }
    : { ok: false, errors };
}
