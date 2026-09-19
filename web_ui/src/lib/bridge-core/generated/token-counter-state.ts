/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_token_counter_payload.py::TokenCounterStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

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

const TOKEN_COUNTER_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["inputTokens", "n", 0],
  ["outputTokens", "n", 0],
  ["contextTokens", "n", 0],
  ["totalTokens", "n", 0],
  ["promptTokens", "n", 1],
  ["completionTokens", "n", 1],
  ["usageIsReal", "b", 0],
  ["estimatedCostUsd", "n", 1],
  ["sessionPromptTokens", "n", 0],
  ["sessionCompletionTokens", "n", 0],
  ["sessionEstimatedCostUsd", "n", 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkTokenCounterState = compileFields(TOKEN_COUNTER_STATE_FIELDS);

export function validateTokenCounterState(value: unknown): ValidationResult<TokenCounterState> {
  const errors: string[] = [];
  checkTokenCounterState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as TokenCounterState }
    : { ok: false, errors };
}
