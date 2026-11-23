import { useEffect, useState, type KeyboardEvent } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppPluginsState } from "../../lib/bridge-core/generated/app-plugins-state";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import { Dialog, useOverlays } from "../overlays/overlays";
import { InlineAlert } from "../ui/InlineAlert";
import { Icon } from "../ui/icons/Icon";
import "./settings/settings.css";
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

const SECTION_GROUPS = [
  { label: "", items: [
    { section: "General", label: "General", icon: "settings" },
  ] },
  { label: "Models", items: [
    { section: "Ollama (Local)", label: "Ollama", icon: "cpu" },
    { section: "Llama.cpp (Local)", label: "Llama.cpp", icon: "cpu" },
    { section: "API Endpoint", label: "API endpoint", icon: "cloud" },
    { section: "Fal.ai", label: "Fal.ai", icon: "sparkle" },
    { section: "Model pricing", label: "Model pricing", icon: "coins" },
  ] },
  { label: "Knowledge", items: [
    { section: "Knowledge", label: "Knowledge", icon: "database" },
  ] },
  { label: "Agent & tools", items: [
    { section: "Workspace Agent", label: "Workspace agent", icon: "agent" },
    { section: "MCP Servers", label: "MCP servers", icon: "hub" },
    { section: "Plugins", label: "Plugins", icon: "puzzle" },
    { section: "Integrations", label: "Integrations", icon: "plug" },
  ] },
  { label: "System", items: [
    { section: "Resource Limits", label: "Resource limits", icon: "shield" },
  ] },
] as const;
const SECTIONS = SECTION_GROUPS.flatMap((group) => group.items.map((item) => item.section));
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
  const [localSection, setLocalSection] = useState<Section | null>(null);
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

  // A section change is immediate; the persisted choice is acknowledged later
  // by app-settings. A stale snapshot must not visibly bounce the rail back.
  const persistedSection = SECTIONS.find((section) => sectionKey(section) === state.activeSection) ?? "General";
  const activeSection = localSection && sectionKey(localSection) !== state.activeSection
    ? localSection : persistedSection;
  const switchSection = (section: Section) => {
    setLocalSection(section);
    transport.fireIntent("app-settings", "setActiveSection", [sectionKey(section)], undefined, true);
  };
  const onRailKeyDown = (event: KeyboardEvent<HTMLButtonElement>, section: Section) => {
    const index = SECTIONS.indexOf(section);
    const next = event.key === "ArrowDown" || event.key === "ArrowRight" ? (index + 1) % SECTIONS.length
      : event.key === "ArrowUp" || event.key === "ArrowLeft" ? (index - 1 + SECTIONS.length) % SECTIONS.length
      : event.key === "Home" ? 0 : event.key === "End" ? SECTIONS.length - 1 : -1;
    if (next < 0) return;
    event.preventDefault();
    const target = SECTIONS[next];
    switchSection(target);
    event.currentTarget.closest("nav")?.querySelectorAll<HTMLButtonElement>(".settings-rail-button")[next]?.focus();
  };

  return (
    <Dialog name="settings" title="Settings" className="settings-dialog">
      {!state.secretsEncryptedAtRest && (
        <div className="settings-secrets-warning" role="alert">
          <InlineAlert variant="warning" {...{ title: "Secrets are not encrypted" }}
            body="API keys are stored unencrypted on this system (Windows DPAPI is unavailable or failing here)." />
        </div>
      )}
      <div className="settings-shell">
        <nav className="settings-rail" aria-label="Settings sections">
          {SECTION_GROUPS.map((group) => (
            <div className="settings-rail-group" key={group.label || "general"}>
              {group.label && <span className="settings-rail-overline">{group.label}</span>}
              {group.items.map(({ section, label, icon }) => (
                <button key={section} type="button"
                  className={"settings-rail-button" + (section === activeSection ? " active" : "")}
                  aria-label={section}
                  aria-current={section === activeSection ? "page" : undefined}
                  tabIndex={section === activeSection ? 0 : -1}
                  onClick={() => switchSection(section)}
                  onKeyDown={(event) => onRailKeyDown(event, section)}>
                  <Icon name={icon} size="md" />
                  <span>{label}</span>
                </button>
              ))}
            </div>
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
