import { useState } from "react";
import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";
import { FieldGroup } from "../../ui/Field";
import { TextField } from "../../ui/TextField";
import { TextArea } from "../../ui/TextArea";
import { Switch } from "../../ui/Switch";
import { Button } from "../../ui/Button";
import { Checkbox } from "../../ui/Checkbox";
import { NumberField } from "../../ui/NumberField";
import { Select } from "../../ui/Select";
import { StatusPill } from "../../ui/Badge";
import { MenuButton, MenuItem } from "../../ui/Menu";
import { useConfirm } from "../../ui/ConfirmDialog";
import { EmptyState } from "../../ui/EmptyState";
import { InlineAlert } from "../../ui/InlineAlert";
import { Disclosure } from "../../ui/Disclosure";
import { formatCount } from "../../ui/format";
import { parseMcpCatalog, type McpTool } from "../../canvas/mcpConnect";
import "./McpServersPage.css";

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

/** An argv editor, never a shell command: quoted values and JSON preserve spaces. */
export function parseMcpArguments(text: string): string[] {
  const source = text.trim();
  if (!source) return [];
  if (source.startsWith("[")) {
    let values: unknown;
    try { values = JSON.parse(source); } catch { throw new Error("Arguments must be a valid JSON array of strings."); }
    if (!Array.isArray(values) || !values.every(value => typeof value === "string"))
      throw new Error("Arguments must be a JSON array containing only strings.");
    return values;
  }
  const values: string[] = [];
  let current = "";
  let quote = "";
  let active = false;
  for (let i = 0; i < source.length; i++) {
    const char = source[i];
    if (quote) {
      if (char === quote) quote = "";
      else if (char === "\\" && source[i + 1] === quote) current += source[++i];
      else current += char;
    } else if (char === '"' || char === "'") { quote = char; active = true; }
    else if (/\s/.test(char)) {
      if (active) { values.push(current); current = ""; active = false; }
    } else { current += char; active = true; }
  }
  if (quote) throw new Error("An argument has an unclosed quote. Close it or use a JSON array.");
  if (active) values.push(current);
  return values;
}

const APPROVAL_LABELS: Record<string, string> = {
  auto: "Auto (no confirmation)", once: "Confirm once", always: "Always confirm",
};

type OutgoingServer = {
  id?: string; name: string; command: string; args: string[]; scopes: string[];
  approval: string; enabledTools: string[]; enabled: boolean; timeout: number;
  envKeys: string[]; env?: Record<string, string>;
  transport?: string; url?: string; headerKeys?: string[]; headers?: Record<string, string>;
};

