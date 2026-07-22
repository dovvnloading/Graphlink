import { useEffect, useState } from "react";
import type { WsTransport } from "../../lib/ws/transport";
import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import { Dialog } from "../overlays/overlays";

/**
 * The settings dialog (Qt-removal plan R2.5d) - settings island's SPA
 * successor, General + Integrations pages only. Ollama/Llama.cpp/API
 * Endpoint need a graphlink_config.py Qt/non-Qt split and a native
 * file-picker capability neither of which exist yet (backend/settings.py's
 * module docstring) - rendered here as disabled placeholders with an
 * explicit R4 label rather than faking them, the same explicit-defer
 * discipline as the app bar's disabled Save/provider-select.
 */

const SECTIONS = ["General", "Ollama (Local)", "Llama.cpp (Local)", "API Endpoint", "Integrations"] as const;
type Section = (typeof SECTIONS)[number];

const DEFERRED_SECTIONS = new Set<Section>(["Ollama (Local)", "Llama.cpp (Local)", "API Endpoint"]);

const THEME_OPTIONS = [
  { value: "dark", label: "Dark" },
  { value: "muted", label: "Muted" },
  { value: "mono", label: "Monochromatic" },
];

const NOTIFICATION_TYPE_LABELS: Record<string, string> = {
  info: "Info",
  success: "Success",
  warning: "Warning",
  error: "Error",
};

const initialState: AppSettingsState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  activeSection: "General",
  theme: "dark",
  showTokenCounter: true,
  enableSystemPrompt: true,
  notificationPreferences: {},
  githubTokenConfigured: false,
};

function sectionKey(section: Section): string {
  // Backend storage is UI-navigation-only for this field (mirrors the
  // legacy bridge - see backend/settings.py's active_section cell), so any
  // stable string works; reusing the display name keeps the wire traffic
  // human-readable in the WS inspector.
  return section === "General" ? "general" : section === "Integrations" ? "integrations" : section.toLowerCase();
}

function GeneralPage({
  state,
  transport,
}: {
  state: AppSettingsState;
  transport: WsTransport;
}) {
  return (
    <div className="settings-general-page">
      <label className="settings-field">
        <span className="settings-field-label">Theme</span>
        <select
          className="settings-select"
          value={state.theme}
          onChange={(event) => transport.intent("app-settings", "setTheme", [event.target.value])}
        >
          {THEME_OPTIONS.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.showTokenCounter}
          onChange={(event) => transport.intent("app-settings", "setShowTokenCounter", [event.target.checked])}
        />
        Show Token Counter Overlay
      </label>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.enableSystemPrompt}
          onChange={(event) => transport.intent("app-settings", "setEnableSystemPrompt", [event.target.checked])}
        />
        Enable Assistant System Prompt
      </label>

      <fieldset className="settings-fieldset">
        <legend>Notification types</legend>
        {Object.keys(NOTIFICATION_TYPE_LABELS).map((type) => (
          <label className="settings-checkbox-row" key={type}>
            <input
              type="checkbox"
              checked={state.notificationPreferences[type] ?? true}
              onChange={(event) =>
                transport.intent("app-settings", "setNotificationPreference", [type, event.target.checked])
              }
            />
            {NOTIFICATION_TYPE_LABELS[type]}
          </label>
        ))}
      </fieldset>
    </div>
  );
}

function IntegrationsPage({
  state,
  transport,
}: {
  state: AppSettingsState;
  transport: WsTransport;
}) {
  const [draftToken, setDraftToken] = useState("");

  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        Store optional external-service tokens used by specialized plugins. The Gitlink plugin uses a GitHub
        personal access token to load your private repositories - if you leave this empty, it still works with
        public repositories.
      </p>

      <label className="settings-field">
        <span className="settings-field-label">GitHub Personal Access Token</span>
        <input
          type="password"
          className="settings-select"
          placeholder="ghp_... or fine-grained token"
          value={draftToken}
          onChange={(event) => setDraftToken(event.target.value)}
        />
      </label>

      <p className="settings-update-status">
        {state.githubTokenConfigured ? "A GitHub token is currently configured." : "No GitHub token configured."}
      </p>

      <div className="settings-button-row">
        <button
          type="button"
          className="settings-button"
          onClick={() => {
            transport.intent("app-settings", "clearGithubToken", []);
            setDraftToken("");
          }}
        >
          Clear Token
        </button>
        <button
          type="button"
          className="settings-button settings-button-primary"
          disabled={draftToken.trim().length === 0}
          onClick={() => {
            transport.intent("app-settings", "setGithubToken", [draftToken]);
            setDraftToken("");
          }}
        >
          Save Integrations
        </button>
      </div>
    </div>
  );
}

function DeferredPage({ section }: { section: Section }) {
  return (
    <div className="settings-deferred-page">
      <p>{section} configuration lands in R4.</p>
      <p className="settings-deferred-detail">
        Local/API model providers need a Qt-free config split and a native file picker that don't exist yet.
      </p>
    </div>
  );
}

export function SettingsDialog({ transport }: { transport: WsTransport }) {
  const [state, setState] = useState<AppSettingsState>(initialState);

  useEffect(() => {
    return transport.subscribe("app-settings", (payload) => {
      const validated = TOPIC_VALIDATORS["app-settings"](payload);
      if (validated.ok) setState(validated.value as AppSettingsState);
      else console.error("[app-settings] rejected snapshot:", validated.errors);
    });
  }, [transport]);

  const activeSection = (SECTIONS.find((s) => sectionKey(s) === state.activeSection) ?? "General") as Section;

  return (
    <Dialog name="settings" title="Settings" className="settings-dialog">
      <div className="settings-shell">
        <nav className="settings-rail" aria-label="Settings sections">
          {SECTIONS.map((section) => (
            <button
              key={section}
              type="button"
              className={"settings-rail-button" + (section === activeSection ? " active" : "")}
              aria-current={section === activeSection ? "page" : undefined}
              onClick={() => transport.intent("app-settings", "setActiveSection", [sectionKey(section)])}
            >
              {section}
            </button>
          ))}
        </nav>
        <div className="settings-page" role="region" aria-label={activeSection}>
          {activeSection === "General" ? (
            <GeneralPage state={state} transport={transport} />
          ) : activeSection === "Integrations" ? (
            <IntegrationsPage state={state} transport={transport} />
          ) : DEFERRED_SECTIONS.has(activeSection) ? (
            <DeferredPage section={activeSection} />
          ) : (
            activeSection
          )}
        </div>
      </div>
    </Dialog>
  );
}
