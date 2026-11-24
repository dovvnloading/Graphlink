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


## Typed service steps (version 1)

The registry also includes these namespaced adapters; the legacy scene kinds
and scheduler are unchanged. All use the same typed descriptor, preparation,
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


## Deterministic control flow (version 1)

Control adapters use registry.describe and freeze_config like transforms.

| Adapter | Configuration | Input → output |
| --- | --- | --- |
| control.condition | kind (default json), predicate and arguments | value → then or else (same kind) |
| control.merge | kind (default json), inputs (unique names; default left/right), mode (all or first) | named inputs → result (ordered JSON array for all, original kind for first) |
| control.approval | kind (default json), message | value → value after approval |
| control.assert | kind (default json), predicate and arguments, on_failure (block by default, or route) | value → pass; optional fail when routing |
| control.map | workflow (contained v1 WorkflowSpec), concurrency (1–100, default 1), on_error (stop or continue), optional item_count | items (JSON array or table via table-to-JSON coercion) → results JSON |

Condition and Assert kinds may be text, json or table. Predicate configuration:

- equals and contains take value. Equality is recursively type-aware
  (booleans are not numbers). Contains means case-sensitive substring,
  array membership by type-aware equality, or a JSON object key.
- regex takes pattern and searches text with bounded matching time.
- numeric_compare takes operator (eq, ne, lt, lte, gt, gte) and a finite
  numeric value. It compares JSON numbers only, never booleans or strings.
- empty matches null, the empty string, empty array/object or a table with
  zero rows. json_path_truthy takes path, uses the single-value path grammar
  of the JSON-path transform, and treats missing paths as false.
- Assert supports contains, regex, json_schema (schema using the existing
  structured-output schema subset), numeric_range (inclusive min and/or max),
  and row_count (inclusive nonnegative integer min and/or max, for arrays
  or tables). Block mode stops admission and records a sanitized reason.
  Route mode forwards the original value to exactly one output.
  Invalid configurations are rejected rather than treated as false.

Branch outputs are optional but may bind to required inputs. The journal
records the untaken output in skipped_outputs and its completion event.
Consumers of skipped inputs are skipped, including transitive consumers.
Skips are successful terminal outcomes, not failures. All-mode Merge waits
for terminal inputs, ignores skips, and emits values in declared port order.
First-mode consumes the first successful available value, using completion
sequence and binding order for ties (direct parameters are available
immediately). It neither cancels other producers nor suppresses their real
failures. A Merge with no available input is skipped if there was no failure.

A human gate drains already-admitted work, records its message and pending
approval in the step inspector, and pauses admission and the duration
clock until approved/rejected/cancelled. No automatic timeout or approval
exists. Backend restart interrupts the run; it never replays it.
GET /api/workflows/active-approvals lists pending gates. POST
/api/workflows/{workspace_id}/runs/{run_id}/step-approval with
{"step_id":"gate","approved":true} explicitly resumes (false rejects and
blocks the run). The existing per-launch authorization and workspace/run
scope are checked. The desktop shows a notification for each new pending gate
and provides controls in Run history → Detached workflow runs.

Map receives table rows as JSON objects or items directly from a JSON array.
The contained immutable spec must declare exactly one required JSON input
named item. Nested Map and human Approval inside Map are rejected in this
version; use top-level gates around a Map. At most 100 items are accepted.
For a direct parameter the preview derives the exact count; for an upstream
producer item_count must declare the expected count (checked before child
dispatch). Preview control_flow metadata lists both branch paths and pins
child workflow, policy/model identity, item count and aggregate maximum
estimated cost. Map reserves expanded attempts and costs before dispatch;
child steps share the engine's provider semaphores. Each child has a private
scratch document and durable nested receipts. Results are an input-order
array of {index,status,outputs,error,cost_usd}; outputs contain typed value
records. Stop mode cancels in-flight items and admits no more after failure;
continue retains per-item errors and runs remaining items. Cost overruns
still stop the outer run. Empty input produces an empty array.
The desktop inspector accepts typed request JSON, shows its pinned preview,
and exposes a separate explicit start action.
