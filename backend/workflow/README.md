# Workflow contracts (v1)

This package contains the versioned workflow contracts and step-adapter
registry. Graph Runner's scene/journal execution is hosted here by
compatibility adapters, without changing canvas, plugin execution, persistence
or WebSocket behavior for existing step kinds. The typed WorkflowSpec also
pins pure reusable library definitions; the existing scheduler admits them
as single Workflow steps.

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
  Reusable library definitions validate graph structure (duplicate IDs, port
  references, missing required inputs, cycles) before execution.
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
service dependencies; typed transforms are dispatched only inside validated
pure reusable Workflow definitions.

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

Workflow Library is global to the local chats database (migration 7). A saved
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

All eight built-in pure registry transforms are supported for imported
definitions. Canvas capture and editable unpack currently cover the existing
plain-note text adapters; imported definitions using other transforms can run
as pinned nodes but cannot yet be unpacked as native editable canvas
operations. Model, scientific, MCP and other external/effectful steps retain
their existing runner and approvals and cannot be captured by this library.
