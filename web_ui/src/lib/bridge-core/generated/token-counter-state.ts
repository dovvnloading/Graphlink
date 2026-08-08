/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_token_counter_payload.py::TokenCounterStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

export interface TokenCounterState {
  schemaVersion: number;
  revision: number;
  inputTokens: number;
  outputTokens: number;
  contextTokens: number;
  totalTokens: number;
  promptTokens?: number | null;
  completionTokens?: number | null;
  usageIsReal: boolean;
  estimatedCostUsd?: number | null;
  sessionPromptTokens: number;
  sessionCompletionTokens: number;
  sessionEstimatedCostUsd: number;
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

function checkTokenCounterState(value: unknown, path: string, errors: string[]): void {
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
    const fieldValue = value["inputTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.inputTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.inputTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["outputTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.outputTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.outputTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["contextTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.contextTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.contextTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["totalTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.totalTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.totalTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["promptTokens"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.promptTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["completionTokens"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.completionTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["usageIsReal"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.usageIsReal: missing required field`);
    else { if (typeof fieldValue !== "boolean") errors.push(`${path}.usageIsReal` + ": expected boolean"); }
  }
  {
    const fieldValue = value["estimatedCostUsd"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.estimatedCostUsd` + ": expected number"); }
  }
  {
    const fieldValue = value["sessionPromptTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sessionPromptTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.sessionPromptTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["sessionCompletionTokens"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sessionCompletionTokens: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.sessionCompletionTokens` + ": expected number"); }
  }
  {
    const fieldValue = value["sessionEstimatedCostUsd"];
    if (fieldValue === undefined || fieldValue === null) errors.push(`${path}.sessionEstimatedCostUsd: missing required field`);
    else { if (typeof fieldValue !== "number") errors.push(`${path}.sessionEstimatedCostUsd` + ": expected number"); }
  }
  {
    const fieldValue = value["minCompatibleSchemaVersion"];
    if (fieldValue !== undefined && fieldValue !== null) { if (typeof fieldValue !== "number") errors.push(`${path}.minCompatibleSchemaVersion` + ": expected number"); }
  }
}

export function validateTokenCounterState(value: unknown): ValidationResult<TokenCounterState> {
  const errors: string[] = [];
  checkTokenCounterState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as TokenCounterState }
    : { ok: false, errors };
}
