# MCP Connect

MCP Connect makes a configured Model Context Protocol tool a saved canvas node. Choose a tool, supply literal arguments or connect upstream outputs, review the resolved request, and run it. The node keeps the response and a provenance receipt. Graph Runner can execute connected tool nodes in dependency order alongside existing text, table, scientific and local Model steps.

This guide covers Graphlink acting as an **MCP client**. The separate headless MCP server in [`backend/mcp_server.py`](../backend/mcp_server.py), which exposes Graphlink to another client, has its own access controls.
See [Headless Graphlink and MCP](HEADLESS_MCP.md#run-approved-workflows-with-cli-or-mcp)
for the opt-in `graphlink_list_workflows`, `graphlink_run_workflow` and
`graphlink_get_workflow_run` tools. Those start and inspect operator-approved
detached presets; they do not approve steps or invoke arbitrary MCP Connect
nodes. MCP Connect tool nodes remain explicit Graph Runner steps with their
own server policies, not automatically registered typed detached adapters.
See [Executable Workflows](WORKFLOWS.md) for the distinction.

## Enable the plugin and connect a server

1. Open **Settings → Plugins** and enable **MCP Connect**. Its plugin grant allows graph reading and mutation; each server's tool policy still controls execution.
2. Open **Settings → MCP servers**. Enter a **Server name** and choose a **Connection type**.
3. Configure a local command or an HTTP endpoint as described below. Leave **Tool approval** at **Always confirm** for the first run, then choose **Add server**.
4. Choose **Test connection and discover tools** on the saved server. This starts the configured process or connects to the endpoint, initializes MCP, and lists tools. It does not invoke a tool.
5. Review the discovered tools and restrict the allowlist if appropriate. Open **Plugins → MCP Connect** to add a tool node.

The plugin can be added on an empty canvas. Creating it with a source node selected also creates an ordinary incoming connection, ready to use for an input binding. Existing MCP server settings remain available to Builder as well as to MCP Connect.

### Local command (stdio)

Use the executable and arguments documented by your MCP server. The **Command** field names one executable; **Arguments** contains its argument vector. Graphlink does not pass this configuration through a shell.

Arguments accept quoted values or a JSON array of strings. For example, if your server is a Python script, use your Python executable as **Command** and adapt these arguments to its documented interface:

```json
["/path/to/server.py", "--directory", "/path/with spaces"]
```

Add required credentials or settings under **Environment variables**, one `KEY=value` per line. The child receives these variables plus basic system plumbing; Graphlink's provider credentials are not inherited automatically. Starting the command executes that server's code under your user account, including during connection testing and discovery.

When editing, leaving environment variables blank retains the saved set. Entering a new set replaces it. **Clear saved environment variables** removes it explicitly. **Restart** reapplies the configuration for the next connection; connections are opened on demand.

### Remote server (Streamable HTTP)

Enter the server's MCP endpoint, for example `https://your-service.example/mcp`. Remote endpoints require HTTPS. Plain HTTP is accepted only for a loopback endpoint such as `http://127.0.0.1:8000/mcp`.

Use **Bearer token** for a token supplied by the service, or **Authentication headers** for documented custom headers:

```text
X-API-Key=your-service-key
```

A bearer token becomes an `Authorization: Bearer …` header. Headers cannot override transport framing, the MCP session/protocol headers, or other reserved headers. Put credentials in these fields. URLs containing user-info credentials or recognized credential query parameters such as `token` or `api_key` are rejected before saving. Ordinary query parameters, such as a project reference, remain supported.

Saved credentials are write-only in the UI: only environment/header names are published back to it. Leave both HTTP credential fields empty to retain saved headers, or choose **Clear saved authentication headers** to remove them. Entering new credentials replaces the saved header set. Changing the endpoint's origin clears old headers unless you enter credentials for the new origin explicitly.

HTTP support negotiates MCP revisions **2025-11-25**, **2025-06-18**, or **2025-03-26**, accepts JSON or request-scoped Server-Sent Events, and retains a negotiated session for that connection. The stdio client retains its **2024-11-05** initialization version. There is no browser OAuth sign-in, token refresh flow, legacy HTTP+SSE transport, background event subscription, sampling, or elicitation flow in MCP Connect. A service requiring interactive OAuth must be connected through a compatible setup that supplies a usable credential to Graphlink.

Connection credentials use Graphlink's existing secret-storage facility: Windows DPAPI when available, with the existing permission-restricted plaintext fallback on non-Windows platforms or when DPAPI is unavailable. They are not copied into node configuration or run receipts. MCP Connect redacts literal matches for saved environment/header values from discovered tool metadata and results before publication and persistence; this does not change Builder's existing tool-result handling.

### Tool access and approval

An **empty allowlist means all advertised tools are allowed**. After restricting the list, newly advertised tools stay unavailable until included. If you deselect the last allowed tool, the settings UI disables the server and preserves that last selection; it does not turn an empty selection into permission for every tool. The server's switch can disable all its new connections without deleting saved results.

| Tool approval | Behavior |
| --- | --- |
| **Always confirm** | Each new tool call requires explicit review. On a tool card, reviewing **Preview tool** and choosing **Run tool** supplies that approval. Graph Runner displays **Approve tool** / **Deny tool** with the resolved arguments before dispatch. |
| **Confirm once per tool per session** | The first approved tool call permits subsequent calls to that tool for the current MCP Connect session, unchanged connection configuration, and unchanged input/output schemas and annotation hints. Disconnecting the app session or changing any of those pins invalidates that session consent. |
| **Auto (no confirmation)** | Allowed tools can run in Builder or an explicitly started Graph Runner workflow without a second per-tool confirmation. |

**Scopes** are declared permission metadata displayed with the request. The remote service's credentials and authorization rules determine actual service access. Server annotations such as `readOnlyHint` and `idempotentHint` are hints, not permission grants or evidence that replay is safe. Builder also applies its existing tool executor's scope and approval checks.

## Configure and run a tool node

1. In the node, choose **MCP server**, then **Discover tools**.
2. Use **Search tools** and **Tool** to choose an allowed operation. The card shows its description and a **Tool schema** disclosure.
3. Fill its arguments. Primitive properties use form fields; optional properties have **Include …** controls. Use **Argument editor → JSON object** for compound or nested inputs. The backend validates the complete schema before dispatch.
4. Choose **Save inputs** to configure a future Graph Runner step, or **Preview tool** to review a single call. Neither operation invokes the tool.
5. Inspect the resolved arguments, server, tool and declared scopes. Choose **Run tool** to execute that reviewed request.

The preview pins the configuration, connection settings, input fingerprint and existing output. Changing them invalidates that preview. Before the actual call, Graphlink reconnects, confirms that the tool still exists, compares its live schemas and annotation hints with the saved configuration, and rechecks permissions and inputs.

### Bind a connected source to an argument

Create the MCP node from a selected source, or connect an existing source to it with the canvas's ordinary connection handles. In the argument's **Input for …** selector, choose that incoming node instead of **Enter a value**.

| Output selection | Value passed to the tool |
| --- | --- |
| **Text** | The source's committed text, such as a note, chat message, Drafter document, local Model output or MCP text result. |
| **Structured JSON** | Structured MCP content, or valid JSON parsed from a text source. The optional JSON Pointer selects a nested value. |
| **Table rows** | An array of row objects from a native table or a compatible MCP result. Native CSV cells remain strings; values are not silently converted to numbers. |

Bindings replace matching **top-level argument properties** at execution time. Other properties remain literal JSON. For a nested request body, bind the entire body property or construct the complete nested object in the JSON editor.

JSON Pointer examples:

| Pointer | Selection |
| --- | --- |
| Empty | The entire selected output. |
| `/items/0/name` | The `name` field of the first object in `items`. |
| `/rows/2` | The third item of a `rows` array. |
| `/a~1b/~0key` | The `~key` property inside a property named `a/b`. |

Pointers are literal paths, not code or expressions. Missing paths, disconnected sources, type mismatches and dependency cycles block execution. A configured upstream MCP node can be bound before it has a result; Graph Runner waits for its actual output. Stale or errored upstream results cannot silently satisfy a dependent call.

## Read, reuse and retain outputs

The **Saved result** section has **Text**, **JSON**, a **Table** view when applicable, and **Provenance**. Non-text MCP content blocks are retained under **Additional content blocks**; resources are not opened automatically.

Provenance records the server/tool identity, local request ID, start/finish timestamps, input/configuration/result/receipt fingerprints, and the bound source selections and value fingerprints. It does not include the server command, endpoint credentials or authentication headers. **Copy result with provenance** copies the selected saved version as JSON.

Use **Result version** to inspect previous results. Viewing history does not change the output used by downstream nodes. Changing an input or configuration preserves the earlier result until a new call completes. History retains up to ten prior results and may retain fewer to stay inside the node's persistence budget.

The text output can feed a plain note or a Model node. Compatible table output can feed existing grouped-table, chart and report workflows. A bindable MCP table is a nonempty array of objects with matching column names and scalar cells, either directly in `structuredContent` or under `rows`, `items`, or `data`. Nested object cells are not flattened. Native chart/table operations keep their own requirements, including distinct grouping and numeric columns. The visual table preview may show additional JSON shapes; a preview alone does not make an arbitrary result a typed table source.

Saving and reopening preserves node configuration, result history and fingerprints while remapping source node IDs. A missing result asset or damaged receipt is reported as unavailable and cannot authorize another call or supply a fresh downstream value.

## Run connected tools together

Save the inputs on each tool, then open **Plugins → Graph Runner**, or use a tool node's menu → **Open graph runner**. Choose **Selected nodes** or **Whole graph**, then **Preview run**.

Preview computes dependency order without calling tools. Unchanged valid results are **Current**. Changed runnable tools are **Ready**. A consumer needing a new upstream result is **Waiting**. Several incoming bindings form a fan-in: every prerequisite must finish before the tool can run.

Choose **Run graph** and answer any tool approvals in the runner. Independent branches can execute within the selected concurrency limit. A failed or uncertain branch blocks its dependents; unrelated branches can finish. The runner saves a durable request fence before each external call, then saves the output and completion receipt together.

**Resume run** rechecks saved inputs, configuration and outputs. It does not resend a previously dispatched request whose outcome is unknown. A successful cached result remains local data; rerunning a tool is a distinct explicit action.

### Errors, cancellation and reconciliation

A tool response with `isError: true` is a known response. Graphlink saves it for inspection, marks the step failed, and prevents its use as a fresh source. Correct the inputs or service problem before explicitly starting another call.

A timeout, connection loss, cancellation after dispatch, malformed/oversized response, or failure to save the completed receipt can leave the external outcome uncertain. **Cancel tool** can stop local waiting or close the transport; it cannot guarantee that a remote action was cancelled. These cases remain **Reconciliation required** and are never automatically retried.

1. Inspect the node's request receipts and Graph Runner history.
2. Check the operation in the connected service using its activity/history and the recorded tool, inputs and timestamps. The `graph-…` request ID is Graphlink's local audit identifier; it is not a service-side idempotency key, and the service may use a different request ID.
3. When you have checked the outcome, choose **I checked the outcome** and confirm. This persists an acknowledgement and clears the uncertainty block; it neither asserts that the action succeeded nor creates a replacement result. An earlier cached output remains stale until a new successful result is saved, even if you return the arguments to their old values.
4. If another action is needed, create a fresh preview and run it explicitly. Acknowledgement never starts a call.

Undo restores canvas configuration and saved outputs. It does **not** reverse an external action, refund a charge, or make a dispatched request safe to replay. Deleting a node also leaves remote effects in place. Unresolved request fences are retained even when a containing run has a cancelled or partial status. The durable request history takes precedence over restored node state: undo cannot make an older output current after an unresolved or acknowledged uncertain call. An acknowledged request remains in run history until a later successful call to that node supersedes its refresh requirement.

## Shipped limits

These are enforced bounds, not configurable suggestions. A response that cannot be safely retained is not treated as a complete result.

| Boundary | Limit |
| --- | --- |
| Server request timeout | 1–300 seconds; default 30 seconds. |
| Waiting for a Graph Runner tool approval | 10 minutes. |
| Parallel Graph Runner steps | Default 3; selectable from 1 through 8. |
| Tool discovery | 2,000 tools, 100 pages, and a bounded 2,000,000-character catalogue. |
| Each node's input/output schema | 64 KiB of canonical JSON. |
| Resolved arguments | 256 KiB of canonical JSON. |
| Connected argument fields | 64. |
| JSON data | Depth 40 and 50,000 values per bounded payload; nonfinite numbers are rejected. |
| JSON Pointer | 2,048 characters. |
| Saved result including its receipt | 512 KiB of canonical JSON. |
| Tool text | 200,000 characters at the transport boundary; MCP Connect refuses a truncated result. |
| Saved node state | 1 MiB, including configuration, latest result and history. |
| Result history | At most ten previous results, trimmed oldest-first to fit the state bound. |
| Bindable table result | 10,000 rows and 128 columns, subject to the result/state bounds. |
| Table preview | First 100 rows and at most 20 columns; the saved JSON retains the complete accepted result. |

Schema validation supports `type`, `enum`, `const`, object properties/required/additional and pattern properties, property names, array items/prefix items/contains/uniqueness, numeric and length bounds, `allOf`/`anyOf`/`oneOf`/`not`, `if`/`then`/`else`, dependent properties/schemas, and local JSON Pointer `$ref` references through `$defs` or `definitions`. Common descriptive keywords and `format` are annotations; formats are not independently asserted. Regex and schema evaluation have work limits. Unsupported assertion keywords, such as `unevaluatedProperties` and `$dynamicRef`, produce an explicit validation error instead of being ignored. No schema reference triggers a network fetch.

## Developer map and invariants

| Responsibility | Implementation |
| --- | --- |
| Plugin manifest and registration entry point | [`plugins/mcp_connect/plugin.toml`](../plugins/mcp_connect/plugin.toml), [`plugin.py`](../plugins/mcp_connect/plugin.py). |
| Node factory, live serializer and saved-state hooks | [`graphlink_plugins/mcp_connect/plugin.py`](../graphlink_plugins/mcp_connect/plugin.py). |
| Pure configuration, dependency resolution, source freshness and receipts | [`graphlink_plugins/mcp_connect/core.py`](../graphlink_plugins/mcp_connect/core.py). |
| Bounded JSON Schema and JSON Pointer validation | [`graphlink_plugins/mcp_connect/schema.py`](../graphlink_plugins/mcp_connect/schema.py). |
| Shared stdio client, tool metadata/result types and Builder adapter | [`backend/mcp_client.py`](../backend/mcp_client.py). |
| Streamable HTTP/session/SSE transport | [`backend/mcp_http.py`](../backend/mcp_http.py). |
| Server persistence and secret handling | [`settings_store/mcp.py`](../settings_store/mcp.py), [`backend/settings.py`](../backend/settings.py). |
| Session connections, permission rechecks, approvals and dispatch | [`backend/mcp_connect_runtime.py`](../backend/mcp_connect_runtime.py). |
| Discover/configure/preview/run/cancel/approval/reconciliation intents | [`backend/api/intents_mcp_connect.py`](../backend/api/intents_mcp_connect.py). |
| Runner planning, async adapter and durable journal | [`backend/domain/graph_runner.py`](../backend/domain/graph_runner.py), [`backend/api/intents_graph_runner.py`](../backend/api/intents_graph_runner.py), [`backend/domain/graph_run_external.py`](../backend/domain/graph_run_external.py). |
| History retention and trace export | [`backend/domain/graph_run_history.py`](../backend/domain/graph_run_history.py), [`backend/domain/graph_run_inspector.py`](../backend/domain/graph_run_inspector.py). |
| Node, form, results, approvals and reconciliation UI | [`McpToolNodeView.tsx`](../web_ui/src/app/canvas/McpToolNodeView.tsx), [`McpArgumentEditor.tsx`](../web_ui/src/app/canvas/McpArgumentEditor.tsx), [`McpResultView.tsx`](../web_ui/src/app/canvas/McpResultView.tsx), [`McpApprovalPanel.tsx`](../web_ui/src/app/canvas/McpApprovalPanel.tsx), [`McpReconciliation.tsx`](../web_ui/src/app/canvas/McpReconciliation.tsx). |
| Frontend payload guards and intent facade | [`mcpConnect.ts`](../web_ui/src/app/canvas/mcpConnect.ts), [`mcpScene.ts`](../web_ui/src/app/canvas/mcpScene.ts). |
| Server settings UI | [`McpServersPage.tsx`](../web_ui/src/app/chrome/settings/McpServersPage.tsx). |

`SceneNode` keeps its existing core field set. The namespaced plugin state is immutable JSON, published as a JSON string in `pluginState.mcp`. Version 1 contains `nodeKey`, `config`, `latest`, `history`, `status` and `error`, with a sticky `requiresRefresh` flag after an uncertain attempt. Configuration stores `serverId`, `serverName`, `toolName`, schemas, annotation hints, literal `arguments` and `bindings`; no connection credentials are part of it. Each binding has exactly `sourceId`, `output` and `pointer`.

`prepare(document, node)` returns resolved arguments, source fingerprints, the input hash and currentness. It has no transport side effects. Configuration hashes include a binding identity that changes when the configuration changes, plus a stable node identity; reminted runtime source IDs are excluded from stable receipt fingerprints and explicitly remapped on reopen.

Execution must retain the existing mutation guard, recheck the live server configuration and tool schema, secure approval, and pass through `dispatch_external_attempt`. The pre-dispatch checkpoint must durably save the graph and journal before `tools/call`; the result and completed receipt must commit together. Do not add transport retries, infer replay permission from annotations, or prune unresolved attempts. `can_prune_run(run, document.graph_runs)` validates step statuses and external fences and retains acknowledged refresh barriers until a later successful receipt for the same node is available. `core.prepare` checks that journal independently of undoable plugin state; only the executing request's exact ID may exempt its own dispatched fence during live runtime validation.

The focused tests are [`test_mcp_connect_domain.py`](../backend/tests/test_mcp_connect_domain.py), [`test_mcp_connect_schema.py`](../backend/tests/test_mcp_connect_schema.py), [`test_mcp_connect_intents.py`](../backend/tests/test_mcp_connect_intents.py), [`test_mcp_connect_safety.py`](../backend/tests/test_mcp_connect_safety.py), [`test_mcp_client.py`](../backend/tests/test_mcp_client.py), [`test_mcp_http.py`](../backend/tests/test_mcp_http.py) and [`test_mcp_settings.py`](../backend/tests/test_mcp_settings.py). Run the full repository checks in [`AGENTS.md`](../AGENTS.md) before shipping; focused suites do not replace them.
