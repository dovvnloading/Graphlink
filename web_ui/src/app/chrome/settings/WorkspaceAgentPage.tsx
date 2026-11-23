import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";
import { FieldGroup } from "../../ui/Field";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";

export function WorkspaceAgentPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  return (
    <div className="settings-general-page">
      <h2 className="settings-page-title">Workspace agent</h2>
      <p className="settings-page-description">
        These folders were granted through the folder picker. Revoking access prevents new agent runs there;
        an existing run retains its workspace until it ends.
      </p>
      <FieldGroup title="Trusted folders">
        {state.harnessTrustedDirs.length === 0 ? (
          <EmptyState icon="folder" title="No trusted folders"
            body="Choose a workspace folder on an agent to grant access." />
        ) : state.harnessTrustedDirs.map((path) => (
          <div className="settings-server-row" key={path}>
            <span className="settings-trusted-folder-path">{path}</span>
            <Button size="sm" destructive aria-label={`Revoke access to ${path}`}
              onClick={() => transport.fireIntent("app-settings", "revokeHarnessTrustedDir", [path], undefined, true)}>
              Revoke access
            </Button>
          </div>
        ))}
      </FieldGroup>
    </div>
  );
}
