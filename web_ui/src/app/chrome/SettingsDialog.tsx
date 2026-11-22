import { useEffect, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppPluginsState } from "../../lib/bridge-core/generated/app-plugins-state";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import { Dialog, useOverlays } from "../overlays/overlays";
import { CloudProviderSettings } from "./CloudProviderSettings";
import { KnowledgeSettingsPage } from "./KnowledgeSettingsPage";
import { ModelPricingSettings } from "./ModelPricingSettings";
import { OllamaSettingsPage } from "./OllamaSettingsPage";
import { GeneralPage } from "./settings/GeneralPage";
import { FalAiPage } from "./settings/FalAiPage";
import { IntegrationsPage } from "./settings/IntegrationsPage";
import { LlamaCppPage } from "./settings/LlamaCppPage";
import { WorkspaceAgentPage } from "./settings/WorkspaceAgentPage";
import { McpServersPage } from "./settings/McpServersPage";
import { PluginsPage } from "./settings/PluginsPage";
import { ResourceLimitsPage } from "./settings/ResourceLimitsPage";

export { parseEnvLines } from "./settings/McpServersPage";

/**
 * The settings dialog (Qt-removal plan R2.5d, extended R7.4a-c) - settings
 * island's SPA successor. Every page R2.5d originally deferred is real now:
 * General, Integrations, API-provider, Ollama, and (R7.4c) Llama.cpp. The
 * Ollama page's own "Scan Folder..." button, deferred alongside Llama.cpp
 * pending a native folder-picker capability, is real too - see
 * backend/native_dialogs.py's docstring for the mechanism.
 */

const SECTIONS = [
  "General",
  "Model pricing",
  "Knowledge",
  "Ollama (Local)",
  "Llama.cpp (Local)",
  "API Endpoint",
  "Fal.ai",
  "Integrations",
  "MCP Servers",
  "Workspace Agent",
  "Plugins",
  "Resource Limits",
] as const;
type Section = (typeof SECTIONS)[number];


// Local mode IDs are also used by the backend setProviderMode intent.
const MODE_OLLAMA_LOCAL = "Ollama (Local)";



