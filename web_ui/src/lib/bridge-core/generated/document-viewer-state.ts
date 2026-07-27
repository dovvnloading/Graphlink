/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_document_viewer_payload.py::DocumentViewerStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface DocumentViewerState {
  schemaVersion: number;
  revision: number;
  content: string;
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

function checkDocumentViewerState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["content"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.content: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.content` + ": expected string"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateDocumentViewerState(value: unknown): ValidationResult<DocumentViewerState> {
  const errors: string[] = [];
  checkDocumentViewerState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as DocumentViewerState }
    : { ok: false, errors };
}
