/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_grid_control_payload.py::GridControlStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface GridControlState {
  schemaVersion: number;
  revision: number;
  gridSize: number;
  gridOpacityPercent: number;
  gridStyle: string;
  gridColor: string;
  sizePresets: number[];
  stylePresets: string[];
  colorPresets: string[];
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

function checkGridControlState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["gridSize"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gridSize: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.gridSize` + ": expected number"); }
  }
  {
    const fieldValue = value["gridOpacityPercent"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gridOpacityPercent: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.gridOpacityPercent` + ": expected number"); }
  }
  {
    const fieldValue = value["gridStyle"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gridStyle: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gridStyle` + ": expected string"); }
  }
  {
    const fieldValue = value["gridColor"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.gridColor: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.gridColor` + ": expected string"); }
  }
  {
    const fieldValue = value["sizePresets"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sizePresets: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.sizePresets` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "number") errors.push(`${path}.sizePresets` + `[${i}]` + ": expected number"); }); }
  }
  {
    const fieldValue = value["stylePresets"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.stylePresets: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.stylePresets` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.stylePresets` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["colorPresets"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.colorPresets: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.colorPresets` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.colorPresets` + `[${i}]` + ": expected string"); }); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateGridControlState(value: unknown): ValidationResult<GridControlState> {
  const errors: string[] = [];
  checkGridControlState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as GridControlState }
    : { ok: false, errors };
}
