/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_composer_picker_payload.py::ComposerPickerStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface ComposerPickerOption {
  id: string;
  label: string;
  meta: string;
  current: boolean;
  unavailable: boolean;
}

export interface ComposerPickerState {
  schemaVersion: number;
  revision: number;
  kind: string;
  title: string;
  options: ComposerPickerOption[];
  openToken: number;
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

function checkComposerPickerOption(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["label"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.label: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.label` + ": expected string"); }
  }
  {
    const fieldValue = value["meta"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.meta: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.meta` + ": expected string"); }
  }
  {
    const fieldValue = value["current"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.current: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.current` + ": expected boolean"); }
  }
  {
    const fieldValue = value["unavailable"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.unavailable: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.unavailable` + ": expected boolean"); }
  }
}

function checkComposerPickerState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["kind"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.kind: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.kind` + ": expected string"); }
  }
  {
    const fieldValue = value["title"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.title: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.title` + ": expected string"); }
  }
  {
    const fieldValue = value["options"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.options: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.options` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkComposerPickerOption(item, `${path}.options` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["openToken"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.openToken: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.openToken` + ": expected number"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateComposerPickerState(value: unknown): ValidationResult<ComposerPickerState> {
  const errors: string[] = [];
  checkComposerPickerState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as ComposerPickerState }
    : { ok: false, errors };
}
