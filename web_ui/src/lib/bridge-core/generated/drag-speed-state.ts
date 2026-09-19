/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_drag_speed_payload.py::DragSpeedStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface DragSpeedState {
  schemaVersion: number;
  revision: number;
  percentPresets: number[];
  percentMin: number;
  percentMax: number;
  minCompatibleSchemaVersion?: number | null;
}

const DRAG_SPEED_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["percentPresets", { a: "n" }, 0],
  ["percentMin", "n", 0],
  ["percentMax", "n", 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkDragSpeedState = compileFields(DRAG_SPEED_STATE_FIELDS);

export function validateDragSpeedState(value: unknown): ValidationResult<DragSpeedState> {
  const errors: string[] = [];
  checkDragSpeedState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as DragSpeedState }
    : { ok: false, errors };
}
