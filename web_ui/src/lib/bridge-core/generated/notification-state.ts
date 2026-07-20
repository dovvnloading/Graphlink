/* GENERATED - do not hand-edit. Source of truth: graphlink_app/graphlink_notification_payload.py::NotificationStatePayload.
 * Regenerate with graphlink_island_codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface NotificationState {
  schemaVersion: number;
  revision: number;
  visible: boolean;
  message: string;
  msgType: "info" | "success" | "warning" | "error";
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

function checkNotificationState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["message"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.message: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.message` + ": expected string"); }
  }
  {
    const fieldValue = value["msgType"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.msgType: missing required field`);
    else { if (!["info", "success", "warning", "error"].includes(fieldValue as string)) errors.push(`${path}.msgType` + `: ${JSON.stringify(fieldValue)} is not one of [` + "info, success, warning, error" + `]`); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateNotificationState(value: unknown): ValidationResult<NotificationState> {
  const errors: string[] = [];
  checkNotificationState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as NotificationState }
    : { ok: false, errors };
}
