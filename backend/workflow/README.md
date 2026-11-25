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
usage is accounted at the reserved maximum, including interrupted dispatches.
Failed or cancelled non-Map steps retain at least the reserved maximum; a
partial usage report is not a final bill. A cancelled workflow remains busy
until any draining adapter tasks and cancel hooks settle. A cloud adapter without trusted
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


## Built-in starter workflows

Open Workflow Library → Built-in starters and import any of the six v1
examples. Import makes an independent local library entry. Opening the entry
loads a synthetic example request into the existing detached workflow
inspector; review or edit the typed request, then preview and explicitly
approve/start it. The examples are versioned, exportable, and importable as
.graphlink-workflow files. The separate starter format accepts only exact
shipped definitions and synthetic examples (local library identity may
change). Ordinary portable definitions and canvas nodes remain pure-only.
Importing, opening or exporting an example never grants execution approval.

| Starter | Example input | Output |
| --- | --- | --- |
| Table to chart to report | Team/ticket table | Grouped bar chart and Markdown report with model commentary |
| Research to summary | Question and source-labelled research notes | Extracted evidence and concise cited summary |
| Compare two models on one prompt | One prompt | Two answers and a side-by-side text comparison |
| Document question answering with citations | Question and passage-labelled document text | Answer prompted to cite supplied passage IDs |
| Summarize every table row | Topic/detail table | Map results in input order, with a typed summary per row |
| Text quality gate | Draft | Rewritten text only if Assert finds a nonempty Action line |

The research and document examples use supplied text, not live web search or
knowledge indexing. Citation labels are generated by the model, not verified
references. Models default to local Ollama qwen3:8b (and llama3.2:3b for the
comparison), at http://127.0.0.1:11434. Install these models or edit the
model/endpoint configuration in the typed request, including the contained
Map workflow where applicable, before preview. The engine checks the pinned
endpoint and model identity; the examples never start a model automatically.

The focused offline regression suite is:
`python -m pytest -q -n0 backend/tests/test_workflow_starters.py`.
It imports the actual library entries and uses the production engine,
registry, chart/report, Map and Assert; only the local-model completion
transport is replaced with scripted fixture replies.

## Workflow assurance and task evaluation

The ordinary root pytest collection in the PR `python-tests` CI job includes
both suites; neither needs a provider key, daemon, external tool, or live
network access. Focused reproduction from the repository root:

```sh
python -m pytest -q -n0 backend/tests/test_workflow_fault_assurance.py backend/tests/test_workflow_task_evals.py
python -m backend.evals.workflow_tasks
```

The fault suite uses the detached engine, real SQLite journal and asset
store, a real spawned worker, and in-process ASGI/WebSocket transport. A
separate fsynced ledger stands in for paid model and external write calls;
it is intentionally independent of the recorded run/step state. Each fault
checks the exact allowed effect sequence, preserved completed step
outputs/receipts, and the absence of downstream effects where applicable:

| Fault | Injection / expected behavior |
| --- | --- |
| Process death mid-step | Hard exit after the remote-effect ledger append and durable dispatch checkpoint. Startup interrupts uncertain steps without replay; previously completed outputs are retained. |
| WebSocket disconnect | Disconnect and reconnect a real ASGI WebSocket while an HTTP-admitted detached run is gated in a provider adapter. Replay its consumed approval; it cannot start a second run. |
| Stale revision | Change the frozen revision after approval and edit an actual canvas after its preview. Neither stale admission can dispatch; the prior durable result remains. |
| Duplicate provider response | Replay dispatch and re-deliver the identical completed receipt at the request-journal boundary. The original request IDs, typed outputs and row count remain unchanged. |
| Slow adapter | Block on an explicit gate; cancel or advance the loop's monotonic deadline. No sleeps determine when the fault occurs. |
| Corrupt run record | Add trailing malformed JSON to a checkpoint containing finished steps. Startup fails closed and leaves the damaged bytes untouched; after explicit restoration of the intact prefix, recovery preserves finished receipts without replay. |
| Exhausted budget | Spend the approved cost on the first model step, then block the next paid step and its write dependency. |
| Denied approval | Withhold approval and retry admission; no new run or dispatch is created. |
| Partial artifact write | Truncate the staged asset and raise at atomic replacement. No torn asset is published or referenced; the previously completed steps survive. |

