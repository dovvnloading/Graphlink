# Graph Runner: explicit local model step (first model execution slice)

This slice builds on the crash-safe external dispatch journal. It adds an
**opt-in one-shot Ollama model call** from a saved text node into an existing
plain note. It does not yet introduce a reusable Model node, a model/dataflow
edge, independent cloud credential profiles, paid-provider approvals, or
tool execution.

## Try it

1. Start Ollama locally and install a chat model, for example
   `ollama pull qwen3:8b`. Ensure `OLLAMA_HOST`, if set, points to
   `localhost`, `127.0.0.1` or `[::1]`.
2. In Graphlink, create a chat message or plain note with the input text
   and a second **unbound plain note** to receive the answer.
3. Open **Plugins → Graph Runner**, expand **Run a local model into a note**,
   choose the source and output note, and type the exact installed model ID.
4. Choose **Preview model step**. Review the selected source, destination,
   model and non-secret input fingerprint.
5. Choose **Run local model**. A request fence is checkpointed in SQLite
   before Ollama is called. The model's response becomes an undoable edit
   of the destination note. The output and completed receipt save together.

The preview does not send a request. Nothing runs automatically when a source
is edited. Only Ollama endpoints that resolve to the loopback hostname
configured by `OLLAMA_HOST` are allowed in this slice. The backend enforces
this restriction; merely calling a provider "local" would be insufficient
if `OLLAMA_HOST` were pointed at a cloud or LAN endpoint.

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
overwrite the same destination.

This local-only feature does **not** promise exactly-once inference inside
the Ollama SDK: some model implementations or transport retries can retry a
read-only completion internally. It deliberately does not extend that
behavior to paid providers or mutating tools.

## Next steps

Turn the one-shot model step into an explicit typed, versioned graph binding;
add multi-step planning for model outputs without speculative preview text;
enforce separate per-call provider profiles and budgets; add explicit
approval and remote-side idempotency/reconciliation for paid services; add
write-capable tools with their own consent and isolation rules.

Offline backend tests use a substituted model transport to prove checkpoint
order, exact ModelRef selection, preview invalidation, failed requests,
replay, real SQLite load/save and cancellation. The frontend unit test
exercises explicit preview and execution controls without contacting Ollama.