const initialState: AppSettingsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  activeSection: "General",
  showTokenCounter: true,
  enableSystemPrompt: true,
  notificationPreferences: {},
  githubTokenConfigured: false,
  falApiKeySource: "none",
  falJobsJson: "[]",
  falNotice: "",
  falBusy: false,
  falSyncResultJson: "",
  falDiscoveryJson: "",
  falUploadUrl: "",
  // ADR-004 stage 4.4: optimistic default (true = encrypted) so the
  // persistent badge below never flashes a false warning before the
  // first real snapshot arrives - the real backend value replaces this
  // on the very first "app-settings" publish, same as every other field
  // here.
  secretsEncryptedAtRest: true,
  // ADR-016 stage 16.1: mirrors SettingsManager.get_log_level()'s own default.
  logLevel: "INFO",
  // ADR-018 stage 18.4: mirrors SettingsManager.get_auto_model_policy()'s
  // own default.
  autoModelPolicy: "cheapest-capable",
  // ADR-012 stage 12.2: mirrors SettingsManager.get_theme()'s own default.
  theme: "system",
  // ADR-012 stage 12.6: mirrors SettingsManager.get_has_completed_onboarding()'s
  // own default - OnboardingDialog.tsx is the surface that actually acts on
  // this; SettingsDialog only needs it to satisfy the shared contract.
  hasCompletedOnboarding: false,
  // ADR-012 stage 12.6: mirrors SettingsManager.get_current_mode()'s own default.
  providerMode: MODE_OLLAMA_LOCAL,
  activeApiProvider: "",
  viewingApiProvider: "",
  apiProviders: [],
  apiBaseUrl: "",
  apiKeyConfigured: {},
  apiKeySource: {},
  apiModels: {},
  apiModelCatalog: [],
  pricingOverrides: [],
  effectiveModelPrices: [],
  apiCatalogBaseUrl: "",
  apiCatalogStatus: "idle",
  apiCatalogMessage: "Model catalog has not been refreshed yet.",
  geminiStaticModels: [],
  geminiStaticImageModels: [],
  ollamaReasoningLevel: "high",
  ollamaCurrentModel: "",
  ollamaModelAssignments: {},
  ollamaContextWindows: {},
  ollamaContextModel: "",
  ollamaContextStatus: "idle",
  ollamaContextNotice: "",
  ollamaContextTrainedLength: 0,
  ollamaContextModelSizeBytes: 0,
  ollamaContextKvBytesPerToken: 0,
  ollamaContextAvailableMemoryBytes: 0,
  ollamaScannedModels: [],
  ollamaInventory: [],
  ollamaModelChecks: [],
  ollamaInventorySource: "",
  ollamaServerReachable: null,
  ollamaServerStatus: "checking",
  ollamaStartStatus: "idle",
  ollamaStartError: "",
  ollamaScanSummary: "",
  ollamaScanStatus: "idle",
  ollamaPullStatus: "idle",
  ollamaPullModel: "",
  ollamaPullMessage: "",
  ollamaPullCompleted: 0,
  ollamaPullTotal: 0,
  ollamaRemoveStatus: "idle",
  ollamaRemoveModel: "",
  ollamaNotice: "",
  knowledgeEmbeddingSource: "off",
  knowledgeEmbeddingModel: "",
  knowledgeCloudProvider: "OpenAI",
  knowledgeCloudOptIn: false,
  knowledgeOllamaModels: [],
  knowledgeOllamaCapableModels: [],
  knowledgeScanStatus: "idle",
  knowledgeTestStatus: "idle",
  knowledgeNotice: "",
  llamaCppReasoningLevel: "high",
  llamaCppChatModelPath: "",
  llamaCppTitleModelPath: "",
  llamaCppChatFormat: "",
  llamaCppNCtx: 4096,
  llamaCppNGpuLayers: 0,
  llamaCppNThreads: 0,
  llamaCppScannedModels: [],
  llamaCppModels: [],
  llamaCppScanSummary: "",
  llamaCppScanStatus: "idle",
  llamaCppNotice: "",
  // ADR-012 stage 12.6: mirrors SettingsManager.get_mcp_servers()'s own default.
  harnessTrustedDirs: [],
  mcpServers: [],
};

const initialPluginsState: AppPluginsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  categories: [],
  // ADR-014 stage 14.4: one entry per discovered NON-built-in plugin (see
  // backend/plugins.py's plugin_registry.picker_entries/node_kinds split vs.
  // builtin_actions, and AppPluginGrantPayload's own docstring in
  // contracts/graphlink_app_plugins_payload.py for the "one row per
  // distinct plugin_id" construction rule) - built-ins never appear here.
  grants: [],
};

function sectionKey(section: Section): string {
  // Backend storage is UI-navigation-only for this field (mirrors the
  // legacy bridge - see backend/settings.py's active_section cell), so any
  // stable string works; reusing the display name keeps the wire traffic
  // human-readable in the WS inspector.
  return section === "General" ? "general" : section === "Integrations" ? "integrations" : section.toLowerCase();
}


