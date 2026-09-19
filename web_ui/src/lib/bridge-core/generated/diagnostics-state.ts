/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_diagnostics_payload.py::DiagnosticsStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

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

const DIAGNOSTICS_RUN_ROW_FIELDS: WireFields = [
  ["runId", "s", 0],
  ["kind", "s", 0],
  ["nodeId", "s", 1],
  ["outcome", "s", 0],
  ["durationSeconds", "n", 1],
];

const DIAGNOSTICS_PROVIDER_ERROR_FIELDS: WireFields = [
  ["provider", "s", 0],
  ["message", "s", 0],
  ["at", "n", 0],
];

const DIAGNOSTICS_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["recentRuns", { a: { o: DIAGNOSTICS_RUN_ROW_FIELDS } }, 0],
  ["publishCount", "n", 0],
  ["publishBytesTotal", "n", 0],
  ["lastPublishBytes", "n", 1],
  ["lastPublishTopic", "s", 1],
  ["publishBytesPerSecond", "n", 0],
  ["sessionCount", "n", 1],
  ["providerErrors", { a: { o: DIAGNOSTICS_PROVIDER_ERROR_FIELDS } }, 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkDiagnosticsState = compileFields(DIAGNOSTICS_STATE_FIELDS);

export function validateDiagnosticsState(value: unknown): ValidationResult<DiagnosticsState> {
  const errors: string[] = [];
  checkDiagnosticsState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as DiagnosticsState }
    : { ok: false, errors };
}
