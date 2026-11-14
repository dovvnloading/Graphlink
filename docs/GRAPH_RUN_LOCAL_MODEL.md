# Graph Runner: explicit local model step (first model execution slice)

This slice builds on the crash-safe external dispatch journal. It adds an
**opt-in Ollama model call** from a saved text node into an existing plain note.
Graph Runner can now also save that text-to-text model connection across
workspaces, track freshness and rerun it explicitly. It does not yet introduce
a standalone Model node, an automatically scheduled model/dataflow DAG,
independent cloud credential profiles, paid-provider approvals, or tool
execution. See [the persisted binding contract](GRAPH_RUN_MODEL_BINDINGS.md).

## Try it

1. Start Ollama locally and install a chat model, for example
   `ollama pull qwen3:8b`. Ensure `OLLAMA_HOST`, if set, points to
   `localhost`, `127.0.0.1` or `[::1]`.
2. In Graphlink, create a chat message or plain note with the input text
   and a second **unbound plain note** to receive the answer.
3. Open **Plugins → Graph Runner**, expand **Run a local model into a note**,
   choose the source and output note, and type the exact installed model ID.
4. Optionally choose **Save model connection**. This only saves the typed,
   versioned text-to-text connection; it does **not** run Ollama. After saving
   and reopening the workspace, select it from **Saved model connections**.
   You can disconnect it without deleting the existing output text.
5. Choose **Preview model step**. Review the selected source, destination,
   model and non-secret input fingerprint.
6. Choose **Run local model**. A request fence is checkpointed in SQLite
   before Ollama is called. The model's response and the source/output
   provenance become one undoable edit, saved with the completed receipt.

The preview does not send a request. Nothing runs automatically when a source
is edited. Only local Ollama endpoints are accepted, and `localhost` is
canonicalized to a numeric loopback address (never resolved through DNS).
The dedicated HTTP transport refuses redirects, ignores environment proxy
settings, sends **no credentials or inherited Ollama API key**, and bounds the
expanded response body to 2 MiB. Graph Runner rejects recognizable `-cloud`
and `:cloud` model IDs, but **cannot verify whether a custom Ollama alias
is cloud-backed**. For fully local inference, run a downloaded local model
with cloud routing disabled in the Ollama daemon.

## Durability and failure behavior

Graph Runner persists source and destination node identity, selected Ollama
model, a versioned input fingerprint and a generated request ID **before
dispatch**. A pending remote attempt that may have been sent is not replayed
on restart, transport timeout, cancellation, or failed completion checkpoint.
The inspector exposes a reconciliation-required state and request ID; it
never shows the private prompt in the run history.

Completed model replies and the corresponding command's undo state are saved
in the graph's existing SQLite transaction. An output can be inspected,
saved, reopened and explicitly undone. Repeating a transport message using
the original operation ID returns the saved result rather than creating
another Ollama request. An unresolved request blocks new attempts to
overwrite the same destination. If the source or output note changes while
the model is responding, the generated response is not applied over the
newer text; the attempt remains available for inspection.

This local-only transport sends one HTTP request per admitted attempt and
does **not** auto-retry a failed call. An interrupted client cannot prove
whether the daemon completed the work, so the journal conservatively marks
the outcome uncertain. That behavior must not be generalized to paid
providers or mutating tools without additional authorization and remote
reconciliation.

## Next steps

The first typed, versioned model-to-note binding is now saved and reloadable;
next add multi-step planning for model outputs without speculative preview text;
enforce separate per-call provider profiles and budgets; add explicit
approval and remote-side idempotency/reconciliation for paid services; add
write-capable tools with their own consent and isolation rules.

Offline backend tests use a substituted model transport to prove checkpoint
order, pinned model identity, preview invalidation, failed requests,
replay, real SQLite load/save and cancellation. HTTP transport tests verify
no inherited credentials, no proxy/redirects, response limits and JSON
shape. The frontend unit test
exercises explicit preview and execution controls without contacting Ollama.
