import { useState } from "react";
import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";
import { FieldGroup } from "../../ui/Field";
import { TextField } from "../../ui/TextField";
import { Button } from "../../ui/Button";
import { InlineAlert } from "../../ui/InlineAlert";

export function IntegrationsPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  const [draftToken, setDraftToken] = useState("");
  return (
    <div className="settings-general-page">
      <h2 className="settings-page-title">Integrations</h2>
      <p className="settings-page-description">
        Configure optional external services. Gitlink can load public GitHub repositories without a token.
      </p>
      <FieldGroup title="GitHub">
        <TextField label="GitHub personal access token" type="password" suffix={{ type: "password" }}
          autoComplete="new-password" mono placeholder="Paste a token"
          value={draftToken} onValueChange={setDraftToken} />
        <p className="settings-update-status">
          {state.githubTokenConfigured ? "A GitHub token is configured." : "No GitHub token configured."}
        </p>
        <div className="settings-button-row">
          <Button onClick={() => {
            transport.fireIntent("app-settings", "clearGithubToken", []);
            setDraftToken("");
          }}>Clear token</Button>
          <Button variant="primary" disabled={!draftToken.trim()} onClick={() => {
            transport.fireIntent("app-settings", "setGithubToken", [draftToken]);
            setDraftToken("");
          }}>Save integration</Button>
        </div>
      </FieldGroup>
      {!state.secretsEncryptedAtRest && <InlineAlert variant="warning" title="Secrets are not encrypted"
        body="GitHub tokens saved here cannot be encrypted at rest on this system." />}
    </div>
  );
}
