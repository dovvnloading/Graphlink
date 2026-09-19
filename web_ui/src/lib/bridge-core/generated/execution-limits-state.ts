/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_execution_limits_payload.py::ExecutionLimitsStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface ExecutionLimitsState {
  schemaVersion: number;
  revision: number;
  codeSandboxResourceLimitsText: string;
  minCompatibleSchemaVersion?: number | null;
}

const EXECUTION_LIMITS_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["codeSandboxResourceLimitsText", "s", 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkExecutionLimitsState = compileFields(EXECUTION_LIMITS_STATE_FIELDS);

export function validateExecutionLimitsState(value: unknown): ValidationResult<ExecutionLimitsState> {
  const errors: string[] = [];
  checkExecutionLimitsState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as ExecutionLimitsState }
    : { ok: false, errors };
}
