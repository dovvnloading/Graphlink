# First-class Model nodes and dependency-aware local Graph Runner

Status: implementation slice, gated on CI and live Ollama smoke testing.

## Behavior

Graph Runner now recognizes a first-class **Model** canvas node with a named
`text` input and `text` output. It is distinct from a plain note's saved
model binding and from legacy structural/reference edges.

1. In **Plugins → Graph Runner**, choose a chat, plain note, Drafter
   document or an existing Model output as source. Enter an installed Ollama
   model ID and choose **Create Model node**. This saves a typed, versioned
   port without sending a request. Drag the text-output port on another card
   onto the Model's named text-input port to rebind it with cycle/type
   validation. Repeat from the first Model output to build a sequence. A
   note's existing word-count/copy transform may consume the final Model
   output.
2. Choose **Preview run** for the graph or a selected branch. A
   `Source → Model A → Model B → word-count note` plan shows the first
   Model as ready and its dependents as waiting. It cannot predict the
   model's text and never sends requests during preview.
3. Choose **Run graph** to explicitly admit the bounded plan. Independent
   ready steps now run concurrently (default three in flight); each dependent
   still waits for all its prerequisites to complete. Each Model step
   checkpoints its own unique request ID to SQLite **before** contacting
   loopback Ollama. Its real reply and completion receipt are checkpointed
   atomically before a dependent consumes the result.
4. Reopen the saved workspace to inspect previous outputs and request IDs.
   Model node IDs and input-port references are remapped and validated.
   Editing a source marks its dependent models stale without re-running.
   An uncertain request is not sent again on resume, while independent
   deterministic branches can still complete.

## Constraints and safety

The v1 Model has exactly one named, versioned `text` input and one `text`
output. Compatible sources are chats, plain notes, accepted Drafter documents
and other Model nodes. Connecting a dependency cycle is rejected before a
live mutation; graph ancestry/visual edges are independent.

At most 8 Model operations are allowed in one graph run. Only the explicit
local Ollama adapter is enabled. The existing numeric-loopback transport
ignores proxies/ambient credentials, rejects redirects and known cloud
variants and bounds request and response sizes. Ollama aliases may
themselves route to cloud; choose genuinely downloaded models with remote
routing disabled.

A **preview token** pins the endpoint, selection, input/configuration and
destination; a remote call cannot start before a durable request fence. A
result is discarded if the source, Model port or output changed meanwhile.
Output and provenance are written with the committed receipt. Crashes,
timeouts, cancelled calls and failed terminal persistence never automatically
resubmit external requests. An unreconciled request blocks new attempts at
that destination. No paid-provider, write-capable tool, unreviewed approval,
autonomous trigger, or cross-provider profile is authorized in this slice.

Workflows with other kinds continue to use their existing deterministic
Graph Runner semantics. More advanced type ports, reusable subgraphs,
remote provider approvals/budgets and reliable remote reconciliation are
separate roadmap gates.

## Tests

```bash
pytest -q -n0 backend/tests/test_first_class_model_runner.py backend/tests/test_graph_model_bindings.py backend/tests/test_graph_runner_intents.py backend/tests/test_graph_model_intents.py
cd web_ui && npm run check
```

The offline integration tests substitute the local model transport while
driving the real SessionBus and SQLite checkpoints. Live end-to-end inference
requires an installed local Ollama model and running daemon. This is not
equivalent to a proven cloud or tool-using graph agent.

## Parallel scheduling and crash recovery

The `executeGraphRun` intent accepts an optional fourth positional argument,
`maxConcurrency`, an integer from **1 to 8** (default **3**). The limit is
saved with the run and reused by `resumeGraphRun`; existing three-argument
callers keep the default. The eight-Model-steps-per-plan cap remains unchanged.
Current typed Model, note and table bindings have a single named input; the
scheduler also accepts a multi-prerequisite dependency relation for future
fan-in step types.

A step is eligible only when its scheduled prerequisites are `current` or
`completed`. Ready siblings run concurrently up to the cap, with stable
preview-order admission. A blocked, failed or reconciliation-required step
blocks only downstream dependents, not unrelated branches. Checkpoints are
serialized even though external requests overlap: a **confirmed SQLite fence**
is still required before every model send, and a completed response and receipt
still share a transaction. Two model calls in flight at process death therefore
restore as **two distinct reconciliation-required attempts**, never automatic
retries. Remaining safe independent pending steps may be resumed.

Step `startedAt` is captured when its worker actually begins and `finishedAt`
when it resolves, not when it was queued. Overlapping timestamps are expected;
blocked descendants that were never dispatched have no fictional start time.
See [the inspector guide](GRAPH_RUN_INSPECTOR.md) for saved timing fidelity.

Offline backend checks include bounded-concurrency (default and custom), fan-out,
fan-in scheduling, branch failure, multi-step cancellation, and SQLite restart
with two external requests simultaneously fenced. Run:

```bash
pytest -q -n0 backend/tests/test_first_class_model_runner.py backend/tests/test_graph_runner_domain.py backend/tests/test_graph_runner_intents.py backend/tests/test_graph_run_external.py backend/tests/test_graph_run_inspector.py
```
