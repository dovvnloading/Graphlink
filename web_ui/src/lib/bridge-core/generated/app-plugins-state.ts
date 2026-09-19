/* GENERATED - do not hand-edit. Source of truth: contracts/graphlink_app_plugins_payload.py::AppPluginsStatePayload.
 * Regenerate with codegen.py; a pytest fails if this file
 * drifts from what regenerating it now would produce. */

import { type ValidationResult, type WireFields, compileFields } from "../wireCheck";

export type { ValidationResult };

export interface AppPluginCategory {
  name: string;
  description: string;
  plugins: AppPluginEntry[];
}

export interface AppPluginEntry {
  name: string;
  description: string;
}

export interface AppPluginGrant {
  pluginId: string;
  name: string;
  scopes: string[];
  granted: boolean;
}

export interface AppPluginsState {
  schemaVersion: number;
  revision: number;
  categories: AppPluginCategory[];
  grants: AppPluginGrant[];
  minCompatibleSchemaVersion?: number | null;
}

const APP_PLUGIN_ENTRY_FIELDS: WireFields = [
  ["name", "s", 0],
  ["description", "s", 0],
];

const APP_PLUGIN_CATEGORY_FIELDS: WireFields = [
  ["name", "s", 0],
  ["description", "s", 0],
  ["plugins", { a: { o: APP_PLUGIN_ENTRY_FIELDS } }, 0],
];

const APP_PLUGIN_GRANT_FIELDS: WireFields = [
  ["pluginId", "s", 0],
  ["name", "s", 0],
  ["scopes", { a: "s" }, 0],
  ["granted", "b", 0],
];

const APP_PLUGINS_STATE_FIELDS: WireFields = [
  ["schemaVersion", "n", 0],
  ["revision", "n", 0],
  ["categories", { a: { o: APP_PLUGIN_CATEGORY_FIELDS } }, 0],
  ["grants", { a: { o: APP_PLUGIN_GRANT_FIELDS } }, 0],
  ["minCompatibleSchemaVersion", "n", 1],
];

const checkAppPluginsState = compileFields(APP_PLUGINS_STATE_FIELDS);

export function validateAppPluginsState(value: unknown): ValidationResult<AppPluginsState> {
  const errors: string[] = [];
  checkAppPluginsState(value, "$", errors);
  return errors.length === 0
    ? { ok: true, value: value as AppPluginsState }
    : { ok: false, errors };
}
