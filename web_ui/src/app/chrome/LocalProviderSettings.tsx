import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";

// Both local providers share the same reasoning levels and provider switch.
export function formatLocalModelBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return "Unknown";
  if (value === 0) return "0 B";
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4);
  return `${(value / (1024 ** unit)).toFixed(1)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}

export const REASONING_LEVEL_OPTIONS = [
  { id: "off", label: "Off" },
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
] as const;

// A local mode switch starts a provider, so it cannot be replayed offline.
export function ProviderModeSwitch({ mode, state, transport }: { mode: string; state: AppSettingsState; transport: WsTransport }) {
  const isActive = state.providerMode === mode;
  return (
    <div className="settings-field">
      <p className="settings-update-status" data-level={isActive ? undefined : "warning"}>
        {isActive
          ? `${mode} is the active provider mode.`
          : `${mode} is configured here, but ${state.providerMode} is the active provider mode.`}
      </p>
      {!isActive && (
        <div className="settings-button-row">
          <button
            type="button"
            className="settings-button settings-button-primary"
            onClick={() => transport.fireIntent("app-settings", "setProviderMode", [mode])}
          >
            Use This Provider
          </button>
        </div>
      )}
    </div>
  );
}
