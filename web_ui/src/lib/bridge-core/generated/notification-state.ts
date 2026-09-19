/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_notification_payload.py::NotificationStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface NotificationState {
  schemaVersion: number;
  revision: number;
  visible: boolean;
  message: string;
  msgType: "info" | "success" | "warning" | "error";
  minCompatibleSchemaVersion?: number | null;
}

const NOTIFICATION_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["visible", "b", 0],
  ["message", "s", 0],
  ["msgType", { e: ["info", "success", "warning", "error"] }, 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkNotificationState = compileFields(NOTIFICATION_STATE_FIELDS);

export function validateNotificationState(value: unknown): ValidationResult<NotificationState> {
  const errors: string[] = [];
  checkNotificationState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as NotificationState }
    : { ok: false, errors };
}
