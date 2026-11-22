import { useState } from "react";
import type { WsTransport } from "../../../lib/ws/transport";
import type { AppSettingsState } from "../../../lib/bridge-core/generated/app-settings-state";

// ADR-012 stage 12.6: the deferred UI half of ADR-007 stage 7.5's MCP client
// - an McpServerConfig dataclass and SettingsManager.get_mcp_servers/
// set_mcp_servers persistence already existed (backend/mcp_client.py, its
// own module docstring explicitly deferred this page to ADR-012) with zero
// way to view or edit the configured list until now. Every mutation below
// sends the FULL updated array via setMcpServers - bulk-replace, matching
// SettingsManager.set_mcp_servers' own "replace the whole collection"
// posture, the simplest shape to implement and test on both the toggle/
// remove path (derived from the current state.mcpServers) and the add path
// (state.mcpServers plus one new entry). queueable (same as GeneralPage's
// checkboxes/OllamaTaskField's selects above) - a pure persisted setting
// with no live side effect at save time (backend/agents.py's
// _register_configured_mcp_tools only connects enabled servers lazily, when
// the Builder registry is next built), so replaying it after a reconnect is
// always safe.
/** "KEY=value" lines -> env record. Blank lines and lines without "=" are
 * skipped; the first "=" splits, so a value may itself contain "=" (base64
 * tokens routinely do). Exported for direct unit testing. */
export function parseEnvLines(text: string): Record<string, string> {
  const env: Record<string, string> = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    if (!key) continue;
    env[key] = line.slice(eq + 1).trim();
  }
  return env;
}

// SECURITY-FIX (mcp-approval-auto-invisible-in-settings-ui): human-readable
// labels for ToolRegistry's own three-value approval vocabulary
// (backend/tools.py's ApprovalPolicy = Literal["auto", "once", "always"] /
// _KNOWN_APPROVAL_POLICIES) - see the render site below for why this page
// shows the value at all. Falls back to the raw wire string for anything
// outside that set (defensive only; the backend rejects unknown policies
// at registration time, so this page should never actually see one).
const MCP_APPROVAL_LABELS: Record<string, string> = {
  auto: "Auto (no confirmation)",
  once: "Confirm once",
  always: "Always confirm",
};

