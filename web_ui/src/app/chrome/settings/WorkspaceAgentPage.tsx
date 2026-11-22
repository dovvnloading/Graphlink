import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";

export function WorkspaceAgentPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        These folders were granted to the Workspace Agent when you selected them through the folder picker.
        Revoking a folder prevents new runs from using it. An agent already running there retains its workspace
        until that run ends.
      </p>
      <fieldset className="settings-fieldset">
        <legend>Trusted Folders</legend>
        {state.harnessTrustedDirs.length === 0 && (
          <p className="settings-update-status">No trusted folders yet. Choose a workspace folder on an agent to grant access.</p>
        )}
        {state.harnessTrustedDirs.map((path) => (
          <div className="settings-mcp-server-row" key={path}>
            <span className="settings-trusted-folder-path" title={path}>{path}</span>
            <button
              type="button"
              className="settings-button settings-mcp-server-remove"
              aria-label={`Revoke access to ${path}`}
              onClick={() => transport.fireIntent("app-settings", "revokeHarnessTrustedDir", [path], undefined, true)}
            >
              Revoke
            </button>
          </div>
        ))}
      </fieldset>
    </div>
  );
}