export function McpServersPage({ state, transport }: { state: Pick<AppSettingsState, "mcpServers">; transport: WsTransport }) {
  const askConfirm = useConfirm();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftTransport, setDraftTransport] = useState("stdio");
  const [draftCommand, setDraftCommand] = useState("");
  const [draftArgs, setDraftArgs] = useState("");
  const [draftEnv, setDraftEnv] = useState("");
  const [draftUrl, setDraftUrl] = useState("");
  const [draftBearer, setDraftBearer] = useState("");
  const [draftHeaders, setDraftHeaders] = useState("");
  const [clearHeaders, setClearHeaders] = useState(false);
  const [clearEnv, setClearEnv] = useState(false);
  const [draftApproval, setDraftApproval] = useState("always");
  const [draftScopes, setDraftScopes] = useState("");
  const [draftTimeout, setDraftTimeout] = useState<number | "">(30);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [testing, setTesting] = useState("");
  const [discovered, setDiscovered] = useState<Record<string, McpTool[]>>({});
  const [toolFilter, setToolFilter] = useState("");

  const updateServers = (next: OutgoingServer[]) => {
    transport.fireIntent("app-settings", "setMcpServers", [next], undefined, true);
  };
  const reset = () => {
    setEditingId(null); setDraftName(""); setDraftTransport("stdio"); setDraftCommand(""); setDraftArgs("");
    setDraftEnv(""); setDraftUrl(""); setDraftBearer(""); setDraftHeaders(""); setClearHeaders(false);
    setClearEnv(false); setDraftApproval("always"); setDraftScopes(""); setDraftTimeout(30);
  };
  const beginEdit = (server: AppSettingsState["mcpServers"][number]) => {
    setEditingId(server.id); setDraftName(server.name); setDraftTransport(server.transport || "stdio");
    setDraftCommand(server.command); setDraftArgs(server.args.length ? JSON.stringify(server.args) : "");
    setDraftEnv(""); setDraftUrl(server.url || ""); setDraftBearer(""); setDraftHeaders(""); setClearHeaders(false);
    setClearEnv(false); setDraftApproval(server.approval); setDraftScopes(server.scopes.join(", "));
    setDraftTimeout(server.timeout); setError("");
    setNotice("Credentials are write-only. Leave their fields empty to keep saved values.");
  };
  const save = () => {
    const name = draftName.trim();
    const command = draftCommand.trim();
    const remote = draftTransport === "streamable_http";
    if (!name || (!remote && !command) || (remote && !draftUrl.trim())) return;
    try {
      const args = remote ? [] : parseMcpArguments(draftArgs);
      const env = parseEnvLines(draftEnv);
      const headers = parseEnvLines(draftHeaders);
      if (draftBearer.trim()) headers.Authorization = `Bearer ${draftBearer.trim()}`;
      const timeout = draftTimeout === "" ? 30 : draftTimeout;
      if (timeout < 1 || timeout > 300) throw new Error("Request timeout must be between 1 and 300 seconds.");
      if (remote) {
        let url: URL;
        try { url = new URL(draftUrl.trim()); } catch { throw new Error("Enter a complete MCP endpoint URL."); }
        if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.hash)
          throw new Error("Use an HTTP or HTTPS endpoint without embedded credentials or a fragment.");
      }
      const configuration = { name, command: remote ? "" : command, args,
        scopes: draftScopes.split(",").map(scope => scope.trim()).filter(Boolean), approval: draftApproval, timeout,
        // Preserve the legacy stdio add shape; the settings contract supplies defaults.
        ...(remote || editingId ? { transport: draftTransport, url: remote ? draftUrl.trim() : "" } : {}),
      };
      const envUpdate = clearEnv || Object.keys(env).length ? { env: clearEnv ? {} : env,
        envKeys: clearEnv ? [] : Object.keys(env).sort() } : {};
      const headerUpdate = clearHeaders || Object.keys(headers).length ? { headers: clearHeaders ? {} : headers,
        headerKeys: clearHeaders ? [] : Object.keys(headers).sort() } : {};
      if (editingId !== null) {
        updateServers(state.mcpServers.map(server => server.id !== editingId ? server : ({
          ...server, ...configuration, ...envUpdate, ...headerUpdate,
        })));
        setNotice("Server configuration saved. Changes apply to the next connection.");
      } else {
        updateServers([...state.mcpServers, { ...configuration, enabledTools: [], enabled: true,
          envKeys: Object.keys(env).sort(), env,
          ...(remote ? { headerKeys: Object.keys(headers).sort(), headers } : {}),
        }]);
        setNotice("Server added. Test the connection to discover its tools, then add MCP Connect to the canvas.");
      }
      reset(); setError("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The server configuration could not be saved. Check the fields and try again."); }
  };
  const requestRemove = async (id: string, name: string) => {
    if (!await askConfirm({ title: "Remove MCP server?", body: `Remove ${name} from your configured servers? Saved credentials for this entry will also be deleted.`,
      confirmLabel: "Remove server", destructive: true })) return;
    updateServers(state.mcpServers.filter(server => server.id !== id));
    if (editingId === id) reset();
    setDiscovered(current => { const next = { ...current }; delete next[id]; return next; });
    setNotice(`${name} removed.`);
  };
  const restart = () => {
    updateServers([...state.mcpServers]);
    setNotice("Configuration reapplied. MCP servers connect when their tools are next needed.");
  };
  async function testConnection(id: string) {
    if (testing) return;
    setTesting(id); setError(""); setNotice("");
    try {
      const response = parseMcpCatalog(await transport.request("scene", "listMcpTools", [id], 120_000));
      const failure = response.errors?.find(item => item.serverId === id);
      if (failure) throw new Error(failure.message);
      const tools = response.tools.filter(tool => tool.serverId === id);
      setDiscovered(current => ({ ...current, [id]: tools }));
      setNotice(`Connection successful. ${formatCount(tools.length)} tools discovered.`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The connection failed. Check the endpoint, credentials, or local command and test again."); }
    finally { setTesting(""); }
  }
  function setToolAllowed(id: string, name: string, allowed: boolean) {
    const server = state.mcpServers.find(item => item.id === id);
    if (!server) return;
    const enabled = new Set(server.enabledTools.length ? server.enabledTools : (discovered[id] ?? []).map(tool => tool.name));
    if (allowed) enabled.add(name); else enabled.delete(name);
    // The existing protocol uses [] for all tools. An empty selection must
    // disable the server rather than accidentally granting every tool.
    if (enabled.size === 0) {
      updateServers(state.mcpServers.map(item => item.id === id ? { ...item, enabled: false, enabledTools: [name] } : item));
      setNotice("Server disabled. Its last tool remains selected so re-enabling it cannot accidentally allow every tool. Review the allowlist before enabling it again.");
    } else updateServers(state.mcpServers.map(item => item.id === id ? { ...item, enabledTools: [...enabled].sort() } : item));
  }

  return <div className="settings-general-page">
    <h2 className="settings-page-title">MCP servers</h2>
    <p className="settings-page-description">Connect local commands or remote Model Context Protocol servers.
      Their tools are available to MCP Connect canvas nodes and Builder, using the permissions you set here.</p>
    <FieldGroup title="Configured servers">
      {state.mcpServers.length === 0 ? <EmptyState icon="hub" title="No MCP servers"
        body="Add a server, test its connection, and choose a tool in an MCP Connect node." />
        : state.mcpServers.map((server, index) => <div className="mcp-settings-server" key={server.id}>
          <div className="settings-server-row"><div className="settings-server-info">
            <Switch label={server.name || "MCP server"} description={server.transport === "streamable_http" ? server.url
              : server.command + (server.args.length ? " " + server.args.join(" ") : "")}
              checked={server.enabled} onCheckedChange={enabled => updateServers(state.mcpServers.map((entry, i) => i === index ? { ...entry, enabled } : entry))} />
            <span className="settings-server-meta"><span className={server.approval === "auto" ? "settings-mcp-server-approval-auto" : undefined}>
              Approval: {APPROVAL_LABELS[server.approval] ?? server.approval}</span>{" · "}
              <span>Scopes: {server.scopes.length ? server.scopes.join(", ") : "No declared scopes"}</span></span>
            {server.envKeys.length > 0 && <span className="settings-server-meta">Environment keys: {[...server.envKeys].sort().join(", ")}</span>}
            {(server.headerKeys ?? []).length > 0 && <span className="settings-server-meta">Saved authentication headers: {[...server.headerKeys].sort().join(", ")}</span>}
          </div>
          <StatusPill status={server.enabled ? "Done" : "Skipped"} />
          <MenuButton label={`Actions for ${server.name}`} menuLabel={`Actions for ${server.name}`}>
            <MenuItem label="Edit" icon="edit" onSelect={() => beginEdit(server)} />
            <MenuItem label="Restart" icon="refresh" onSelect={restart} description="Reapply settings for the next connection" />
            <MenuItem label="Remove" destructive onSelect={() => { void requestRemove(server.id, server.name); }} />
          </MenuButton></div>
          <Button size="sm" iconStart="plug" loading={testing === server.id} disabled={!!testing || !server.enabled}
            disabledReason={!server.enabled ? "Enable this server before testing its connection." : undefined}
            onClick={() => { void testConnection(server.id); }}>Test connection and discover tools</Button>
          {discovered[server.id] && <Disclosure title={`${server.name} tools (${formatCount(discovered[server.id].length)})`} defaultOpen>
            <p className="settings-field-hint">An empty allowlist allows every advertised tool. Restricting tools keeps newly added tools unavailable until you allow them.</p>
            <TextField label={`Search ${server.name} tools`} value={toolFilter} onValueChange={setToolFilter} type="search" prefixIcon="search" />
            <div className="mcp-settings-tools">{discovered[server.id].filter(tool => `${tool.name} ${tool.description}`.toLowerCase().includes(toolFilter.toLowerCase()))
              .map(tool => <Checkbox key={tool.name} label={tool.title || tool.name} description={tool.description}
                checked={!server.enabledTools.length || server.enabledTools.includes(tool.name)} disabled={!server.enabled}
                onChange={event => setToolAllowed(server.id, tool.name, event.target.checked)} />)}</div>
          </Disclosure>}
        </div>)}
    </FieldGroup>
    <FieldGroup title={editingId ? "Edit server" : "Add server"}
      description="Credentials are write-only. Only their key names are displayed after saving.">
      <TextField label="Server name" value={draftName} onValueChange={setDraftName} placeholder="e.g. research" />
      <Select label="Connection type" value={draftTransport} onChange={setDraftTransport} options={[
        { value: "stdio", label: "Local command (stdio)" }, { value: "streamable_http", label: "Remote server (Streamable HTTP)" },
      ]} />
      {draftTransport === "stdio" ? <>
        <TextField label="Command" value={draftCommand} onValueChange={setDraftCommand} placeholder="e.g. npx" mono />
        <TextField label="Arguments (optional, space-separated)" value={draftArgs} onValueChange={setDraftArgs}
          description="Quote values containing spaces, or enter a JSON array of strings. No shell is used."
          placeholder="-y @modelcontextprotocol/server-filesystem /path/to/allow" mono />
        <TextArea label="Environment variables (one KEY=value per line)" value={draftEnv} disabled={clearEnv}
          onValueChange={setDraftEnv} minRows={3} maxRows={6} spellCheck={false} placeholder={"GITHUB_TOKEN=…\nBRAVE_API_KEY=…"} />
        {editingId && <Checkbox label="Clear saved environment variables" checked={clearEnv} onChange={event => setClearEnv(event.target.checked)} />}
        <p className="settings-field-hint">Commands can execute third-party code. Servers receive these variables and basic system plumbing, never Graphlink provider keys.</p>
      </> : <>
        <TextField label="MCP endpoint URL" value={draftUrl} onValueChange={setDraftUrl} type="url" mono placeholder="https://example.com/mcp" />
        <TextField label="Bearer token (optional)" value={draftBearer} onValueChange={setDraftBearer} type="password" autoComplete="off"
          suffix={{ type: "password" }} disabled={clearHeaders} />
        <TextArea label="Authentication headers (one Name=value per line)" value={draftHeaders} onValueChange={setDraftHeaders}
          minRows={2} maxRows={6} spellCheck={false} disabled={clearHeaders} placeholder="X-API-Key=…" />
        {editingId && <Checkbox label="Clear saved authentication headers" checked={clearHeaders} onChange={event => setClearHeaders(event.target.checked)} />}
        <p className="settings-field-hint">Use HTTPS for remote services. Leave both credential fields empty to keep saved headers.
          Entering credentials replaces the saved header set. Changing the endpoint’s origin clears saved headers unless you enter them again.
          Browser OAuth sign-in is not available here.</p>
      </>}
      <Select label="Tool approval" value={draftApproval} onChange={setDraftApproval} options={[
        { value: "always", label: "Always confirm" }, { value: "once", label: "Confirm once per tool per session" },
        { value: "auto", label: "Auto (no confirmation)" },
      ]} />
      {draftApproval === "auto" && <InlineAlert variant="warning" title="Tools can run without confirmation"
        body="Builder and Graph Runner can call allowed tools on this server without another approval. Use an allowlist and server credentials with the access you intend to grant." />}
      <TextField label="Scopes (optional, comma-separated)" value={draftScopes} onValueChange={setDraftScopes}
        description="Declared scopes accompany tool approval. Server credentials remain the service’s access boundary." />
      <NumberField label="Request timeout" value={draftTimeout} onValueChange={setDraftTimeout} min={1} max={300} unit="seconds" />
      <div className="settings-button-row">
        {editingId && <Button onClick={reset}>Cancel edit</Button>}
        <Button variant="primary" disabled={!draftName.trim() || (draftTransport === "stdio" ? !draftCommand.trim() : !draftUrl.trim())} onClick={save}>
          {editingId ? "Save server" : "Add server"}</Button>
      </div>
    </FieldGroup>
    {error && <InlineAlert variant="error" title="MCP server needs attention" body={error} />}
    {notice && <p className="settings-update-status" role="status">{notice}</p>}
  </div>;
}
