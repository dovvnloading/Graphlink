# Conversation Node — architecture review and modernization

Status: November 2026, first integrity and long-session UX increment.

The `plugins/conversation_node` folder is only the SDK registration shim.
The production path is `ConversationNodeView` -> `sceneStore` ->
`backend/api/intents_conversation.py` ->
`backend/agent_dispatch/chat.py` / `core.py` ->
`SceneDocument.history` -> session save/reload and the scene wire.
The built-in chat Composer and Conversation Nodes share a **single** chat
run slot, and Conversation Nodes use a message list inside one graph node.

## Findings and fixes in this increment

1. **Unsent messages accidentally persisted.** The send intent used to
   append a user message and record an undo command BEFORE calling the
   dispatcher. If any other Composer/Conversation request was running, or no
   provider was configured, the dispatcher refused the request but left a
   durable *phantom* message that the model never received. The new dispatcher
   has a synchronous post-claim hook; only an actually admitted turn mutates
   conversation history. The accepted history is copied for the provider
   before asynchronous work starts. The check-to-claim stretch remains
   await-free; the existing AST race gate covers it.

2. **Replies attached to changed histories.** Deleting a message or
   undoing a send while the model was responding did not invalidate the
   reply. It could append an answer based on a different conversation or
   even a newly restored node under the same ID. The new callbacks
   require node-object identity, an active run, and an exact history
   snapshot. Late replies to cancelled runs are rejected at the shared
   dispatcher boundary too, before their success callback.

3. **Lost user drafts after a send refusal.** The UI had a fire-and-forget
   `sendConversationMessage` and immediately emptied the composer. It
   couldn't distinguish an accepted run from an unavailable provider,
   shared busy slot, or disconnected socket. An acknowledged request now
   returns success only when the backend actually claimed the turn. The
   textarea preserves its contents and shows retry guidance on refusal or
   transport failure. A local sending gate blocks repeated clicks while
   admission is pending.

4. **Large histories eagerly rendered.** Every conversation rendered
   every Markdown message in its node card, even with hundreds of messages
   and offscreen virtualized canvas nodes. Render only the most recent
   40 at first, with progressive Show earlier controls. Absolute message
   indices remain intact for deletion; the backend still retains and sends
   the whole history to its existing context-budgeting pipeline, and Open
   Document View still exports the complete transcript.

5. **No per-message input validation.** Raw inputs could be coerced to
   strings (including `None`) and huge user submissions could be saved
   without bounds. New user turns must be nonblank strings of at most
   16,000 characters; deletion indices must be actual nonnegative integers,
   not booleans, strings or floats.

6. **Ignored branch/persona model context.** Conversation dispatch previously
   omitted the canvas node ID/document even though regular chat resolves
   branch-attached System Prompt notes and node/branch/workspace model pins.
   Conversation Nodes now use the same routing and prompt resolution from
   the shared dispatcher rather than silently ignoring those choices.

These improvements preserve the existing message wire shape and session
serialization; no new state or migration is required. The history list is
never silently truncated, messages are not synthesized or erased behind the
user's back, and the app's provider context-trim notification still reports
when older turns are dropped from the **model input**, not saved history.

## Remaining high-value work (separate, gated increments)

**Durable revisions and message IDs.** Replace index-based message identity
with stable opaque IDs, so edits/deletes/retries target exactly the intended
turn even across multi-tab sync, history reordering and reconnect. Migrate
old saved histories carefully. Include per-turn creation and model usage
metadata only after the wire contract is versioned and regenerated.

**Streaming and context economy.** Bound raw stream queues and accumulated
tokens in the shared dispatcher (without breaking Composer/ChatNode), add
explicit context-retention controls (recent turns vs pinned facts vs
summarized older messages), and measure token/latency tradeoffs rather than
guessing. Allow cancellation and retry of the interrupted turn without
resending unrelated instructions. Preserve explicit user choice when
summarizing or pruning persisted history.

**Large-conversation workspace.** Provide a dedicated searchable transcript
view, conversation title/summary, navigation to a message, edit-and-fork
workflow, selectable excerpts for Artifact, and a stronger paginated or
virtualized markdown renderer. Recover unsent local drafts across canvas
virtualization/unmount without persisting secrets to ordinary analytics logs.

**Cross-node concurrency.** The shared chat slot currently makes one
Conversation Node block the Composer and all other Conversation Nodes.
Evaluate scoped per-node run admission with a documented global concurrency
budget, fair scheduling, provider rate-limit handling, and independent
cancel states. Do not simply remove the slot without per-node guards.

**Quality and safety evaluation.** Deterministic tests for quick double
sends, busy Composer, unconfigured provider, delete/undo/reload races,
late cancelled results, multi-tab stale edits, enormous messages,
transcript export parity and model-pinned branch context. Include offline
record/replay scenarios before adding external provider tests.

Each future phase must ship in a separate reviewed PR with full Windows
pytest/Ruff/mypy/coverage/performance/pip-audit, frontend checks, Playwright,
packaging and POSIX checks. Never merge a red or stale CI run.
