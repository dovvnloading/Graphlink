# Headless Graphlink and MCP (read-only by default)

Graphlink's FastAPI server already owns the real graph state, database,
assets, intent handlers and harness. The headless launcher starts that SAME
application without opening the pywebview desktop. An explicitly requested
local browser can provide the app's human-approval UI for MCP-controlled Agent
tasks; by default no browser opens. A separate local MCP stdio adapter
connects back to its protected control routes.

The default setup supports **inspection only**. Optional canvas writes
and workspace-agent task control require an explicit opt-in and a separate
scoped credential (described below). Arbitrary intent execution, MCP tool
approvals/permission grants, direct filesystem access and remote MCP/HTTP
hosting remain unavailable.

## Start one headless backend

From the Graphlink repository root, after installing requirements:

```powershell
python -m backend.headless --port 8765
```

The backend binds **only 127.0.0.1** and generates **two independent random
credentials** on each read-only launch. By default, the full-access
WebSocket/backend credential stays in the backend process; ONLY the limited
MCP-read credential is written to the token file. The explicit `--open-ui`
mode described below hands the full credential only to a local operator
browser via a URL fragment, never to an MCP token file. By default this file is:

```text
~/.graphlink/headless.token
```

The launcher prints the listening port and token-file **path**, not the
token, to stderr. The file is created atomically with POSIX mode 0600.
On **Windows**, the staging file is created with an explicit, protected
access-control list (DACL), granting the current Windows account,
LocalSystem, and local Administrators full access, with **no inherited
access** from the chosen token-file directory. Graphlink verifies the
actual on-disk DACL *before writing the token*. Filesystems that cannot
provide or verify these access controls cause headless startup to fail
rather than silently publish a readable credential. Atomic replacement
preserves that DACL even when rotating an older token file.

Keep the file under your protected user profile and do not share it. The
read credential still permits access to private workspace and graph
contents, even though it cannot write.

Optional flags: `--port`, `--token-file`, `--chat-db`, and
`--settings-file`. With `--port 0`, the OS chooses a free port;
use the reported port in the MCP adapter's URL.

**Only one Graphlink backend can own a data directory.** The desktop and
headless launchers both acquire a nonblocking OS file lock in the directory
containing the resolved `chats.db`. A second launcher refuses startup
*before* changing the crash sentinel or rotating the first backend's MCP
token, even when the two processes use different TCP ports.

The lock file is `<data-directory>/.graphlink.instance.lock`. Leave it in
place: the OS releases its lock automatically when the process exits or
crashes; the file itself is intentionally retained so two processes can
never accidentally lock different replacement files. Graphs and assets
using separate database filenames **inside the same directory** still share
one lock. The MCP adapter never starts a second backend. If running truly
independent instances using different database directories, use
**different `--port`, `--token-file`, and `--settings-file` values too**.
Otherwise the instances may overwrite each other's default MCP token file
or share settings state even though their chat databases are independent.

## Configure a local MCP client

Launch a second process from the same checkout:

```powershell
python -m backend.mcp_server --url http://127.0.0.1:8765 --token-file ~/.graphlink/headless.token
```

This process speaks newline-delimited MCP JSON-RPC over stdin/stdout.
In an MCP host that supports stdio servers, configure its equivalent of:

```json
{
  "mcpServers": {
    "graphlink": {
      "command": "python",
      "args": [
        "-m", "backend.mcp_server",
        "--url", "http://127.0.0.1:8765",
        "--token-file", "~/.graphlink/headless.token"
      ]
    }
  }
}
```

If Graphlink isn't installed as a package, configure the MCP client's
working directory to the checkout's repository root. Paths and the exact
configuration format differ by MCP host. Do not put the token itself in
this config: the adapter reads the per-launch file locally.

The adapter supports both MCP 2026-07-28 stateless discovery
(`server/discover`, per-request `_meta`) and the older
`initialize` / `notifications/initialized` handshake.

## Available read tools

