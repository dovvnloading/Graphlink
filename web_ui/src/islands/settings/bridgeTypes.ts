/**
 * The settings island's state contract.
 *
 * Phase 3 increment 2 (shell-only, per the recorded scope note on the
 * Phase 3 checklist item in doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md):
 * activeSection navigation only. Each page's own fields land in its own
 * later increment.
 */
export type { SettingsState } from "../../lib/bridge-core/generated/settings-state";

import type { SettingsState } from "../../lib/bridge-core/generated/settings-state";

export const SECTION_NAMES = [
  "General",
  "Ollama (Local)",
  "Llama.cpp (Local)",
  "API Endpoint",
  "Integrations",
] as const;

export const initialSettingsState: SettingsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  activeSection: "General",
};
