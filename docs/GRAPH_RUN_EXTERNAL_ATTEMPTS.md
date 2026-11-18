# Graph Runner external-call journal (implementation slice)

This describes the first C2 step after the local Graph Runner in PR #577.
**It does not yet enable model or tool nodes in the Graph Runner UI.**
The current executable dataflow remains local and deterministic.

## Dispatch contract

A future model/tool step must supply an **already-authorized** `ExternalCall`
to `dispatch_external_attempt` in `backend/domain/graph_run_external.py`.
The caller must:

1. Pin an immutable input artifact/version and its SHA-256 fingerprint.
   A mutable node ID or a prompt reconstructed after a restart is not enough.
2. Resolve a specific provider profile and model or tool, and check the actual
   permission, approval, local-only policy, and spending constraints **before**
   entering the helper. The helper does not authorize or execute by itself.
3. Hold the graph mutation guard and supply a checkpoint callback that saves
   the graph and run journal in the **same durable SQLite transaction**.
   The callback must return `True` only after a confirmed commit; `False`
   or an exception forbids dispatch.
4. Invoke the selected adapter exactly once using the recorded request ID.
   An adapter may pass that ID as an idempotency key **only if its provider
   explicitly supports the appropriate semantics**. Recording the ID does not
   imply that the remote service is idempotent.
5. Apply the result in memory and return its output artifact digest. The
   helper checkpoints the output and the completed receipt atomically.

The persisted `externalAttempt` record contains `requestId`, `kind`,
`provider`, `model` or `tool`, `inputRef`, `inputHash`, and `phase`.
It deliberately does **not** contain credentials, raw prompts, tool arguments,
or provider responses. `inputRef` must identify an immutable artifact,
not a raw filename or user-authored free-text prompt.

## Recovery rule

The pre-dispatch fence is written in phase `dispatched` **before** the
network call. A crash after that checkpoint can leave the remote outcome
unknown, including if the process died before the actual send. Restart
therefore maps this phase to `reconciliation-required` rather than
`pending`. A timeout, cancellation, or failure to checkpoint the returned
output also requires reconciliation. The request ID is shown in Graph Runner
so a human or a future provider-specific status lookup can inspect the job.

Only a committed `completed` receipt proves that the local output exists.
An uncertain request is never automatically dispatched again; it also cannot
be cleared merely by resuming the run. A provider-specific reconciliation
flow must prove the outcome or explicitly request a fresh authorization
before any new attempt. Completed deterministic local steps retain their
existing checkpoint-and-resume behavior.

## Remaining work

- Introduce typed model/tool graph nodes and immutable input-artifact refs.
- Connect authorized provider and tool adapters to the dispatch fence.
- Add explicit human approval for write-capable or billable operations, with
  separately tracked authorization and spending records.
- Implement provider-specific remote status reconciliation and an explicit
  recovery decision; some providers cannot report by client request ID.
- Exercise actual adapter calls under process death, races, and disconnects
  before allowing them to execute from the Graph Runner UI.

Tests in `backend/tests/test_graph_run_external.py` cover call ordering,
failed pre-dispatch persistence, timeout, cancellation, failed completion
persistence, malformed metadata, and save/reopen through the real graph DB.
The UI test checks that an uncertain request reveals its ID without a
one-click Resume action.

## Concurrent Graph Runner calls

Graph Runner can now have several independent steps in flight (default three;
explicit `maxConcurrency` from one through eight). This does not weaken the
external-call contract: each concurrently running model step obtains a distinct
request ID, and the shared graph/checkpoint writer serializes SQLite saves.
No call is sent until **its own** pre-dispatch fence has been confirmed durable,
and every output is saved with its corresponding completed receipt. One uncertain
step prevents dispatch of its dependents but does not stop siblings. Cancelling
the parent run cancels and drains every active step before releasing its graph
mutation guard. After a restart, all saved `dispatched` attempts are still
reconciliation-required, even if multiple requests were in flight; only
never-dispatched independent steps can resume automatically.
