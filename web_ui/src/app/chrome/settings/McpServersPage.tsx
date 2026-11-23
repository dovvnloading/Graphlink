import { useState } from "react";
import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";
import { FieldGroup } from "../../ui/Field";
import { TextField } from "../../ui/TextField";
import { TextArea } from "../../ui/TextArea";
import { Switch } from "../../ui/Switch";
import { Button } from "../../ui/Button";
import { StatusPill } from "../../ui/Badge";
import { MenuButton, MenuItem } from "../../ui/Menu";
import { useConfirm } from "../../ui/ConfirmDialog";
import { EmptyState } from "../../ui/EmptyState";

/** Parse KEY=value lines without destroying embedded "=" characters. */
export function parseEnvLines(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (key) env[key] = line.slice(eq + 1).trim();
  }
  return env;
}

const APPROVAL_LABELS: Record<string, string> = {
  auto: "Auto (no confirmation)",
  once: "Confirm once",
  always: "Always confirm",
};

// Outgoing settings may include newly entered secret values. Published rows
// contain only envKeys; omitting env preserves existing saved values by ID.
type OutgoingServer = {
  id?: string;
  name: string;
  command: string;
  args: string[];
  scopes: string[];
  approval: string;
  enabledTools: string[];
  enabled: boolean;
  timeout: number;
  envKeys: string[];
  env?: Record<string, string>;
};

export function McpServersPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  const askConfirm = useConfirm();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftCommand, setDraftCommand] = useState("");
  const [draftArgs, setDraftArgs] = useState("");
  const [draftEnv, setDraftEnv] = useState("");
  const [notice, setNotice] = useState("");

  const updateServers = (next: OutgoingServer[]) => {
    transport.fireIntent("app-settings", "setMcpServers", [next], undefined, true);
  };
  const reset = () => {
    setEditingId(null);
    setDraftName("");
    setDraftCommand("");
    setDraftArgs("");
    setDraftEnv("");
  };
  const beginEdit = (server: AppSettingsState["mcpServers"][number]) => {
    setEditingId(server.id);
    setDraftName(server.name);
    setDraftCommand(server.command);
    setDraftArgs(server.args.join(" "));
    setDraftEnv("");
    setNotice("Environment secrets are write-only. Leave this field empty to keep them unchanged.");
  };
  const save = () => {
    const name = draftName.trim();
    const command = draftCommand.trim();
    if (!name || !command) return;
    const args = draftArgs.trim() ? draftArgs.trim().split(/\s+/) : [];
    const env = parseEnvLines(draftEnv);
    if (editingId !== null) {
      updateServers(state.mcpServers.map((server) => server.id !== editingId ? server : ({
        ...server, name, command, args,
        ...(Object.keys(env).length ? { env, envKeys: Object.keys(env).sort() } : {}),
      })));
      setNotice("Server configuration saved. Changes apply to the next connection.");
    } else {
      updateServers([...state.mcpServers, {
        name, command, args, scopes: [], approval: "always", enabledTools: [],
        enabled: true, timeout: 30, envKeys: Object.keys(env).sort(), env,
      }]);
      setNotice("Server added. Tools become available when the Builder reconnects.");
    }
    reset();
  };
  const requestRemove = async (id: string, name: string) => {
    if (!await askConfirm({ title: "Remove MCP server?", body: `Remove ${name} from your configured servers? Saved environment values for this entry will also be deleted.`,
      confirmLabel: "Remove server", destructive: true })) return;
    updateServers(state.mcpServers.filter((server) => server.id !== id));
    if (editingId === id) reset();
    setNotice(`${name} removed.`);
  };
  const restart = () => {
    // MCP tools connect lazily when the Builder registry is next built.
    // Reapply the persisted list rather than inventing a non-existent runtime
    // restart intent or implying that a long-running daemon was restarted.
    updateServers([...state.mcpServers]);
    setNotice("Configuration reapplied. MCP servers connect when Builder next loads their tools.");
  };

  return (
    <div className="settings-general-page">
      <h2 className="settings-page-title">MCP servers</h2>
      <p className="settings-page-description">
        Configure local Model Context Protocol servers. Commands can execute third-party code;
        enabled servers connect on demand, and their tools are approval-gated.
      </p>
      <FieldGroup {...{ title: "Configured servers" }}>
        {state.mcpServers.length === 0 ? (
          <EmptyState icon="hub" {...{ title: "No MCP servers" }} body="Add a server to make its tools available to Builder." />
        ) : state.mcpServers.map((server, index) => (
          <div className="settings-server-row" key={server.id}>
            <div className="settings-server-info">
              <Switch label={server.name || "MCP server"} description={server.command + (server.args.length ? " " + server.args.join(" ") : "")}
                checked={server.enabled} onCheckedChange={(enabled) =>
                  updateServers(state.mcpServers.map((entry, i) => i === index ? { ...entry, enabled } : entry))} />
              <span className="settings-server-meta">
                Approval: <span className={server.approval === "auto" ? "settings-mcp-server-approval-auto" : undefined}>
                  {APPROVAL_LABELS[server.approval] ?? server.approval}
                </span>
                {" · "}Scopes: {server.scopes.length ? server.scopes.join(", ") : "No declared scopes"}
              </span>
              {server.envKeys.length > 0 && <span className="settings-server-meta">
                Environment keys: {[...server.envKeys].sort().join(", ")}
              </span>}
            </div>
            <StatusPill status={server.enabled ? "Done" : "Skipped"} />
            <MenuButton label={`Actions for ${server.name}`} menuLabel={`Actions for ${server.name}`}>
              <MenuItem label="Edit" icon="edit" onSelect={() => beginEdit(server)} />
              <MenuItem label="Restart" icon="refresh" onSelect={restart}
                description="Reapply settings for the next connection" />
              <MenuItem label="Remove" destructive onSelect={() => { void requestRemove(server.id, server.name); }} />
            </MenuButton>
          </div>
        ))}
      </FieldGroup>
      <FieldGroup {...{ title: editingId ? "Edit server" : "Add server" }}
        description="Only the environment variable names are displayed after saving. Leave the secret field blank when editing to keep saved values.">
        <TextField label="Server name" value={draftName} onValueChange={setDraftName}
          placeholder="e.g. filesystem" />
        <TextField label="Command" value={draftCommand} onValueChange={setDraftCommand}
          placeholder="e.g. npx" mono />
        <TextField label="Arguments (optional, space-separated)" value={draftArgs} onValueChange={setDraftArgs}
          placeholder="-y @modelcontextprotocol/server-filesystem /path/to/allow" mono />
        <TextArea label="Environment variables (one KEY=value per line)" value={draftEnv}
          onValueChange={setDraftEnv} minRows={3} maxRows={6} spellCheck={false}
          placeholder={"GITHUB_TOKEN=…\nBRAVE_API_KEY=…"} />
        <p className="settings-field-hint">Servers receive only these variables and basic system plumbing, never Graphlink provider keys.</p>
        <div className="settings-button-row">
          {editingId && <Button onClick={reset}>Cancel edit</Button>}
          <Button variant="primary" disabled={!draftName.trim() || !draftCommand.trim()} onClick={save}>
            {editingId ? "Save server" : "Add server"}
          </Button>
        </div>
      </FieldGroup>
      {notice && <p className="settings-update-status" role="status">{notice}</p>}
    </div>
  );
}
