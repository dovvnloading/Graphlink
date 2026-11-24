# Workflow contracts (v1)

This package contains the versioned workflow contracts and step-adapter
registry. Graph Runner's scene/journal execution is hosted here by
compatibility adapters, without changing canvas, plugin execution, persistence
or WebSocket behavior for existing step kinds. The typed WorkflowSpec has
its own detached execution engine and also pins pure reusable library
definitions; the legacy canvas scheduler admits them as single Workflow steps.

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
  The detached engine and reusable library definitions validate graph
  structure (duplicate IDs, port references, missing required inputs, cycles),
  port compatibility and exact adapter versions before execution.
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
selected by scene kind; typed adapters retain their own port and preview API.
Legacy
`graph_steps` entries select v1 by existing step kind: note (copy and word
count), table/chart/artifact (group sum), model (first-class Model), scientific
and tool (MCP Connect); workflow handles pinned reusable definitions. The opt-in model output note has its own
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
Existing saved histories and their remapping and trace exports retain their
legacy shapes; Workflow steps add linked child-run receipts.

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
service dependencies for pure execution; typed transforms are dispatched by
Graph Runner only inside validated pure reusable Workflow definitions.

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

## Reusable workflow library

Workflow Library is global to the local chats database (migration 8). A saved
version pins the version-one workflow contract and adapter descriptors, and
has no source canvas node IDs or input contents. Editing a library definition
appends an immutable revision; deleting a library entry archives its label
without invalidating nodes that reference older revisions.

### Canvas

Select a connected branch of ordinary Copy text / Word count notes, open
Workflows in the application bar, and choose Make workflow from selection.
Choose outputs and optionally expose operation choices. External sources
become named inputs, not copied contents. Save a new workflow or append a
revision to a library entry. Open the entry to inspect its definition,
history, export, rename, duplicate, delete, insert a pinned node, or unpack.

A pinned Workflow node can accept literal text, JSON, table inputs, or
connected text sources. Text/number/choice parameters are validated against
the frozen interface and registered adapter configuration. If exactly one text
output is exposed, it can connect to existing text-input nodes. A previewed
Graph Runner step resolves the exact pinned library revision and records a
linked pure child execution receipt in the graph's transaction. No external
effects are authorized by this adapter. A newer revision is never substituted
automatically; the upgrade preview explicitly resets input configuration and
results. Existing versions remain intact.

### Portable files and current scope

The .graphlink-workflow file contains only a validated version-one spec and
parameter declarations. Export scrubs configuration JSON before validating;
import rejects incompatible ports, cycles, unknown adapter versions, secret
patterns and non-pure adapters. Import creates a new local library identity.

All eight built-in pure registry transforms and the pure chart/report service
adapters are supported for validated imported definitions. Canvas capture and editable unpack currently cover the existing
plain-note text adapters; imported definitions using other transforms can run
as pinned nodes but cannot yet be unpacked as native editable canvas
operations. Model, scientific, MCP and other external/effectful steps retain
their existing runner and approvals and cannot be captured by this library.

## Typed service steps (version 1)

The registry also includes these namespaced adapters; the existing legacy
scene kinds and scheduler remain compatible. All use the same typed descriptor, preparation,
fingerprint, preview, run and cancel interface as transforms.

| Adapter | Class | Configuration | Input → output |
| --- | --- | --- | --- |
| model.generate | cloud (conservative) | provider, model_id, system_prompt, template, output_kind; optional schema (required for JSON), estimated_output_tokens | Named text ports → text or JSON |
| python.execute | executes | code, inputs (name-to-port-kind mapping), output_kind, optional timeout_seconds (1–240) | Named variables and file/image paths → text, JSON, table or file |
| knowledge.search | local-read | optional k (1–50) | query text → passages JSON, cited text |
| web.research | cloud | optional max_sources (1–8) | query text → cited text, sources JSON |
| chart.generate | pure | label_column, value_column | table, optional title text → chart JSON |
| report.generate | pure | label_column, value_column | table, optional intro text → Markdown report |
| image.generate | cloud | model_id | prompt text → content-addressed image |
| agent.run | executes | folder | task text → text |

Model JSON supports the documented schema subset (type, properties, required,
items, enum, boolean additionalProperties). An invalid reply fails the step;
there is no automatic repair/retry. The preview estimates cost with the existing
pricing table (unknown prices remain unknown). The output token estimate is
an estimate, not an enforced budget. The exact provider/model pair must be
available in the unified cached catalog.

Python runs through the existing guarded virtualenv sandbox (not an OS
filesystem security boundary); no implicit dependency installation is performed.
Inputs are variables, table inputs are objects with columns and rows, and
file/image inputs are paths to staged copies. Text output is stdout; for
other output kinds assign result to a JSON value, a columns/rows object, or
a relative regular file path respectively. Outputs are bounded and file
outputs are stored by content hash.

