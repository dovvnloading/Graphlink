# Workflow contracts (v1)

This package contains the versioned workflow contracts and step-adapter
registry. Graph Runner's scene/journal execution is hosted here by
compatibility adapters, without changing canvas, plugin execution, persistence
or WebSocket behavior. The typed WorkflowSpec has its own detached execution engine; the legacy
canvas scheduler and its compatibility receipts remain unchanged.

- **Ports:** text, JSON (any JSON value), table (ordered unique columns and
  rectangular JSON-scalar rows), file and image (content-addressed asset
  references). Identical types pass unchanged. The only cross-kind coercions
  are JSON → text (pretty-printed JSON), table → text (CSV with header), and
  table → JSON (row objects). All other connections fail with the destination
  port name and a reason. No file or image is loaded or converted implicitly.
- **Side effects:** pure, local-read, cloud, executes, writes. These classes
  describe what the adapter does; they are not permissions. Preparing and
  fingerprinting must not perform effects. Run and cancel are asynchronous
  and cancellation does not guarantee rollback.
- **Frozen spec:** `WorkflowSpec` v1 is a by-value snapshot with stable workflow
  and revision IDs, declared workflow ports, pinned step adapter IDs and
  versions, class and ports, canonical JSON configuration strings
  (`freeze_config`), and explicit source/target bindings. Tuples and frozen
  nested records prevent mutation of a correctly constructed spec. JSON
  serialization uses arrays for tuples; there is no implicit graph traversal.
  The detached engine validates graph structure, port compatibility and
  required inputs, resolves exact adapter versions and rejects cycles before admission.
- **Records:** a run pins the spec and its fingerprint; step records are keyed
  by run/step/attempt and carry class, status, fingerprint, timing, results and
  errors. Events are ordered by sequence within a run and can identify the
  step/attempt. Timestamps are UTC ISO 8601 strings; sequence and attempt are
  nonnegative integers, with attempt numbering starting at one. Records are
  data shapes, not a database or transition engine.

`python contracts/codegen.py --write` generates the standalone JSON Schemas
and TypeScript types in `web_ui/src/lib/workflow-contract/generated/`. The
existing `--check` command checks them for drift; they do not register a topic
or add anything to the existing topic contract.

## Graph Runner compatibility adapters

`StepAdapterRegistry` pins implementations by exact ID and version. The live
runner extends `builtin_registry()` with legacy entries in a frozen registry
snapshot, using the same lookup API. Only legacy GraphStepAdapter entries are
selected by scene kind; typed transforms retain their own port and preview API.
Legacy
`graph_steps` entries select v1 by existing step kind: note (copy and word
count), table/chart/artifact (group sum), model (first-class Model), scientific
and tool (MCP Connect). The opt-in model output note has its own
`model-note` entry; it is never discovered during an ordinary preview.
The compatibility interface does not convert the old scene journal into
WorkflowSpec or change the typed StepAdapter protocol (including preview). No adapter/version
field is added to legacy preview or run bytes.

Read-only pins, inspection, matching and result digests live in the adapters.
The domain planner owns dependency ordering, private projection copies and
ancestor validation. The intent owns admission, mutation serialization,
scheduling, timestamps and checkpoints; `graph_execution` uses those host
services for approved runs. The scientific worker and MCP runtime remain
authoritative for grants, cancellation and results. Both model and MCP use
the existing durable external-request fence, with no new retry path.
Saved histories, remapping, trace exports and schema codegen are unchanged.

Golden compatibility projections in
`backend/tests/fixtures/graph_runner_legacy.json` come from the existing runner
test scenarios. Deterministic text/CSV digests and bytes are literal;
external request identities are checked before normalizing UUIDs.

## Built-in Transform steps (adapter version 1)

Use `backend.workflow.registry.builtin_registry()` to construct a fresh
version-pinned registry. `registry.describe(id, "1", freeze_config(config))`
returns the typed descriptor to store in a step spec; `registry.resolve(id, "1")`
returns the adapter. Unknown IDs/versions and duplicate registrations fail;
there is no version fallback. The standalone factory has no live-runner
service dependencies; typed transforms are not dispatched by Graph Runner.

All eight adapters declare the `pure` side-effect class. Each accepts
already-resolved values of exactly its declared input kind, validates its
configuration and inputs during prepare, and snapshots them by value.
Fingerprints include the pinned ID/version, canonical configuration and
canonical input content, not the step/run/attempt identity. Configurations
have exactly the keys shown below (use `freeze_config`).

| Adapter ID | Config example | Input → output |
| --- | --- | --- |
| `transform.template` | `{"template":"Hello {name}"}` | Named text ports (first occurrence order) → text `text` |
| `transform.json_path` | `{"path":"$.users[0].name"}` | JSON `json` → JSON `value` |
| `transform.text_split` | `{"separator":","}` | Text `text` → JSON `parts` (array of strings) |
| `transform.text_join` | `{"separator":" / "}` | JSON `parts` (array of strings) → text `text` |
| `transform.table_filter` | `{"column":"score","operator":"gte","value":10}` | Table `table` → table `table` |
| `transform.table_sort` | `{"column":"score","direction":"desc"}` | Table `table` → table `table` |
| `transform.table_select` | `{"columns":["name","score"]}` | Table `table` → table `table` |
| `transform.table_rename` | `{"renames":{"score":"points"}}` | Table `table` → table `table` |

