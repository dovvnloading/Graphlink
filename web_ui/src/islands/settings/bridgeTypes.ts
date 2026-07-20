/**
 * The settings island's state contract.
 *
 * Grown one page at a time per the Phase 3 increment sequence recorded in
 * doc/FRONTEND_WEB_MIGRATION_MASTER_PLAN.md: increment 2 shipped
 * activeSection alone; increment 3 adds the General/Appearance page's
 * fields. Each remaining page's own fields land in its own later
 * increment.
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

export const NOTIFICATION_TYPES = ["info", "success", "warning", "error"] as const;

export const initialSettingsState: SettingsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  activeSection: "General",
  theme: "dark",
  showTokenCounter: true,
  enableSystemPrompt: true,
  notificationPreferences: { info: true, success: true, warning: true, error: true },
  updateNotificationsEnabled: false,
  updateStatusMessage: "Automatic update checks are off.",
  updateStatusLevel: "info",
  updateLastCheckedAt: "",
  updateAvailable: false,
};
