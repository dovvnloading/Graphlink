import type { AppSettingsState } from "../../lib/bridge-core/generated/app-settings-state";
import type { WsTransport } from "../../lib/ws/transport";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";

export function formatLocalModelBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return "Unknown";
  if (value === 0) return "0 B";
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), 4);
  return `${(value / (1024 ** unit)).toFixed(1)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}

export const REASONING_LEVEL_OPTIONS = [
  { value: "off", label: "Off" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
] as const;

export function ProviderModeSwitch({ mode, state, transport }: { mode: string; state: AppSettingsState; transport: WsTransport }) {
  const isActive = state.providerMode === mode;
  return (
    <Card {...{ title: "Provider mode" }} actions={!isActive ? (
      <Button variant="primary" size="sm"
        onClick={() => transport.fireIntent("app-settings", "setProviderMode", [mode])}>
        Use this provider
      </Button>
    ) : undefined}>
      <p className="settings-update-status">
        {isActive ? `${mode} is the active provider mode.`
          : `${mode} is configured here, but ${state.providerMode} is the active provider mode.`}
      </p>
    </Card>
  );
}
