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

export const API_PROVIDERS = ["OpenAI-Compatible", "Anthropic Claude", "Google Gemini"] as const;

export const API_TASKS = [
  "task_title",
  "task_chat",
  "task_chart",
  "task_image_gen",
  "task_web_validate",
  "task_web_summarize",
] as const;

export const API_TASK_LABELS: Record<(typeof API_TASKS)[number], string> = {
  task_title: "Chat Naming / Session Title",
  task_chat: "Chat, Explain, Takeaways (main model)",
  task_chart: "Chart Generation (code-capable model)",
  task_image_gen: "Image Generation",
  task_web_validate: "Web Content Validation",
  task_web_summarize: "Web Content Summarization",
};

export const OLLAMA_TASKS = ["task_chat", "task_title", "task_chart", "task_web_validate", "task_web_summarize"] as const;

export const OLLAMA_TASK_LABELS: Record<(typeof OLLAMA_TASKS)[number], string> = {
  task_chat: "Chat Model",
  task_title: "Chat Naming Model",
  task_chart: "Chart Generation Model",
  task_web_validate: "Web Content Validation Model",
  task_web_summarize: "Web Content Summarization Model",
};

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
  updateLatestVersion: "",
  updateCheckInProgress: false,
  githubTokenConfigured: false,
  apiProvider: "OpenAI-Compatible",
  apiBaseUrl: "https://api.openai.com/v1",
  openaiKeyConfigured: false,
  anthropicKeyConfigured: false,
  geminiKeyConfigured: false,
  apiTaskModels: {},
  apiAvailableModels: [],
  apiImageModels: [],
  apiLoadStatus: "idle",
  ollamaReasoningMode: "Thinking",
  ollamaCurrentModel: "",
  // Matches SettingsManager's real defaults exactly (task_chat starts
  // "auto", every other task starts "inherit" - see
  // graphlink_licensing.py::_create_initial_state).
  ollamaModelAssignments: {
    task_chat: "auto",
    task_title: "inherit",
    task_chart: "inherit",
    task_web_validate: "inherit",
    task_web_summarize: "inherit",
  },
  ollamaScannedModels: [],
  ollamaScanSummary: "No saved scan yet. Run a system scan or choose a folder to build the local model list.",
  ollamaScanStatus: "idle",
  ollamaPullStatus: "idle",
  llamaCppReasoningMode: "Thinking",
  llamaCppChatModelPath: "",
  llamaCppTitleModelPath: "",
  llamaCppChatFormat: "",
  llamaCppNCtx: 4096,
  llamaCppNGpuLayers: 0,
  llamaCppNThreads: 0,
  llamaCppScannedModels: [],
  llamaCppScanSummary: "No saved GGUF scan yet. Run a system scan or choose a folder to build the local model list.",
  llamaCppScanStatus: "idle",
  notice: null,
};
