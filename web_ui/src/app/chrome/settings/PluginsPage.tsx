import type { WsTransport } from "../../../lib/ws/transport";
import type { AppPluginGrant } from "../../../lib/bridge-core/generated/app-plugins-state";

// ADR-014 stage 14.4: install-time consent for discovered THIRD-PARTY
// plugins, mirroring McpServersPage's own minimalism immediately above -
// one row per entry, a single checkbox, no per-scope editing UI (MCP
// Servers itself has never had one for its own `scopes` field, so this
// stays exactly that coarse). Built-in plugins (the 8 migrated in stage
// 14.3) never appear in `grants` - backend/plugins.py only emits an entry
// per distinct plugin_id reached through the generic, non-builtin_actions
// discovery path - so this page can never gate anything that ships with
// the app itself.
//
// Honesty note (carried over from the ADR-014 stage 14.4 design): checking
// "Granted" here answers "did the user consent to this plugin acting at
// all," not "is this specific mutation confined to some enforced boundary."
// A plugin's declared scopes are a self-reported checklist the backend
// records for its own self-documentation, not a sandboxed capability set -
// real capability sandboxing is ADR-014 stage 14.5's job (out-of-process
// execution). This page does not claim otherwise.
export function PluginsPage({ grants, transport }: { grants: AppPluginGrant[]; transport: WsTransport }) {
  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        Grant a plugin to create its nodes and run its actions. Each scientific workbench pack has its own grant.
      </p>

      <fieldset className="settings-fieldset">
        <legend>Discovered Plugins</legend>
        {grants.length === 0 && (
          <p className="settings-update-status">No discovered plugins need a grant right now.</p>
        )}
        {grants.map((grant) => (
          <div className="settings-mcp-server-row" key={grant.pluginId}>
            <label className="settings-checkbox-row settings-mcp-server-toggle">
              <input
                type="checkbox"
                aria-label={`Granted: ${grant.name}`}
                checked={grant.granted}
                onChange={(event) =>
                  transport.fireIntent(
                    "app-plugins",
                    "setPluginGrant",
                    [grant.pluginId, event.target.checked],
                    undefined,
                    true,
                  )
                }
              />
              <span className="settings-mcp-server-info">
                <span className="settings-mcp-server-name">{grant.name}</span>
                <span className="settings-mcp-server-command">
                  {grant.scopes.length > 0 ? grant.scopes.join(", ") : "No declared scopes"}
                </span>
              </span>
            </label>
          </div>
        ))}
      </fieldset>
    </div>
  );
}
