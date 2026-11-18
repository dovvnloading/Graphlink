# Graph Runner: saved-run inspector

## Where it lives

Open **Graph Runner** and expand **Inspect run trace** under the latest
saved run or any item under **Earlier runs**. Steps appear in dependency
order, with a separate expandable entry for each saved destination. Clicking
a step selects its destination node on the canvas, when that node still
exists. The node IDs are persisted/remapped run references; the titles shown
beside them are the *current* canvas titles, not historical title snapshots.

The inspector shows source and destination, operation, pinned provider/model
identity where recorded, the input/configuration/destination-before/endpoint
SHA-256 fingerprints, output version, status, classified blocked cause,
timing, and external attempt history. Each run has an **Export trace JSON**
button. There is no extra intent: the existing `getGraphRun`,
`executeGraphRun`, and `resumeGraphRun` responses include a `trace`
projection of the saved run journal.

## History and fidelity

The source of truth is `SceneDocument.graph_runs`, already persisted with
the graph in the same SQLite checkpoint. The inspector never reads prompts,
output bodies, provider APIs, or live execution logs to reconstruct history.

- The saved steps are stably topologically ordered using their persisted
  `sourceId` and `nodeId`. Cyclic legacy/corrupt entries are retained at
  the end rather than assigned a fictional dependency order.
- A SHA-256 `outputHash` is a **content-addressed output version**. Where
  the step writes a table projection, the existing table `current.version`
  number is also captured at the step checkpoint. A step that was already
  current records its content fingerprint without rerunning it.
- `startedAt`, `finishedAt`, and `durationMs` are actual journaled UTC
  times for newly executed steps, and the run's terminal checkpoint records
  its end time. Existing older records, unexecuted steps, or an incomplete
  historical checkpoint show **Not recorded**, never made-up elapsed times.
- `externalAttempt` already pins a single request ID, provider/model/tool,
  immutable input fingerprint, and persisted phase per step. The inspector
  exposes that saved attempt as history; Graph Runner never automatically
  retries it. A restored `dispatched` fence is displayed with an effective
  **Reconciliation required** outcome and its *original saved phase* remains
  visible. It must not be mistaken for a completed or safely retryable call.
  New attempts carry their step start/end timestamps where checkpointed.
  Separate executions of a node belong to separate saved runs.
- `blockedCause` is a conservative **fixed-category classification** of the
  saved validation detail. It avoids exporting arbitrary exception strings,
  so a rare unrecognized error is categorized generically. The existing
  Graph Runner summary still shows the original detail in the UI.

## JSON privacy boundary

`backend/domain/graph_run_inspector.py` constructs a versioned, **explicit
allowlist** from persisted metadata. `web_ui/src/app/chrome/graphRunTrace.ts`
constructs the download from a *second* strict allowlist. Neither layer
serializes the original run/step object or uses a catch-all spread, and
neither exports user-authored labels, free-form error details, immutable
input references, endpoint addresses, node content, model prompts, model
output text, tool payloads, or credential fields.

The export contains only:

- Run ID, status, start/end timestamps, derived duration, and schema version.
- Ordered step index, source/destination node IDs, kind, operation,
  validated provider/model IDs, fixed status and blocked cause.
- SHA-256 input/configuration/previous-destination/endpoint hashes and
  content-addressed output hash; optional table output version number.
- Validated external request ID, kind, provider/model/tool identifiers,
  input digest, effective and originally persisted phase, and recorded times.

The provider/model names are **identifiers**, not API keys or client
configuration; identifiers are validated against a bounded character set.
If an imported record fails validation, the corresponding field is omitted
or marked unavailable. The exported file is named
`graph-run-<run-id>.trace.json`. Export never triggers an execution, retry,
provider request, or mutation.

## Checks

Backend:

```sh
pytest -q backend/tests/test_graph_run_inspector.py backend/tests/test_graph_runner_intents.py backend/tests/test_graph_run_external.py
```

Web UI:

```sh
cd web_ui
npm run typecheck
npx vitest run src/app/chrome/graphRunTrace.test.ts src/app/chrome/GraphRunnerInspector.test.tsx src/app/chrome/GraphRunnerDialog.test.tsx
```

The backend projection test injects secret credentials, a prompt, output
text, misleading labels/details, and an input reference and verifies none
appear in the exported JSON. The frontend serializer test repeats the
adversarial case even if the server-projected trace carries unexpected
extra fields. The SQLite reopen test proves ordered steps, hashes, versions,
and timestamps remain inspectable after restart.
