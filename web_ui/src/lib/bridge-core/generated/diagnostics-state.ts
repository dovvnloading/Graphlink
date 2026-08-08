/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_diagnostics_payload.py::DiagnosticsStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface DiagnosticsRunRow {
  runId: string;
  kind: string;
  nodeId?: string | null;
  outcome: string;
  durationSeconds?: number | null;
}

export interface DiagnosticsProviderError {
  provider: string;
  message: string;
  at: number;
}

export interface DiagnosticsState {
  schemaVersion: number;
  revision: number;
  recentRuns: DiagnosticsRunRow[];
  publishCount: number;
  publishBytesTotal: number;
  lastPublishBytes?: number | null;
  lastPublishTopic?: string | null;
  publishBytesPerSecond: number;
  sessionCount?: number | null;
  providerErrors: DiagnosticsProviderError[];
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

function checkDiagnosticsRunRow(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["runId"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.runId: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.runId` + ": expected string"); }
  }
  {
    const fieldValue = value["kind"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.kind: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.kind` + ": expected string"); }
  }
  {
    const fieldValue = value["nodeId"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.nodeId` + ": expected string"); }
  }
  {
    const fieldValue = value["outcome"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.outcome: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.outcome` + ": expected string"); }
  }
  {
    const fieldValue = value["durationSeconds"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.durationSeconds` + ": expected number"); }
  }
}

function checkDiagnosticsProviderError(value: unknown, path: string, errors: string[]): void {
  if (!isRecord(value)) { errors.push(`${path}: expected object`); return; }
  {
    const fieldValue = value["provider"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.provider: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.provider` + ": expected string"); }
  }
  {
    const fieldValue = value["message"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.message: missing required field`);
    else { if (typeof fieldValue !== "string") errors.push(`${path}.message` + ": expected string"); }
  }
  {
    const fieldValue = value["at"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.at: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.at` + ": expected number"); }
  }
}

function checkDiagnosticsState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["recentRuns"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.recentRuns: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.recentRuns` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkDiagnosticsRunRow(item, `${path}.recentRuns` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["publishCount"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.publishCount: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.publishCount` + ": expected number"); }
  }
  {
    const fieldValue = value["publishBytesTotal"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.publishBytesTotal: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.publishBytesTotal` + ": expected number"); }
  }
  {
    const fieldValue = value["lastPublishBytes"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.lastPublishBytes` + ": expected number"); }
  }
  {
    const fieldValue = value["lastPublishTopic"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "string") errors.push(`${path}.lastPublishTopic` + ": expected string"); }
  }
  {
    const fieldValue = value["publishBytesPerSecond"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.publishBytesPerSecond: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.publishBytesPerSecond` + ": expected number"); }
  }
  {
    const fieldValue = value["sessionCount"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.sessionCount` + ": expected number"); }
  }
  {
    const fieldValue = value["providerErrors"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.providerErrors: missing required field`);
    else { if (!Array.isArray(fieldValue)) errors.push(`${path}.providerErrors` + ": expected array");
    else (fieldValue as unknown[]).forEach((item, i) => { checkDiagnosticsProviderError(item, `${path}.providerErrors` + `[${i}]`, errors); }); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateDiagnosticsState(value: unknown): ValidationResult<DiagnosticsState> {
  const errors: string[] = [];
  checkDiagnosticsState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as DiagnosticsState }
    : { ok: false, errors };
}