| MCP tool | Result |
| --- | --- |
| `graphlink_list_workspaces` | Persisted workspace IDs and metadata |
| `graphlink_list_graphs` | Paged saved-graph list (optional `workspace_id`) |
| `graphlink_get_graph` | Paged saved-node excerpts, graph ID/title/workspace, counts |
| `graphlink_list_nodes` | Paged nodes on the **currently open live canvas** |
| `graphlink_read_subgraph` | Bounded live-canvas node/edge neighborhood |

Saved-graph reads use one SQLite snapshot for metadata/notes/pins and do
**not** switch the active graph. Live-canvas reads use the same in-memory
session as Graphlink's UI, not a second SceneDocument. Each tool is
explicitly marked read-only. Large results are paginated or excerpted;
`more` and `truncated` tell the caller when additional pages or a
future detailed-read tool will be needed.

The read-only token is valid **only** for GET requests to the five exact
control route shapes under `/api/control/`. It does not authorize the
`/ws` intent connection, `/api/health`, assets, other API endpoints, any
non-GET operation, or a future control endpoint that wasn't explicitly
allowlisted. The read token must be sent in the Authorization Bearer
**header**, not a URL query parameter. The backend also retains its
existing Origin and trusted-loopback-Host checks. The MCP adapter accepts
only `http://127.0.0.1:<port>` URLs, ignores proxy environment variables,
and refuses redirects to prevent forwarding the limited credential to
another origin.

## Optional MCP canvas writes and agent tasks (explicit opt-in)

This is a separate, permission-scoped capability. **Graphlink starts
read-only by default**, and the MCP adapter lists only its five
read tools until explicitly given a write-token-file path. To enable
scoped editing and workspace-agent task control on the **currently open canvas**:

```powershell
python -m backend.headless --port 8765 --allow-mcp-writes --open-ui
```

**`--open-ui` is the supported live-Agent / human-consent path.** It requires
`--allow-mcp-writes` and a built app SPA (for source checkouts, run
`cd web_ui && npm run build` first; missing builds fail startup rather than
opening a broken operator UI). It opens the normal Graphlink SPA in the local default
browser with the same `/#token=...` capability fragment as the desktop
window. In that browser, create an Agent using the Agent composer or open a
saved graph with an Agent; let any initial Agent run finish or stop it.
`graphlink_list_nodes` will then see the **same live harness node** in this
headless backend. MCP may start follow-up tasks against it, and the browser's
Agent panel displays approvals/questions and sends the human's decisions
through the normal authenticated app WebSocket. Keep this window connected
while awaiting consent. No MCP endpoint can approve a task or create an Agent.

This flag is **explicit**, not a bypass for MCP: the browser gets the full
app capability, and the MCP read and write files remain restricted. Never
share the browser's token-bearing URL, use it in an untrusted browser profile,
or leave it in a shared browser history. The fragment is never sent in HTTP
requests and Graphlink does not save the full credential to disk. If a local
graphical browser cannot be opened (for example, on a remote server without
a desktop), the launcher warns without printing the token; do **not** run
MCP Agent tasks requiring approval until an authenticated operator UI is
available on the same instance. For inspection-only use, omit both flags.

Then configure your MCP client to launch the adapter with the explicit
write capability:

```powershell
python -m backend.mcp_server --url http://127.0.0.1:8765 --write-token-file ~/.graphlink/headless.write.token
```

The headless launcher creates three independent secrets: an administrative
backend token passed to the operator browser **only with `--open-ui`**, a read-only token in
`~/.graphlink/headless.token`, and a write token in
`~/.graphlink/headless.write.token`. Both files use the same secure
atomic creation and Windows DACL/POSIX permission protection. The write
token authorizes only five exact control routes: canvas mutation/save
(POST), workspace-agent task start/cancel (POST), and agent task status
(GET). The task-status GET requires the **write token**, not the read
token. It does not authorize `/ws`, `/api/health`, general graph reads,
deletion, shell execution, workspace grants, tool approval, or arbitrary
intent dispatch. Do not share it with untrusted MCP hosts: a host with
this file can change the live canvas and start tasks on existing agents.

**Seven additional MCP tools are available only with this opt-in:**

