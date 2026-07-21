/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_drag_speed_payload.py::DragSpeedStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface DragSpeedState {
  schemaVersion: number;
  revision: number;
  percentPresets: number[];
  percentMin: number;
  percentMax: number;
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

function checkDragSpeedState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["percentPresets"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.percentPresets: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.percentPresets` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "number") errors.push(`${path}.percentPresets` + `[${i}]` + ": expected number"); }); }
  }
  {
    const fieldValue = value["percentMin"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.percentMin: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.percentMin` + ": expected number"); }
  }
  {
    const fieldValue = value["percentMax"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.percentMax: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.percentMax` + ": expected number"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateDragSpeedState(value: unknown): ValidationResult<DragSpeedState> {
  const errors: string[] = [];
  checkDragSpeedState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as DragSpeedState }
    : { ok: false, errors };
}
