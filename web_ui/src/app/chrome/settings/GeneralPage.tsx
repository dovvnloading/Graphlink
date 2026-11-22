import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";
import { useOverlays } from "../../overlays/overlays";
import { CustomSelect } from "../CustomSelect";

// ADR-018 stage 18.4: mirrors graphlink_model_catalog.AUTO_POLICIES - the
// policy the auto rung of the resolution chain applies when neither an
// explicit task assignment nor a node/branch pin resolves a model.
const AUTO_MODEL_POLICY_OPTIONS = [
  { id: "cheapest-capable", label: "Cheapest Capable" },
  { id: "fastest", label: "Fastest" },
  { id: "best-quality", label: "Best Quality" },
] as const;

// ADR-012 stage 12.2: mirrors SettingsManager.THEMES (graphlink_settings_store.py).
const THEME_OPTIONS = [
  { id: "system", label: "Match System" },
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
] as const;


const NOTIFICATION_TYPE_LABELS: Record<string, string> = {
  info: "Info",
  success: "Success",
  warning: "Warning",
  error: "Error",
};

export function GeneralPage({
  state,
  transport,
}: {
  state: AppSettingsState;
  transport: WsTransport;
}) {
  // R8a (UI/UX issue list finding #9) removed the prior Theme select
  // (Dark/Muted/Monochromatic): it persisted a real setTheme intent, but
  // nothing in the SPA ever applied the value - no data-theme attribute, no
  // alternate token set, the app rendered identically no matter what was
  // selected. A persisted preference that provably does nothing is worse
  // than no preference at all, so it was cut rather than left inert.
  // ADR-012 stages 12.1 (a real light+dark token palette) and 12.2 (this
  // control, plus App.tsx's own applyTheme effect) resolve exactly that gap
  // - system/light/dark all genuinely repaint the app now.
  const overlays = useOverlays();
  return (
    <div className="settings-general-page">
      <div className="settings-field">
        <span className="settings-field-label">Theme</span>
        <CustomSelect
          ariaLabel="Theme"
          value={state.theme}
          options={THEME_OPTIONS.map((option) => ({ id: option.id, label: option.label }))}
          onChange={(id) => transport.fireIntent("app-settings", "setTheme", [id], undefined, true)}
        />
      </div>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.showTokenCounter}
          onChange={(event) => transport.fireIntent("app-settings", "setShowTokenCounter", [event.target.checked], undefined, true)}
        />
        Show Token Counter Overlay
      </label>

      <label className="settings-checkbox-row">
        <input
          type="checkbox"
          checked={state.enableSystemPrompt}
          onChange={(event) => transport.fireIntent("app-settings", "setEnableSystemPrompt", [event.target.checked], undefined, true)}
        />
        Enable Assistant System Prompt
      </label>

      <div className="settings-field">
        <span className="settings-field-label">Automatic Model Selection Policy</span>
        <CustomSelect
          ariaLabel="Automatic Model Selection Policy"
          value={state.autoModelPolicy}
          options={AUTO_MODEL_POLICY_OPTIONS.map((option) => ({ id: option.id, label: option.label }))}
          onChange={(id) => transport.fireIntent("app-settings", "setAutoModelPolicy", [id], undefined, true)}
        />
        <p className="settings-field-hint">
          Used when a task has no explicitly assigned model and no node/branch pin - picks the cheapest, fastest,
          or highest-quality capable model from your configured providers.
        </p>
      </div>

      <fieldset className="settings-fieldset">
        <legend>Notification types</legend>
        {Object.keys(NOTIFICATION_TYPE_LABELS).map((type) => (
          <label className="settings-checkbox-row" key={type}>
            <input
              type="checkbox"
              checked={state.notificationPreferences[type] ?? true}
              onChange={(event) =>
                transport.fireIntent("app-settings", "setNotificationPreference", [type, event.target.checked], undefined, true)
              }
            />
            {NOTIFICATION_TYPE_LABELS[type]}
          </label>
        ))}
      </fieldset>

      {/* ADR-012 stage 12.6: manual re-entry to the first-run onboarding
          wizard (OnboardingDialog.tsx) - it auto-opens exactly once (a
          fresh machine's hasCompletedOnboarding defaults false) and never
          shows itself again on its own, so this button is the only way
          back to it afterward - the "still reachable manually" entry point,
          matching how Help's own AppBar chip is a standing way back into
          that dialog. */}
      <div className="settings-field">
        <span className="settings-field-label">Onboarding</span>
        <div className="settings-button-row">
          <button type="button" className="settings-button" onClick={() => overlays.open("onboarding", "dialog")}>
            Show Onboarding
          </button>
        </div>
      </div>
    </div>
  );
}
