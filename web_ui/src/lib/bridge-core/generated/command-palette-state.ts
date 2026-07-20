/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_command_palette_payload.py::CommandPaletteStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface CommandEntry {
  id: string;
  name: string;
  aliases: string[];
}

export interface CommandPaletteState {
  schemaVersion: number;
  revision: number;
  visible: boolean;
  commands: CommandEntry[];
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

function checkCommandEntry(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["aliases"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.aliases: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.aliases` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { if (typeof item !== "string") errors.push(`${path}.aliases` + `[${i}]` + ": expected string"); }); }
  }
}

function checkCommandPaletteState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["visible"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.visible: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.visible` + ": expected boolean"); }
  }
  {
    const fieldValue = value["commands"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.commands: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.commands` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkCommandEntry(item, `${path}.commands` + `[${i}]`, errors); }); }
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

export function validateCommandPaletteState(value: unknown): ValidationResult<CommandPaletteState> {
  const errors: string[] = [];
  checkCommandPaletteState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as CommandPaletteState }
    : { ok: false, errors };
}
