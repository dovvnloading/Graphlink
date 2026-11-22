import { useState } from "react";
import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";

export function IntegrationsPage({
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
            transport.fireIntent("app-settings", "clearGithubToken", []);
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
            transport.fireIntent("app-settings", "setGithubToken", [draftToken]);
            setDraftToken("");
          }}
        >
          Save Integrations
        </button>
      </div>
    </div>
  );
}