| MCP tool | Behavior |
| --- | --- |
| `graphlink_create_node` | Create a chat, note, code, or document node |
| `graphlink_connect_nodes` | Connect existing canvas node IDs |
| `graphlink_set_node_content` | Replace content of chat, note, code, or document nodes |
| `graphlink_save_graph` | Save the live graph using Graphlink's normal guarded Save |
| `graphlink_start_agent_task` | Start a task on an **existing live harness (Agent) node** by `node_id` |
| `graphlink_get_agent_task` | Read the node's latest run ID, status, checklist, bounded reply and app-attention flags |
| `graphlink_cancel_agent_task` | Request cancellation of the specified `node_id` + `run_id` |

Every **canvas mutation/save** and **agent-task start** requires an
`expected_revision` (64-character hex digest returned by
`graphlink_list_nodes`) and a caller-generated `operation_id`
of 8–100 characters (letters, digits, underscore or hyphen).
For 409 conflicts, re-read the scene before trying a *new* operation;
never blindly retry with a fresh revision when the first call might
have succeeded. To recover a lost response, retry with the **same
operation ID and exact original arguments**. During one backend process,
the server caches up to 256 recent successful operations per control
surface; scene changes or new agent runs prevent stale cross-canvas
replays. Restarts invalidate old revisions.

For agent tasks, identify an existing `harness` node using
`graphlink_list_nodes`, then call `graphlink_start_agent_task` with
that `node_id`, a task `text`, an operation ID and the observed revision.
This does **not** create a new agent node or switch graphs. Poll
`graphlink_get_agent_task` by `node_id`; optionally supply `run_id` to
reject a status read if a newer task has replaced it. `checklist`
contains the agent's `plan.update` entries; `reply` is the latest
available agent reply, truncated to 16,000 characters with
`reply_truncated` signaling truncation. Cancel using both `node_id`
and the exact `run_id` returned by start; a stale run cannot cancel
a newer task.

**Approval remains exclusively inside the Graphlink app (including its
explicit `--open-ui` local browser).** An MCP-started agent can pause for a
sensitive tool approval or user question; status
indicates `awaiting_app_approval` or `awaiting_app_answer`. Neither
MCP tool schemas nor the write-token HTTP routes expose an approval,
denial, question answer, tool grant, workspace-folder selection, or
unrestricted harness intent endpoint. A person must use the app to
make these decisions.

**Mutations change the live in-memory canvas and are NOT durable until
`graphlink_save_graph` succeeds.** Save failures and stale revisions
are explicit. These tools do not open a different graph or create a new
workspace. A separate, reviewed stage will add persisted graph creation,
selection and deletion with additional authorization and conflict
handling. The UI remains usable through its existing WebSocket intents.

## Verification

```powershell
python -m pytest -q backend/tests/test_control_api.py backend/tests/test_control_writes.py backend/tests/test_control_agent_tasks.py backend/tests/test_mcp_server.py backend/tests/test_mcp_operator_ui.py backend/tests/test_web_research_fetch_policy.py
```

The tests include a real stdio subprocess connecting to a real loopback
FastAPI instance, authenticated reads with a restricted credential, denied
WebSocket and administrative API access for that credential, modern/legacy
MCP negotiation, invalid arguments, and POSIX token-file permissions.
On Windows, real filesystem DACL tests verify protected user/SYSTEM/admin
access after atomic replacement, rotation and fail-closed behavior when
DACL verification fails. The tests also verify that the launcher does not put its administrative
credential in the MCP token files, and that Graphlink's own legacy MCP
client can connect successfully. The operator smoke starts the actual
headless launcher, uses the real stdio adapter, creates an Agent via the
app-authorized WebSocket, and completes a token-bound approval through
that WebSocket while MCP status remains unable to approve it.

## Next increments

Future increments could add safe graph creation/selection, durable
multi-operation controls and bounded transcript pagination for agent
tasks. Tool approvals, workspace grants and user answers must stay
in the Graphlink app, never in MCP. Remote HTTP MCP must remain
separately opt-in with scoped credentials. Never expose an unrestricted
`dispatch_intent` tool.
