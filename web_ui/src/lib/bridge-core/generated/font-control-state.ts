/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_font_control_payload.py::FontControlStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface FontControlState {
  schemaVersion: number;
  revision: number;
  fontFamilies: string[];
  colorPresets: string[];
  sizeMin: number;
  sizeMax: number;
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

function checkFontControlState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["fontFamilies"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.fontFamilies: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.fontFamilies` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.fontFamilies` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["colorPresets"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.colorPresets: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.colorPresets` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.colorPresets` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["sizeMin"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sizeMin: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.sizeMin` + ": expected number"); }
  }
  {
    const fieldValue = value["sizeMax"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sizeMax: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.sizeMax` + ": expected number"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateFontControlState(value: unknown): ValidationResult<FontControlState> {
  const errors: string[] = [];
  checkFontControlState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as FontControlState }
    : { ok: false, errors };
}
