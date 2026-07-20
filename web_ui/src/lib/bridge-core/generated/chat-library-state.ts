/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_chat_library_payload.py::ChatLibraryStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface ChatLibraryRow {
  id: number;
  title: string;
  createdLabel: string;
  updatedLabel: string;
}

export interface ChatLibraryState {
  schemaVersion: number;
  revision: number;
  rows: ChatLibraryRow[];
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

function checkChatLibraryRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.id` + ": expected number"); }
  }
  {
    const fieldValue = value["title"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.title: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.title` + ": expected string"); }
  }
  {
    const fieldValue = value["createdLabel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.createdLabel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.createdLabel` + ": expected string"); }
  }
  {
    const fieldValue = value["updatedLabel"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.updatedLabel: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.updatedLabel` + ": expected string"); }
  }
}

function checkChatLibraryState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["rows"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.rows: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.rows` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkChatLibraryRow(item, `${path}.rows` + `[${i}]`, errors); }); }
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

export function validateChatLibraryState(value: unknown): ValidationResult<ChatLibraryState> {
  const errors: string[] = [];
  checkChatLibraryState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as ChatLibraryState }
    : { ok: false, errors };
}
