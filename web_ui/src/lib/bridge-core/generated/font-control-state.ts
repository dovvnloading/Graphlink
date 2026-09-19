/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_font_control_payload.py::FontControlStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface FontControlState {
  schemaVersion: number;
  revision: number;
  fontFamilies: string[];
  colorPresets: string[];
  sizeMin: number;
  sizeMax: number;
  minCompatibleSchemaVersion?: number | null;
}

const FONT_CONTROL_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["fontFamilies", { a: "s" }, 0],
  ["colorPresets", { a: "s" }, 0],
  ["sizeMin", "n", 0],
  ["sizeMax", "n", 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkFontControlState = compileFields(FONT_CONTROL_STATE_FIELDS);

export function validateFontControlState(value: unknown): ValidationResult<FontControlState> {
  const errors: string[] = [];
  checkFontControlState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as FontControlState }
    : { ok: false, errors };
}