These are regression tests of the current safety contract, not a promise of
general exactly-once remote execution. Uncertain dispatched work is not
automatically replayed. In particular, the detached store currently refuses
startup on malformed active JSON; the corruption case verifies non-destructive
failure and explicit recovery of a still-intact checkpoint, **not** automatic
repair or recovery of bytes destroyed by disk corruption.

The task evaluator uses the real typed service adapters, pure transforms,
approval/preview admission, scheduler and persisted receipts. Its restricted
host replaces model and research dispatch with fixed fictional data and
denies every other effectful service. The scripted model checks actual bound
inputs and rendered prompts. It scores semantic outputs independently of
the run's completed status and records dispatched steps, cost, retries, and
wrong-side-effect count from the independent host action log. Duplicates,
unexpected steps/services and wrong request inputs are counted as wrong
effects; missing expected calls also fail the task.

| Offline task | Success | Dispatched steps | Simulated USD | Retries | Wrong effects |
| --- | --- | ---: | ---: | ---: | ---: |
| Research → cited summary | true | 2 | 0.03 | 0 | 0 |
| Table → chart → report | true | 3 | 0.02 | 0 | 0 |
| Conditional repair | true | 2 | 0.04 | 1 | 0 |
| Mapped summaries | true | 4 | 0.06 | 0 | 0 |

These are expected fixture baselines, asserted by pytest; the CLI emits the
observed metrics as JSON. Charges are synthetic ($0.02/model call,
$0.01/research call), not provider invoices. A retry is a dispatched attempt
beyond the first or an explicit host-orchestrated repair iteration. The
repair case validates the output and admits up to two separate frozen repair
runs, while mapping uses explicit fan-out and fan-in bindings. These fixtures
exercise those DAG-based patterns, not the native control-flow adapters
(including Map) documented above. Tests also exercise immediate success, repair exhaustion,
wrong model outputs and forbidden actions.

### Optional local model comparison

```sh
python -m backend.evals.workflow_tasks --live --model qwen3:8b --model llama3.2:3b
```

Choose exact installed Ollama tags on a numeric loopback endpoint
(`OLLAMA_HOST`, default `http://127.0.0.1:11434`). No models are downloaded,
and no research or other effectful services become live. The command
requires explicit `--live` and model selection, rejects cloud-backed tags
and CI environments, and runs the same task scorers with real local model
replies. It records each model tag, installed content digest (checked before
and after the run), and USD price basis alongside the scripted baseline.
Local API billing is zero; hardware and electricity are not estimated.
The live scores are report-only, with strict output-format scoring rather
than an LLM judge. Only the deterministic baseline controls the exit status
for task quality. Provider/metadata failures exit nonzero. Redirect stdout
to a file to retain a comparison.


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
For presets, the duration cap includes both effect-approval waiting and
explicit control-flow gates; shutdown interrupts rather than replaying pending
or running steps. A preset Map must have every child effect and execution lane
permitted by its unattended policy; per-item approval is not supported. Use a
manual detached run for Maps with children that require fresh approval. The normal run store retains the receipt.
The portable library remains pure-only; the operator preset API also accepts
a local typed detached spec plus its typed parameters (currently text, JSON,
and table only). This does not enable adapters without a live host or expand
their existing capabilities. For a built-in starter, review its typed request in
the detached inspector and use the local detached workflow spec option for a
preset rather than the portable library revision.

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
even after its receipt is pruned. The run ID remains reserved in that workspace
until the workspace is deleted; an expired receipt itself cannot be read.
The migration preserves operation IDs still present in existing run history;
IDs already pruned by an older release cannot be reconstructed.
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

### Operator library history reads

The existing authenticated `GET /api/workflows/{workspace_id}/runs` and
`GET /api/workflows/{workspace_id}/presets` responses remain full records by
default. The library requests `?summary=true` for display-only metadata: run
identity, name, status, times, cost, preset ID and waiting-for-approval flag;
preset identity, name, workflow name/ID, trigger, cost cap and allowed effects.
Neither summary includes inputs, outputs, events, approved preset requests or
approval nonces. Use the exact run detail route for review and approval. The
summary history applies the existing age/count visibility rule without deleting
rows on each poll; admission, completion and offline GC still perform retention
cleanup. Active runs remain visible even beyond the history limit.

Run summaries refresh every 2 seconds on the Runs tab when a run is active,
otherwise every 15 seconds. Preset summaries refresh on navigation and preset
mutations, and every 15 seconds only on the Presets tab. Full run detail is
loaded when selected and refreshed every 2 seconds only while active; closing
the detail or switching tabs cancels its poll. The separate approval notification
endpoint keeps its existing polling cadence.