- Templates accept simple named fields and doubled braces for literal braces.
  Repeated names share a port; attribute access, indexing, conversions and
  formatting expressions are rejected.
- JSON paths select exactly one value: root `$`, dotted names, zero-based
  array indices, or bracketed JSON-string keys such as `$["a.b"]`. No
  wildcard, recursive descent, script or filter expressions. Missing paths
  fail; an existing JSON null is a valid result.
- Split uses a nonempty literal separator and preserves empty pieces; join
  accepts only an array of strings and permits an empty separator.
- Filter operators: `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `contains`.
  Equality is type-aware (booleans are not numbers); ordered comparisons
  compare only numbers with numbers or strings with strings; contains is a
  case-sensitive substring test. Nonmatching types do not match ordered or
  substring predicates. Filter preserves row order.
- Sort is stable, with booleans before numbers before strings in ascending
  order and the reverse in descending order. Strings use Unicode code-point
  order, numbers use numeric order; nulls always come last.
- Select preserves requested column order, including an empty selection.
  Rename changes only column names in place (swaps are allowed). Unknown
  columns, duplicate selections or rename collisions fail; inputs are never
  mutated.

`adapter.preview(prepared)` returns JSON-compatible metadata by output
port: text character count; JSON type and array length or object keys where
applicable; or table column names and row count. It uses the same local pure
transformation as `run`, but does not call `run` or expose output contents.
No provider, filesystem, asset, network, process or scheduler is required.
Cancellation is a no-op because these adapters retain no external work.


## Detached workflow engine

Instantiate one backend-owned WorkflowEngine per chats.db, on the application's
event loop. Its public Python service API is:

- preview(spec, parameters): validates the immutable DAG, pins its canonical
  hash, the canonical parameters (file/image values pin content hashes),
  adapter versions, side-effect classes, concrete provider/model identities
  and the trusted cost estimate. Preview never dispatches a step.
- approve(workspace_id, preview): trusted host consent boundary after the
  user has reviewed that preview; returns an expiring, single-use token.
  It is not a permission grant to arbitrary adapters or a reusable credential.
- start(workspace_id, spec, parameters, approval_token=...): revalidates every
  pin, persists the frozen run and returns its ID before any step executes.
  Cloud, executes and writes steps require approval; all tokens are consumed
  on admission, including for writes on subsequent identical runs.
- cancel(workspace_id, run_id), await wait(workspace_id, run_id),
  store.read/list: stop and inspect the detached journal. Completed steps
  retain their outputs, blocked steps never dispatch. On shutdown await
  shutdown(); on a new engine startup unfinished records are interrupted
  without replaying an uncertain external effect.
- artifacts.file(bytes, filename, media_type, image=False) imports file
  parameters or adapter-produced binary values into the existing asset store.
  artifacts.unpack(recorded_value) explicitly reads a retained result.
- save_result_as_graph(workspace_id, run_id): explicit action creating a
  new graph in that workspace from retained outputs (or finished intermediate
  results if no declared output finished); it does not replace the active
  graph. Image and file output attachments preserve their content hash.

RunLimits defaults to cloud 4, local 1 (independent shared semaphores), $2,
1800 seconds and 500 attempts. Cost reservations precede dispatch; the
trusted StepPolicy supplies a maximum charge, and StepContext exposes that
bound plus a cumulative report_cost callback. Adapters must enforce their
own provider-side bound; a reported overrun stops the run rather than
pretending an already-incurred external charge can be rolled back. Missing
usage is accounted at the reserved maximum. A cloud adapter without trusted
policy metadata is rejected. Use policy_for to pin cloud provider/model
identities and pricing; never derive authorization from untrusted JSON.

The typed registry includes transforms, graph.note (copy/word count),
graph.group_sum (table/chart/report) and graph.local_model (explicit
loopback Ollama). They use the existing Graph Runner adapters on a private
SceneDocument. Local inference requires approval and pins its endpoint and
model identity; the async transport preserves the existing loopback-only
no-credential policy. Scientific and MCP scene-based adapters retain their existing
authorization and external-request fences; they are not implicitly
converted into typed detached adapters. Additional typed adapters use the
same StepAdapter protocol, with the scratch document and asset store supplied
in StepContext, and must honor asynchronous cancellation.

Values are stored inline or as content references above 64 KiB; file/image
ports always refer to immutable binary assets. Typed values hydrate only at
the adapter/read boundary. The migration adds a workspace-scoped detached
run table beside the legacy graph receipts. Retention keeps the newest 50
runs per workspace for up to 90 days (active runs are exempt until terminal).
Offline asset GC marks kept run records, saved graph attachments and retained
backups before sweeping; retention itself never unlinks a blob.


The application exposes these operations under the existing authenticated
/api/workflows/{workspace_id} prefix: POST preview, POST approve (requires
approved: true and the issued fingerprint), POST/GET runs, GET runs/{run_id},
POST runs/{run_id}/cancel, POST runs/{run_id}/save-as-graph, POST files
(raw bounded binary upload returning a typed file/image parameter), and
GET runs/{run_id}/assets/{hash} or graphs/{graph_id}/assets/{hash}
(attachment-only downloads scoped to a retained reference). Send the
existing v1 WorkflowSpec and PortValue records as JSON using spec and
parameters fields; admission additionally accepts approval_token. The app
owns one lazy service and cancels it at shutdown. Existing read-only/MCP
control capabilities do not authorize workflow execution.
