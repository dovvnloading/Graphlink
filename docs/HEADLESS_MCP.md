# Headless Graphlink and MCP (read-only first stage)

Graphlink's FastAPI server already owns the real graph state, database,
assets, intent handlers and harness. The headless launcher starts that SAME
application without opening the pywebview desktop. A separate local MCP
stdio adapter connects back to its protected read-only control routes.

This initial stage supports **inspection only**. It does **not** provide
graph mutation, arbitrary intent execution, unattended approval, filesystem
access, or remote MCP/HTTP hosting. Those are separate security-reviewed
increments.

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
token, to stderr. The file is created atomically, with POSIX permission
mode 0600 where applicable. On Windows, filesystem ACLs still matter:
keep the file under your own protected user profile and do not share it.
The read credential still permits access to your private workspace and
graph contents, so treat it as sensitive even though it cannot write.

Optional flags: `--port`, `--token-file`, `--chat-db`, and
`--settings-file`. With `--port 0`, the OS chooses a free port;
use the reported port in the MCP adapter's URL.

**Use only one Graphlink backend for a data directory at a time.**
Do not separately run the desktop process against the same live database.
The MCP adapter never starts a second backend.

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

## Verification

```powershell
python -m pytest -q backend/tests/test_control_api.py backend/tests/test_mcp_server.py
```

The tests include a real stdio subprocess connecting to a real loopback
FastAPI instance, authenticated reads with a restricted credential, denied
WebSocket and administrative API access for that credential, modern/legacy
MCP negotiation, invalid arguments, and POSIX token-file permissions.
The tests also verify that the launcher writes ONLY the read-only token and
that Graphlink's own legacy MCP client can connect successfully.

## Next increments

Add mutation tools by reusing the existing graph commands and
`saveChat`/autosave guard, with explicit approval, expected-revision
checks, and an idempotency key per mutating request. Then expose durable
harness run handles, status/transcript/cancellation, and human approval
handling. Remote HTTP MCP should remain opt-in with separately scoped
credentials. Do not simply expose an unrestricted `dispatch_intent` tool.
