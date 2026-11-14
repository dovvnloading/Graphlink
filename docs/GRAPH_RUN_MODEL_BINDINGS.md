# Graph Runner — saved local-model text connections

This is the first **reusable text-to-text model binding** on the existing Graph
Runner, following the crash-safe explicit Ollama step in PR #579. A plain note
can keep one local model input: the source node, provider, model identifier,
version 1 port contract and opaque binding ID. The input/output port types are
both `text`. This is **not** a standalone Model node, a model-to-model
scheduler or a tool-execution permission.

## User flow

1. Create a chat message, plain note or accepted Drafter document containing
   the prompt; create a separate plain note for the output.
2. In **Plugins → Graph Runner → Run a local model into a note**, choose the
   source, output and a locally installed Ollama model ID. Select **Save model
   connection**. The UI now shows its `unrun` status. No provider request occurs.
3. Select **Preview model step** then **Run local model**. The existing durable
   external-request fence runs before the loopback Ollama call. The answer
   becomes the note content, and its source/output SHA-256 provenance commits
   with the output in the same undoable mutation and SQLite checkpoint.
4. Edit the prompt. The connection becomes `stale`; the previous answer
   remains available. Editing the generated note instead makes a current
   result `edited`. Neither edit launches inference.
5. Reopen the saved graph. Select the prior connection from **Saved model
   connections**. Preview and explicitly run it again if appropriate.
   **Disconnect model connection** leaves the note text intact.

The binding is independent of the canvas's legacy visual/structural edges.
It is validated on connection and load, and rejects cycles across existing
note-copy/word-count bindings. Deleting a source disconnects surviving
readers, and undo restores their connection. Invalid or unknown persisted
binding versions are dropped rather than interpreted or executed. Old
workspaces without `note_model_inputs` load unchanged.

## Concurrency, permission and recovery

A binding is configuration, **never a standing consent to run**. Only local
Ollama with an explicitly chosen model is accepted, retaining the existing
loopback-only, no-proxy/no-redirect and bounded-response transport. A new
request requires a fresh preview and explicit Run. The preview includes the
saved binding ID, so changing/rebinding the target invalidates it. In-flight
output is refused if the source, output text, or saved binding changed; an
uncertain remote request remains in the durable journal and is not replayed.
Undo/redo restores the result and its provenance together.

Current deterministic note transforms refuse to consume an unrun/stale/edited
upstream model output. The deterministic Graph Runner continues to schedule
only local transforms. It does **not** invent preview text for a model step
or automatically run downstream steps; model steps must still be run
individually and explicitly. Saved bindings do not bypass per-call approval
and budget policies that future cloud/provider/tool adapters will require.

## Validation

```bash
pytest -q -n 0 backend/tests/test_graph_model_bindings.py backend/tests/test_graph_model_intents.py backend/tests/test_graph_runner_domain.py
cd web_ui && npm test -- --run src/app/chrome/GraphRunnerDialog.test.tsx
```

Backend coverage checks model binding validation and cycles, source deletion
and undo, strict saved-version/ID remapping, output provenance and freshness,
explicit execution with no implicit provider call, and rebinding during an
in-flight attempt. The frontend test exercises connection save, selection,
disconnection and explicit execution. Live Ollama still requires an installed
local model and daemon.

**Follow-on milestone:** Add a versioned first-class Model node/port to the
shared graph contract and a multi-step execution planner that suspends
dependent steps until an actual model receipt exists. Then add separate
provider profiles, budgets, paid-call approvals and provider-specific remote
reconciliation before any remote or write-capable execution.