export function SettingsDialog({ transport }: { transport: WsTransport }) {
  const [state, setState] = useState<AppSettingsState>(initialState);
  // ADR-014 stage 14.4: a second, independent subscription to "app-plugins"
  // (the same topic PluginPicker.tsx already reads) - the grants list isn't
  // part of "app-settings" (which this dialog otherwise reads exclusively),
  // so this is a sibling subscription, not a repurposing of `state` above.
  const [pluginsState, setPluginsState] = useState<AppPluginsState>(initialPluginsState);
  const overlays = useOverlays();
  const settingsOpen = overlays.isOpen("settings");

  // The dialog stays mounted after closing. Poll only while visible, including
  // when a non-Ollama Settings section is selected. Focus/reopen refreshes
  // immediately without re-scanning every installed model.
  useEffect(() => {
    if (!settingsOpen) return;
    // This is a best-effort health poll; the result arrives through the
    // app-settings subscription, not a WebSocket request reply. A slow probe
    // must not leave a stale "request timed out" notification after Connected.
    const checkStatus = () => transport.intent("app-settings", "checkOllamaStatus", []);
    const onVisible = () => {
      if (document.visibilityState === "visible") checkStatus();
    };
    checkStatus();
    const interval = window.setInterval(checkStatus, 30_000);
    window.addEventListener("focus", checkStatus);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", checkStatus);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [settingsOpen, transport]);

  useEffect(() => {
    return transport.subscribe("app-settings", (payload) => {
      const validated = TOPIC_VALIDATORS["app-settings"](payload);
      if (validated.ok) setState(validated.value as AppSettingsState);
      else console.error("[app-settings] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  useEffect(() => {
    return transport.subscribe("app-plugins", (payload) => {
      const validated = TOPIC_VALIDATORS["app-plugins"](payload);
      if (validated.ok) setPluginsState(validated.value as AppPluginsState);
      else console.error("[app-plugins] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  const activeSection = (SECTIONS.find((s) => sectionKey(s) === state.activeSection) ?? "General") as Section;

  return (
    <Dialog name="settings" title="Settings" className="settings-dialog">
      {/* ADR-004 stage 4.4: persistent (visible regardless of which
          section tab is active, not a one-time toast) - a DPAPI failure
          means every API key/GitHub token this dialog saves from now on
          is written to disk in PLAINTEXT, a fact worth surfacing no
          matter which page a user happens to be on when it's true. Hidden
          entirely in the common case (secretsEncryptedAtRest is true).
          role="alert" (adversarial-review fix) matches every other warning/
          error banner in this codebase (BridgeErrorState.tsx, the 3
          node-view banner-error divs) - without it, the dialog's own focus
          trap moves focus straight to the rail buttons on open (this <p>
          isn't focusable), so screen-reader users could tab straight past
          this security-relevant text and never encounter it, and a later
          flip from encrypted to unencrypted while the dialog is already
          open would render with no assistive-tech announcement at all. */}
      {!state.secretsEncryptedAtRest && (
        <p className="settings-update-status settings-secrets-warning" data-level="warning" role="alert">
          API keys are stored unencrypted on this system (Windows DPAPI is unavailable or failing here).
        </p>
      )}
      <div className="settings-shell">
        <nav className="settings-rail" aria-label="Settings sections">
          {SECTIONS.map((section) => (
            <button
              key={section}
              type="button"
              className={"settings-rail-button" + (section === activeSection ? " active" : "")}
              aria-current={section === activeSection ? "page" : undefined}
              onClick={() => transport.fireIntent("app-settings", "setActiveSection", [sectionKey(section)], undefined, true)}
            >
              {section}
            </button>
          ))}
        </nav>
        <div className="settings-page" role="region" aria-label={activeSection}>
          {activeSection === "General" ? (
            <GeneralPage state={state} transport={transport} />
          ) : activeSection === "Model pricing" ? (
            <ModelPricingSettings state={state} transport={transport} />
          ) : activeSection === "Knowledge" ? (
            <KnowledgeSettingsPage
              key={JSON.stringify([
                state.knowledgeEmbeddingSource, state.knowledgeEmbeddingModel,
                state.knowledgeCloudProvider, state.knowledgeCloudOptIn,
              ])}
              state={state}
              transport={transport}
            />
          ) : activeSection === "Integrations" ? (
            <IntegrationsPage state={state} transport={transport} />
          ) : activeSection === "API Endpoint" ? (
            <CloudProviderSettings state={state} transport={transport} />
          ) : activeSection === "Fal.ai" ? (
            <FalAiPage state={state} transport={transport} />
          ) : activeSection === "Ollama (Local)" ? (
            <OllamaSettingsPage state={state} transport={transport} />
          ) : activeSection === "Llama.cpp (Local)" ? (
            <LlamaCppPage state={state} transport={transport} />
          ) : activeSection === "MCP Servers" ? (
            <McpServersPage state={state} transport={transport} />
          ) : activeSection === "Workspace Agent" ? (
            <WorkspaceAgentPage state={state} transport={transport} />
          ) : activeSection === "Plugins" ? (
            <PluginsPage grants={pluginsState.grants} transport={transport} />
          ) : activeSection === "Resource Limits" ? (
            <ResourceLimitsPage />
          ) : (
            activeSection
          )}
        </div>
      </div>
    </Dialog>
  );
}
