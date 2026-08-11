/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_app_chat_library_payload.py::AppChatLibraryStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface AppChatLibraryRow {
  id: number;
  title: string;
  createdLabel: string;
  updatedLabel: string;
  createdAtIso?: string | null;
  updatedAtIso?: string | null;
  preview: string;
  messageCount: number;
  workspaceId: number;
  favorite: boolean;
  archived: boolean;
  tags: string[];
}

export interface AppWorkspaceRow {
  id: number;
  name: string;
  icon: string;
  archived: boolean;
}

export interface AppChatLibraryState {
  schemaVersion: number;
  revision: number;
  rows: AppChatLibraryRow[];
  workspaces: AppWorkspaceRow[];
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

function checkAppChatLibraryRow(value: unknown, path: string, errors: string[]): void {
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
  {
    const fieldValue = value["createdAtIso"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.createdAtIso` + ": expected string"); }
  }
  {
    const fieldValue = value["updatedAtIso"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.updatedAtIso` + ": expected string"); }
  }
  {
    const fieldValue = value["preview"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.preview: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.preview` + ": expected string"); }
  }
  {
    const fieldValue = value["messageCount"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.messageCount: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.messageCount` + ": expected number"); }
  }
  {
    const fieldValue = value["workspaceId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.workspaceId: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.workspaceId` + ": expected number"); }
  }
  {
    const fieldValue = value["favorite"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.favorite: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.favorite` + ": expected boolean"); }
  }
  {
    const fieldValue = value["archived"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.archived: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.archived` + ": expected boolean"); }
  }
  {
    const fieldValue = value["tags"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.tags: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.tags` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.tags` + `[${i}]` + ": expected string"); }); }
  }
}

function checkAppWorkspaceRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["id"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.id: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.id` + ": expected number"); }
  }
  {
    const fieldValue = value["name"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.name: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.name` + ": expected string"); }
  }
  {
    const fieldValue = value["icon"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.icon: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.icon` + ": expected string"); }
  }
  {
    const fieldValue = value["archived"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.archived: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.archived` + ": expected boolean"); }
  }
}

function checkAppChatLibraryState(value: unknown, path: string, errors: string[]): void {
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
    else (fieldValue as unknown[]).forEach((item, i) => { checkAppChatLibraryRow(item, `${path}.rows` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["workspaces"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.workspaces: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.workspaces` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkAppWorkspaceRow(item, `${path}.workspaces` + `[${i}]`, errors); }); }
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

export function validateAppChatLibraryState(value: unknown): ValidationResult<AppChatLibraryState> {
  const errors: string[] = [];
  checkAppChatLibraryState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as AppChatLibraryState }
    : { ok: false, errors };
}
