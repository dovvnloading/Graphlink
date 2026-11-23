import type { WsTransport } from "../../../lib/ws/transport";
import type { AppPluginGrant } from "../../../lib/bridge-core/generated/app-plugins-state";
import { FieldGroup } from "../../ui/Field";
import { Switch } from "../../ui/Switch";
import { EmptyState } from "../../ui/EmptyState";

export function PluginsPage({ grants, transport }: { grants: AppPluginGrant[]; transport: WsTransport }) {
  return (
    <div className="settings-general-page">
      <h2 className="settings-page-title">Plugins</h2>
      <p className="settings-page-description">
        Grant third-party plugins permission to create nodes and run actions. Grants are not per-tool sandboxes.
      </p>
      <FieldGroup {...{ title: "Discovered plugins" }}>
        {grants.length === 0 ? (
          <EmptyState icon="puzzle" {...{ title: "No grants needed" }}
            body="No discovered plugins need a grant right now." />
        ) : grants.map((grant) => (
          <div className="settings-server-row" key={grant.pluginId}>
            <Switch label={grant.name}
              aria-label={`Granted: ${grant.name}`}
              description={grant.scopes.length ? grant.scopes.join(", ") : "No declared scopes"}
              checked={grant.granted}
              onCheckedChange={(checked) =>
                transport.fireIntent("app-plugins", "setPluginGrant", [grant.pluginId, checked], undefined, true)} />
          </div>
        ))}
      </FieldGroup>
    </div>
  );
}
