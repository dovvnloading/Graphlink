# Workflow contracts (v1)

This package deliberately does not change the Graph Runner, canvas, plugin
execution, persistence or WebSocket behavior. It is a foundation for later
work, not an executor, permission system or cache.

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
  Validation of graph structure (duplicate IDs, port references, missing
  required inputs, cycles), authorization and scheduling belong to later work.
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


## Built-in Transform steps (adapter version 1)

Use `backend.workflow.registry.builtin_registry()` to construct a fresh
version-pinned registry. `registry.describe(id, "1", freeze_config(config))`
returns the typed descriptor to store in a step spec; `registry.resolve(id, "1")`
returns the adapter. Unknown IDs/versions and duplicate registrations fail;
there is no version fallback. The registry is not connected to the live runner.

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