export function McpServersPage({ state, transport }: { state: AppSettingsState; transport: WsTransport }) {
  const [draftName, setDraftName] = useState("");
  const [draftCommand, setDraftCommand] = useState("");
  const [draftArgs, setDraftArgs] = useState("");
  const [draftEnv, setDraftEnv] = useState("");

  // What setMcpServers SENDS, which is genuinely not the same shape the
  // app-settings state carries back. env values are write-only on this wire
  // (see the contract's own McpServerConfigPayload.envKeys): the state we
  // render has names only, while an outgoing entry MAY carry real values
  // when the user just typed them. A missing `env` means "keep whatever is
  // stored for this server", which is what lets toggling or removing one
  // server leave every other server's variables untouched.
  //
  // Spelled out in full rather than written as `McpServerConfig & { env?: }`
  // deliberately: intersecting a generated wire type is the ADR-003 C9
  // anti-pattern (see lib/bridge-core/wireTypeCastGuard.test.ts) - it tells
  // TypeScript a wire field exists on the author's word. This type describes
  // the OUTBOUND intent argument, which the generated inbound row type has
  // no business defining.
  type OutgoingMcpServer = {
    // REVIEW-FIX: absent on a brand-new "Add Server" entry - the backend
    // assigns a fresh one (see SettingsManager.set_mcp_servers). Present
    // (via the `...s` spread below) on every existing server, echoed back
    // unchanged - that id, not `name`, is now the join key the backend
    // uses to preserve a server's env values across an edit that omits
    // them, since two servers can share a name.
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

  const updateServers = (next: OutgoingMcpServer[]) => {
    transport.fireIntent("app-settings", "setMcpServers", [next], undefined, true);
  };

  const handleAdd = () => {
    const name = draftName.trim();
    const command = draftCommand.trim();
    if (!name || !command) return;
    const args = draftArgs.trim() ? draftArgs.trim().split(/\s+/) : [];
    const env = parseEnvLines(draftEnv);
    updateServers([
      ...state.mcpServers,
      {
        name, command, args, scopes: [], approval: "always", enabledTools: [], enabled: true, timeout: 30,
        // The one place values are sent: the user just typed them. envKeys
        // keeps the entry the same shape the rest of the list has.
        envKeys: Object.keys(env).sort(),
        env,
      },
    ]);
    setDraftName("");
    setDraftCommand("");
    setDraftArgs("");
    setDraftEnv("");
  };

  return (
    <div className="settings-general-page">
      <p className="settings-integrations-intro">
        Configure local MCP (Model Context Protocol) servers. Each enabled server's tools become available to the
        Builder, namespaced and approval-gated - MCP servers run arbitrary user-configured commands, so treat them
        the same as any other third-party code you choose to run.
      </p>

      <fieldset className="settings-fieldset">
        <legend>Configured Servers</legend>
        {state.mcpServers.length === 0 && (
          <p className="settings-update-status">No MCP servers configured yet.</p>
        )}
        {state.mcpServers.map((server, index) => (
          <div className="settings-mcp-server-row" key={server.id}>
            <label
              className="settings-checkbox-row settings-mcp-server-toggle"
              aria-label={server.name || "MCP server"}
            >
              <input
                type="checkbox"
                checked={server.enabled}
                onChange={(event) =>
                  updateServers(
                    state.mcpServers.map((s, i) => (i === index ? { ...s, enabled: event.target.checked } : s)),
                  )
                }
              />
              <span className="settings-mcp-server-info">
                <span className="settings-mcp-server-name">{server.name}</span>
                <span className="settings-mcp-server-command">
                  {server.command}
                  {server.args.length > 0 ? ` ${server.args.join(" ")}` : ""}
                </span>
                {/* Names only, never values - these are the user's own
                    tokens for a server they chose to run, and a settings
                    page is no place to echo a secret back. */}
                {(server.envKeys ?? []).length > 0 && (
                  <span className="settings-mcp-server-command">
                    env: {[...server.envKeys].sort().join(", ")}
                  </span>
                )}
                {/* SECURITY-FIX (mcp-approval-auto-invisible-in-settings-ui):
                    before this line, this row showed only name/command/
                    args/env - a server whose config carries approval="auto"
                    (settable via a hand-edited or imported session.dat; see
                    this codebase's own "hostile data on disk" threat
                    boundary) has its tools run with NO human confirmation
                    in either Builder mode (backend/tools.py's ToolRegistry.
                    invoke gates on `registration.approval != "auto"` -
                    "auto" is the one policy that skips request_approval
                    entirely), and there was no control surface here a user
                    would think to check for it. Read-only display, not an
                    editable control: the gap this closes is the value being
                    INVISIBLE, not being un-settable, and this page's own
                    edit-in-place mutations (the enabled checkbox above,
                    Remove) are the only two established here - reusing that
                    same bulk-replace `updateServers` path for a security-
                    relevant field risked a confused-click flipping
                    "always"->"auto" as a bigger, less-certain change than
                    this pass calls for. Always rendered (unlike the
                    conditional env line above) because the whole point is
                    that this must never be easy to miss. "auto" additionally
                    gets a distinct color - the same --gl-semantic-status-
                    warning token .plan-node-approval already uses for its
                    own approval-needed banner - so the one policy that
                    skips confirmation reads as visually different from the
                    two that don't, not just differently worded. */}
                <span
                  className={
                    server.approval === "auto"
                      ? "settings-mcp-server-command settings-mcp-server-approval-auto"
                      : "settings-mcp-server-command"
                  }
                >
                  Approval: {MCP_APPROVAL_LABELS[server.approval] ?? server.approval}
                </span>
                {/* SECURITY-FIX (mcp-approval-auto-invisible-in-settings-ui):
                    same visibility gap as `approval` above, for the granted
                    `scopes` list. Purely informational, matching
                    PluginsPage's own established stance a few dozen lines
                    down ("no per-scope editing UI - MCP Servers itself has
                    never had one for its own `scopes` field") - scopes are
                    what a server's config declares it was granted, not a
                    per-tool pick a user makes here. */}
                <span className="settings-mcp-server-command">
                  Scopes: {server.scopes.length > 0 ? server.scopes.join(", ") : "No declared scopes"}
                </span>
              </span>
            </label>
            <button
              type="button"
              className="settings-button settings-mcp-server-remove"
              aria-label={`Remove ${server.name}`}
              onClick={() => updateServers(state.mcpServers.filter((_, i) => i !== index))}
            >
              Remove
            </button>
          </div>
        ))}
      </fieldset>

      <fieldset className="settings-fieldset">
        <legend>Add Server</legend>
        <label className="settings-field">
          <span className="settings-field-label">Server Name</span>
          <input
            type="text"
            className="settings-select"
            placeholder="e.g. filesystem"
            value={draftName}
            onChange={(event) => setDraftName(event.target.value)}
          />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Command</span>
          <input
            type="text"
            className="settings-select"
            placeholder="e.g. npx"
            value={draftCommand}
            onChange={(event) => setDraftCommand(event.target.value)}
          />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Arguments (optional, space-separated)</span>
          <input
            type="text"
            className="settings-select"
            placeholder="-y @modelcontextprotocol/server-filesystem /path/to/allow"
            value={draftArgs}
            onChange={(event) => setDraftArgs(event.target.value)}
          />
        </label>
        <label className="settings-field">
          <span className="settings-field-label">Environment variables (optional, one KEY=value per line)</span>
          <textarea
            className="settings-select settings-mcp-env-input"
            rows={3}
            placeholder={"GITHUB_TOKEN=ghp_...\nBRAVE_API_KEY=..."}
            value={draftEnv}
            onChange={(event) => setDraftEnv(event.target.value)}
            spellCheck={false}
          />
          <span className="settings-field-hint">
            A server receives only these variables plus basic system plumbing (PATH, TEMP, HOME) - never this
            app&apos;s own provider keys.
          </span>
        </label>
        <div className="settings-button-row">
          <button
            type="button"
            className="settings-button settings-button-primary"
            disabled={!draftName.trim() || !draftCommand.trim()}
            onClick={handleAdd}
          >
            Add Server
          </button>
        </div>
      </fieldset>
    </div>
  );
}
