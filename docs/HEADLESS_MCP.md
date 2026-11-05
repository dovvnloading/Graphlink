# Headless Graphlink and MCP (read-only first stage)

Graphlink's FastAPI server already owns the real graph state, database,
assets, intent handlers and harness. The headless launcher starts that SAME
application without opening the pywebview desktop. A separate local MCP
stdio adapter connects back to its protected read-only control routes.

The default stage supports **inspection only**. Optional canvas writes
require an explicit opt-in and a separate scoped credential (described
below); arbitrary intent execution, unattended approval, filesystem
access, destructive operations, and remote MCP/HTTP hosting remain
unavailable.

## Start one headless backend

From the Graphlink repository root, after installing requirements:

```powershell
python -m backend.headless --port 8765
```

The backend binds **only 127.0.0.1** and generates **two independent random
credentials** on each launch. The full-access WebSocket/backend credential
stays in the backend process; ONLY the limited MCP-read credential is
written to the token file. By default this file is:

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

## Optional MCP canvas writes (explicit opt-in)

This is a separate, permission-scoped capability. **Graphlink starts
read-only by default**, and the existing MCP adapter lists only its five
read tools until explicitly given a write-token-file path. To enable safe
non-destructive editing on the **currently open canvas**:

```powershell
python -m backend.headless --port 8765 --allow-mcp-writes
```

Then configure your MCP client to launch the adapter with the explicit
write capability:

```powershell
python -m backend.mcp_server --url http://127.0.0.1:8765 --write-token-file ~/.graphlink/headless.write.token
```

The headless launcher creates three independent secrets: an administrative
backend token that is never published, a read-only token in
`~/.graphlink/headless.token`, and a write token in
`~/.graphlink/headless.write.token`. Both files use the same secure
atomic creation and Windows DACL/POSIX permission protection. The write
token authorizes ONLY two named POST routes; it does not authorize
`/ws`, `/api/health`, reads, deletion, shell execution, or arbitrary
intent dispatch. Do not share it with untrusted MCP hosts: when a
trusted host has the write file, it can change the live canvas.

**Four write tools are available only with this opt-in:**

| MCP tool | Behavior |
| --- | --- |
| `graphlink_create_node` | Create a chat, note, code, or document node |
| `graphlink_connect_nodes` | Connect existing canvas node IDs |
| `graphlink_set_node_content` | Replace content of chat, note, code, or document nodes |
| `graphlink_save_graph` | Save the live graph using Graphlink's normal guarded Save |

Every call requires an `expected_revision` (64-character hex digest)
returned by `graphlink_list_nodes` and a caller-generated
`operation_id` of 8–100 characters (letters, digits, underscore or
hyphen). On 409 conflict, re-read the scene before trying a *new*
operation; never blindly retry with a fresh revision when the first
call might already have succeeded. To recover a lost response, retry
with the **same operation ID and exact original arguments**. During one
backend process, the server returns the cached result without
reapplying; after a restart, stale-revision checks prevent blind
duplication. Only 256 recent successes are retained.

**Mutations change the live in-memory canvas and are NOT durable until
`graphlink_save_graph` succeeds.** Save failures and stale revisions
are explicit. These tools do not open a different graph or create a new
workspace. A separate, reviewed stage will add persisted graph creation,
selection and deletion with additional authorization and conflict
handling. The UI remains usable through its existing WebSocket intents.

## Verification

```powershell
python -m pytest -q backend/tests/test_control_api.py backend/tests/test_mcp_server.py
```

The tests include a real stdio subprocess connecting to a real loopback
FastAPI instance, authenticated reads with a restricted credential, denied
WebSocket and administrative API access for that credential, modern/legacy
MCP negotiation, invalid arguments, and POSIX token-file permissions.
On Windows, real filesystem DACL tests verify protected user/SYSTEM/admin
access after atomic replacement, rotation and fail-closed behavior when
DACL verification fails. The tests also verify that the launcher writes
ONLY the read-only token and that Graphlink's own legacy MCP client
can connect successfully.

## Next increments

Expand the initial opt-in, non-destructive live-canvas write tools with
safe graph creation/selection and durable multi-operation controls. Then
expose durable harness run handles, status/transcript/cancellation, and
human approval handling. Remote HTTP MCP must remain opt-in with
separately scoped credentials. Never expose an unrestricted
`dispatch_intent` tool.
