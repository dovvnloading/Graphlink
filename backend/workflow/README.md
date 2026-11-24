# Workflow contracts (v1)

This package contains the versioned workflow contracts and step-adapter
registry. Graph Runner's scene/journal execution is hosted here by
compatibility adapters, without changing canvas, plugin execution, persistence
or WebSocket behavior. The typed WorkflowSpec is still a foundation for
later execution work, not a replacement for the existing scheduler.

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

## Graph Runner compatibility adapters

`StepAdapterRegistry` pins implementations by exact ID and version. Legacy
`graph_steps` entries select v1 by existing step kind: note (copy and word
count), table/chart/artifact (group sum), model (first-class Model), scientific
and tool (MCP Connect). The opt-in model output note has its own
`model-note` entry; it is never discovered during an ordinary preview.
The compatibility interface does not convert the old scene journal into
WorkflowSpec or change the typed StepAdapter protocol. No adapter/version
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
