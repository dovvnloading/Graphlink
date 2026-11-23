import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";
import { useOverlays } from "../../overlays/overlays";
import { FieldGroup, FormRow } from "../../ui/Field";
import { SegmentedControl } from "../../ui/SegmentedControl";
import { Select } from "../../ui/Select";
import { Switch } from "../../ui/Switch";
import { Button } from "../../ui/Button";

const POLICY_OPTIONS = [
  { value: "cheapest-capable", label: "Cheapest capable", description: "Pick the lowest-priced model that can complete the task." },
  { value: "fastest", label: "Fastest", description: "Prefer the quickest capable model." },
  { value: "best-quality", label: "Best quality", description: "Prefer the highest-quality capable model." },
] as const;

const THEME_OPTIONS = [
  { value: "system", label: "Match system" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
] as const;

const NOTIFICATIONS = [
  { type: "info", label: "Information" },
  { type: "success", label: "Success" },
  { type: "warning", label: "Warnings" },
  { type: "error", label: "Errors" },
] as const;

export function GeneralPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  const overlays = useOverlays();
  return (
    <div className="settings-general-page">
      <h2 className="settings-page-title">General</h2>
      <p className="settings-page-description">Choose how Graphlink looks, selects models, and notifies you.</p>
      <FieldGroup title="Appearance">
        <FormRow label="Theme" layout="inline">
          <SegmentedControl label="Theme" options={THEME_OPTIONS} value={state.theme}
            onChange={(value) => transport.fireIntent("app-settings", "setTheme", [value], undefined, true)} />
        </FormRow>
        <Switch label="Show token counter" checked={state.showTokenCounter}
          onCheckedChange={(checked) => transport.fireIntent("app-settings", "setShowTokenCounter", [checked], undefined, true)} />
        <Switch label="Use the assistant system prompt" checked={state.enableSystemPrompt}
          onCheckedChange={(checked) => transport.fireIntent("app-settings", "setEnableSystemPrompt", [checked], undefined, true)} />
      </FieldGroup>
      <FieldGroup title="Model selection">
        <FormRow label="Model selection policy" layout="inline"
          description="Used when a task has no assigned model or pin.">
          <Select ariaLabel="Model selection policy" value={state.autoModelPolicy} options={POLICY_OPTIONS}
            onChange={(value) => transport.fireIntent("app-settings", "setAutoModelPolicy", [value], undefined, true)} />
        </FormRow>
      </FieldGroup>
      <FieldGroup title="Notification types" description="Choose which notifications appear in Graphlink.">
        {NOTIFICATIONS.map(({ type, label }) => (
          <Switch key={type} label={label} checked={state.notificationPreferences[type] ?? true}
            onCheckedChange={(checked) =>
              transport.fireIntent("app-settings", "setNotificationPreference", [type, checked], undefined, true)} />
        ))}
      </FieldGroup>
      <FieldGroup title="Onboarding" description="Replay the first-run introduction at any time.">
        <div className="settings-button-row">
          <Button variant="secondary" onClick={() => overlays.open("onboarding", "dialog")}>Show onboarding</Button>
        </div>
      </FieldGroup>
    </div>
  );
}
