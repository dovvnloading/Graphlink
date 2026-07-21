/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_composer_context_payload.py::ComposerContextStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface ComposerContextItem {
  id: string;
  name: string;
  kind: string;
  tokenCount: number;
}

export interface ComposerContextAnchor {
  id: string;
  label: string;
  type: string;
}

export interface ComposerContextState {
  schemaVersion: number;
  revision: number;
  items: ComposerContextItem[];
  totalTokens: number;
  anchor?: ComposerContextAnchor | null;
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

function checkComposerContextItem(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.id` + ": expected string"); }
  }
  {
    const fieldValue = value["name"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.name: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.name` + ": expected string"); }
  }
  {
    const fieldValue = value["kind"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.kind: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.kind` + ": expected string"); }
  }
  {
    const fieldValue = value["tokenCount"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.tokenCount: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.tokenCount` + ": expected number"); }
  }
}

function checkComposerContextAnchor(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["type"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.type: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.type` + ": expected string"); }
  }
}

function checkComposerContextState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["items"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.items: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.items` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkComposerContextItem(item, `${path}.items` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["totalTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.totalTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.totalTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["anchor"];
    if (fieldValue !== undefined && fieldValue !== null) { checkComposerContextAnchor(fieldValue, `${path}.anchor`, errors); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateComposerContextState(value: unknown): ValidationResult<ComposerContextState> {
  const errors: string[] = [];
  checkComposerContextState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as ComposerContextState }
    : { ok: false, errors };
}
