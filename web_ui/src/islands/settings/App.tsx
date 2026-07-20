import { RefObject, useEffect, useRef, useState } from "react";
import { NOTIFICATION_TYPES, SECTION_NAMES, SettingsState, initialSettingsState } from "./bridgeTypes";
import { BridgeRejection, SettingsBridge, createSettingsBridge } from "./bridge";
import { BridgeErrorState } from "../../lib/ui/BridgeErrorState";

const THEME_OPTIONS = [
  { value: "dark", label: "Dark" },
  { value: "muted", label: "Muted" },
  { value: "mono", label: "Monochromatic" },
];

const NOTIFICATION_TYPE_LABELS: Record<(typeof NOTIFICATION_TYPES)[number], string> = {
  info: "Info",
  success: "Success",
  warning: "Warning",
  error: "Error",
};

interface GeneralPageProps {
  state: SettingsState;
  bridgeRef: RefObject<SettingsBridge | null>;
}

// Check for Updates / Open Repository are deliberately not here yet - both
// need a real window reference (increment 8's job), see
// graphlink_settings_bridge.py's module docstring. bridgeRef is passed as
// the ref object itself (not bridgeRef.current) - every intent call below
// happens inside an event handler, never during render, so reading
// .current there is the sanctioned pattern (react-hooks/refs only flags
// dereferencing a ref while rendering).
function GeneralPage({ state, bridgeRef }: GeneralPageProps) {
  return (
    <div className="settings-general-page">
      <label className="settings-field">
        <span className="settings-field-label">Theme</span>
        <select
          className="settings-select"
          value={state.theme}
          onChange={(event) => bridgeRef.current?.setTheme(event.target.value)}
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
          onChange={(event) => bridgeRef.current?.setShowTokenCounter(event.target.checked)}
        />
        Show Token Counter Overlay
      </label>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.enableSystemPrompt}
          onChange={(event) => bridgeRef.current?.setEnableSystemPrompt(event.target.checked)}
        />
        Enable Assistant System Prompt
      </label>

      <fieldset className="settings-fieldset">
        <legend>Notification types</legend>
        {NOTIFICATION_TYPES.map((type) => (
          <label className="settings-checkbox-row" key={type}>
            <input
              type="checkbox"
              checked={state.notificationPreferences[type] ?? true}
              onChange={(event) => bridgeRef.current?.setNotificationPreference(type, event.target.checked)}
            />
            {NOTIFICATION_TYPE_LABELS[type]}
          </label>
        ))}
      </fieldset>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.updateNotificationsEnabled}
          onChange={(event) => bridgeRef.current?.setUpdateNotificationsEnabled(event.target.checked)}
        />
        Enable Update Notifications on Startup
      </label>

      <p className="settings-update-status" data-level={state.updateStatusLevel}>
        {state.updateStatusMessage}
        {state.updateLastCheckedAt && ` (last checked: ${state.updateLastCheckedAt})`}
      </p>
    </div>
  );
}

interface IntegrationsPageProps {
  state: SettingsState;
  bridgeRef: RefObject<SettingsBridge | null>;
}

// Write-only, by design (see graphlink_settings_bridge.py's module
// docstring): the token input always starts empty and is never pre-filled
// from state, since the bridge structurally cannot send the current value
// back. draftToken is local-only UI state for what the user is currently
// typing, cleared immediately after Save/Clear - it never becomes part of
// the published SettingsState.
function IntegrationsPage({ state, bridgeRef }: IntegrationsPageProps) {
  const [draftToken, setDraftToken] = useState("");

  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        Store optional external-service tokens used by specialized plugins. The Code Review plugin uses a
        GitHub personal access token to load your private repositories - if you leave this empty, it still
        works with public repositories.
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
            bridgeRef.current?.clearGithubToken();
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
            bridgeRef.current?.setGithubToken(draftToken);
            setDraftToken("");
          }}
        >
          Save Integrations
        </button>
      </div>
    </div>
  );
}

function App() {
  const [state, setState] = useState<SettingsState>(initialSettingsState);
  const [rejection, setRejection] = useState<BridgeRejection | null>(null);
  const bridgeRef = useRef<SettingsBridge | null>(null);

  useEffect(() => {
    const bridge = createSettingsBridge(setState, setRejection);
    bridgeRef.current = bridge;
    bridge.ready();
    return () => {
      bridge.dispose();
      bridgeRef.current = null;
    };
  }, []);

  if (rejection) {
    return (
      <BridgeErrorState
        title="Settings unavailable"
        rejection={rejection}
        className="settings-shell bridge-error"
      />
    );
  }

  return (
    <div className="settings-shell">
      <nav className="settings-rail" aria-label="Settings sections">
        {SECTION_NAMES.map((section) => (
          <button
            key={section}
            type="button"
            className="settings-rail-button"
            aria-current={section === state.activeSection ? "page" : undefined}
            onClick={() => bridgeRef.current?.setActiveSection(section)}
          >
            {section}
          </button>
        ))}
      </nav>
      <div className="settings-page" role="region" aria-label={state.activeSection}>
        {state.activeSection === "General" ? (
          <GeneralPage state={state} bridgeRef={bridgeRef} />
        ) : state.activeSection === "Integrations" ? (
          <IntegrationsPage state={state} bridgeRef={bridgeRef} />
        ) : (
          state.activeSection
        )}
      </div>
    </div>
  );
}

export default App;
