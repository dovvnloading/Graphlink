/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_grid_control_payload.py::GridControlStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface GridControlState {
  schemaVersion: number;
  revision: number;
  gridSize: number;
  gridOpacityPercent: number;
  gridStyle: string;
  gridColor: string;
  sizePresets: number[];
  stylePresets: string[];
  colorPresets: string[];
  minCompatibleSchemaVersion?: number | null;
}

const GRID_CONTROL_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["gridSize", "n", 0],
  ["gridOpacityPercent", "n", 0],
  ["gridStyle", "s", 0],
  ["gridColor", "s", 0],
  ["sizePresets", { a: "n" }, 0],
  ["stylePresets", { a: "s" }, 0],
  ["colorPresets", { a: "s" }, 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkGridControlState = compileFields(GRID_CONTROL_STATE_FIELDS);

export function validateGridControlState(value: unknown): ValidationResult<GridControlState> {
  const errors: string[] = [];
  checkGridControlState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as GridControlState }
    : { ok: false, errors };
}
