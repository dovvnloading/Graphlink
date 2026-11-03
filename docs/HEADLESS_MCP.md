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

The backend binds **only 127.0.0.1** and generates a fresh random bearer
token for each launch. By default it writes the token to:

```text
~/.graphlink/headless.token
```

The launcher prints the listening port and token-file **path**, not the
token, to stderr. The file is created atomically, with POSIX permission
mode 0600 where applicable. On Windows, filesystem ACLs still matter:
keep the file under your own protected user profile and do not share it.

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

The underlying HTTP endpoints live under `/api/control/` and inherit
Graphlink's existing bearer-token, Origin, and trusted-loopback-Host checks.
The MCP adapter accepts only `http://127.0.0.1:<port>` URLs, ignores
proxy environment variables, and refuses redirects to avoid forwarding
the bearer token to another origin.

## Verification

```powershell
python -m pytest -q backend/tests/test_control_api.py backend/tests/test_mcp_server.py
```

The tests include a real stdio subprocess connecting to a real loopback
FastAPI instance, successful authenticated reads, forbidden unauthenticated
requests, modern/legacy MCP negotiation, invalid arguments, and POSIX token
file permissions.

## Next increments

Add mutation tools by reusing the existing graph commands and
`saveChat`/autosave guard, with explicit approval, expected-revision
checks, and an idempotency key per mutating request. Then expose durable
harness run handles, status/transcript/cancellation, and human approval
handling. Remote HTTP MCP should remain opt-in with separately scoped
credentials. Do not simply expose an unrestricted `dispatch_intent` tool.
