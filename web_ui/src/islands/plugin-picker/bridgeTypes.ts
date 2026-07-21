/**
 * The plugin-picker island's state contract.
 *
 * Categories are static app-lifetime data (see graphlink_plugin_picker_bridge.py's
 * own docstring) - there is no per-open reset token like composer-picker's
 * `openToken`, because there is nothing that goes stale between opens.
 * Icons are deliberately absent from the wire contract (the same
 * "icons dropped" precedent as About/Help - qtawesome icon-name strings
 * don't resolve to anything in a web island without a new dependency).
 */
export type { PluginPickerState, PluginCategory, PluginEntry } from "../../lib/bridge-core/generated/plugin-picker-state";

import type { PluginPickerState } from "../../lib/bridge-core/generated/plugin-picker-state";

export const initialPluginPickerState: PluginPickerState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  categories: [],
};