Construct WorkflowRunHost with a frozen WorkflowSpec, run ID, durable
WorkflowRequestJournal (use a persistent SQLite path, not memory), settings,
and the required existing session services. Pass the host to
builtin_registry(host). A discovery registry can prepare/preview but refuses
effectful execution. The host verifies the pinned spec/attempt, caps model steps
at 64, and uses the existing external dispatch fence to atomically reserve
model and Fal request IDs and checkpoint their typed outputs. Reopening the
journal allows inspecting completed/uncertain receipts; it never silently
resends, even with a new attempt number.

The caller must supply a trusted per-workflow/revision/destination
cloud_consent lookup and async approve callback for fresh action approvals.
The spec itself never grants permission. Knowledge honors embedding
settings and consent, using keyword-only search if the embedding service
is unavailable or not authorized; the step does not populate cold embeddings.
Web Research uses the existing per-domain approval path including redirects.
Fal requires a current price estimate and a fresh approval of the exact
prompt/model/price before submitting. The Agent uses a chosen trusted folder
and fresh approvals, not saved session/shell grants. This is the typed
execution adapter boundary, not a new workflow scheduler or canvas UI.

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


## Run presets and automation

Open **Workflow Library → Run presets and runs**, choose the run workspace
and a pinned library revision, provide JSON inputs and promoted parameters,
then review and approve a preset. The operator chooses its unattended effect
classes (pure, local-read, cloud) and maximum USD cost, and can run it manually
or choose a local-time daily schedule, interval (10 seconds minimum), or
existing watched directory (recursive metadata changes, no file contents read,
no symlink traversal, at most 10,000 entries). The scheduler polls only during
the application lifespan. Each preset establishes a fresh baseline after
startup or creation; missed intervals and busy-workflow triggers are skipped.
There is no catch-up queue. Folder changes are a trigger, not automatic file
imports or runtime parameter overrides. Delete a preset to stop future
triggers; this does not cancel an admitted run.

The preset stores the exact request, resolved preview fingerprint, approved
effects and cost cap in the existing chats database. Re-resolve and compare
on admission; changed parameters, adapter/provider policies or unavailable
library definitions require a new operator-reviewed preset. The effective
cost limit is the lesser of the preset and host cap. Only one detached run
per workflow ID (including pinned revisions and paused runs) may be active.
Disallowed effects, including every executes/writes step, park before dispatch
and notify the operator; the UI shows the run's frozen spec and inputs and
allows one decision against the specific step nonce. Denial stops the run.
The duration cap includes approval waiting; shutdown interrupts rather than
replaying pending or running steps. The normal run store retains the receipt.
The portable library remains pure-only; the operator preset API also accepts
a local typed detached spec plus its typed parameters (currently text, JSON,
and table only). This does not enable adapters without a live host or expand
their existing capabilities.

### CLI and MCP

Start Graphlink's headless backend with its existing opt-in write credential.
The CLI connects to that backend, never opens a second database or launches an
engine. For example (substitute the workspace/preset/run IDs and token path):

```sh
python -m backend.workflow.cli --write-token-file ~/.graphlink/headless.write.token --workspace-id 1 list
python -m backend.workflow.cli --write-token-file ~/.graphlink/headless.write.token --workspace-id 1 run PRESET_ID
python -m backend.workflow.cli --write-token-file ~/.graphlink/headless.write.token --workspace-id 1 read RUN_ID
```

Use `--request-json '{"workflow_id":"...","revision":"...","inputs":{"input_1":"hello"},"parameters":{}}'`
on run to verify an exact approved request. The preset already saves those
parameters; arbitrary overrides are rejected and must be reviewed in the UI.
Reuse the printed `--operation-id` on a retry; it resolves to the prior run
while the receipt is retained. No retry guarantees exist after retention.
MCP offers `graphlink_list_workflows`, `graphlink_run_workflow`,
and `graphlink_get_workflow_run` only when the separate write token is opted in.
Read and write control credentials cannot create presets, approve run previews,
or approve a paused step. Results are data, not instructions.

### Builder

`workflow.list` discovers library IDs and revisions.
`workflow.preview_edit` produces an exact review object; supply that object to
`workflow.instantiate`, `workflow.wire_inputs`, `workflow.create_step` or
`workflow.create_control`. Every mutating call requires fresh approval
(including Autopilot), checks the preview again after approval, and records
an undoable canvas command. The supported step nodes are bound text
copy/word-count notes; control nodes are literal input and bound output
boundaries using existing canvas kinds (not new conditional/loop adapters).
No Builder workflow edit starts a run. Use Graph Runner's normal run
preview/approval to execute the resulting canvas graph.
