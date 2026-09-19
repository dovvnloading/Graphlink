/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_app_chat_library_payload.py::AppChatLibraryStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface AppChatLibraryRow {
  id: number;
  title: string;
  createdLabel: string;
  updatedLabel: string;
  createdAtIso?: string | null;
  updatedAtIso?: string | null;
  preview: string;
  messageCount: number;
  workspaceId: number;
  favorite: boolean;
  archived: boolean;
  tags: string[];
}

export interface AppWorkspaceRow {
  id: number;
  name: string;
  icon: string;
  archived: boolean;
  defaultModelProvider: string;
  defaultModelId: string;
}

export interface AppChatLibraryState {
  schemaVersion: number;
  revision: number;
  rows: AppChatLibraryRow[];
  workspaces: AppWorkspaceRow[];
  notice?: string | null;
  minCompatibleSchemaVersion?: number | null;
}

const APP_CHAT_LIBRARY_ROW_FIELDS: WireFields = [
  ["id", "n", 0],
  ["title", "s", 0],
  ["createdLabel", "s", 0],
  ["updatedLabel", "s", 0],
  ["createdAtIso", "s", 1],
  ["updatedAtIso", "s", 1],
  ["preview", "s", 0],
  ["messageCount", "n", 0],
  ["workspaceId", "n", 0],
  ["favorite", "b", 0],
  ["archived", "b", 0],
  ["tags", { a: "s" }, 0],
];

const APP_WORKSPACE_ROW_FIELDS: WireFields = [
  ["id", "n", 0],
  ["name", "s", 0],
  ["icon", "s", 0],
  ["archived", "b", 0],
  ["defaultModelProvider", "s", 0],
  ["defaultModelId", "s", 0],
];

const APP_CHAT_LIBRARY_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["rows", { a: { o: APP_CHAT_LIBRARY_ROW_FIELDS } }, 0],
  ["workspaces", { a: { o: APP_WORKSPACE_ROW_FIELDS } }, 0],
  ["notice", "s", 1],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkAppChatLibraryState = compileFields(APP_CHAT_LIBRARY_STATE_FIELDS);

export function validateAppChatLibraryState(value: unknown): ValidationResult<AppChatLibraryState> {
  const errors: string[] = [];
  checkAppChatLibraryState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as AppChatLibraryState }
    : { ok: false, errors };
}
