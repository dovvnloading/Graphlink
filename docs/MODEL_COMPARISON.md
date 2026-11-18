# Model comparison on the chat canvas

One composer message can be sent to **two, three or four** configured text
models at the same time. Comparison is opt-in; the existing single-model Send
intent, active model selection, reasoning control and regenerate behavior are
unchanged.

## Use it

1. Open **Compare** in the chat composer. It lists locally configured
   Ollama models, the configured llama.cpp GGUF slots, and models belonging
   to configured cloud profiles (including cached endpoint catalogs).
2. Select **2–4 different provider/model pairs**. Repeated model IDs from
   different providers count as different routes; identical pairs cannot
   appear twice. At four choices additional choices are disabled until one
   is deselected.
3. Choose **Done**, write one prompt and **Send message**. An explicit
   **Branching from** target, if selected, is used for this one user turn.
4. The canvas creates a single user node and a sibling reply node for each
   model, all sharing that user node as parent. The replies stream
   independently on the **scene** stream channel and show each model on the
   node badge/title.
5. Each finished node contains its elapsed request latency, reported prompt
   and completion counts (or **estimate unavailable**), and the cost estimate
   computed from the existing pricing/overrides (or **estimate unavailable**).
   Cost is an estimate derived from provider-reported usage, not a billing
   receipt. No fallback guessed token counts or pricing are presented as
   actual usage. A failed stream preserves partial text; a canceled stream
   leaves the other siblings running.
6. Click **Use single-model send** in the Compare picker to return to the
   ordinary composer workflow. Follow-up branches and Regenerate continue
   using the existing ChatNode controls.

## Routing, budgets and safety

Model choice uses the existing provider/model identifier pair,
\`ModelRef(provider, model_id)\`. The read-only
\`scene/listComparisonModels\` intent exposes only display identifiers,
not saved API keys or endpoint credentials. \`scene/compareModels\` checks
the 2–4 distinct pairs against that configured list *before* consuming
staged attachments or writing any nodes. Unconfigured profiles, duplicate
choices and invalid cardinalities are rejected.

Each sibling gets an isolated \`ProviderRuntime\` snapshot built with the
existing client-construction and endpoint/credential checks. A saved cloud
profile can run concurrently with Ollama or llama.cpp and with another saved
cloud profile without changing the session's active provider. llama.cpp
selection is restricted to the actually configured GGUF chat/title paths;
scanned-but-not-configured files cannot be run.

The ordinary \`AgentDispatcher.start_chat_reply\` method handles *every*
sibling. It resolves the branch system prompt, node model override and
workspace policy; invokes \`ChatWorker\`; and uses the existing
\`ContextAssembler\` and selected model's context window. The existing
streaming, cancellation, request watchdog, retries and provider exception
handling are not skipped. No separate direct-to-provider endpoint or
unreviewed budget/approval shortcut is introduced.

Each sibling has its own busy-guard bucket in the *session's existing*
\`RunRegistry\`. This is important: a failure or user Stop cancels only
that sibling, while disconnect cancellation, diagnostics and session
liveness still see *all* in-flight comparison jobs. The node-scoped Stop
uses the existing \`scene/cancelChatRequest\` intent. A replacement
request/late finalizer cannot overwrite a newer node request ID.

## Persistence, undo, and availability

The shared user message and initial sibling nodes are created through
\`SceneDocument.record_command\`. The response metadata and content
update the assistant nodes' redo snapshots as generation finishes.
The model identity is stored in the existing chat-node provenance and
override fields; token counts and estimated cost use the existing fields.

The node's ordinary Markdown content includes latency and explicit
unavailability text. This intentionally avoids changes to the shared
SceneNode wire schema, generated types or the reusable ChatNodeView,
outside this feature's narrow file scope. The displayed cost is not
asserted unless usage was reported and the pricing table knows the rate.

A comparison send is never queued for offline replay, because doing so
could repeat billed API work. An unsuccessful send keeps the draft and
staged branch target. UI availability depends on cached/local settings;
if a provider becomes unreachable after selection, only that model's
node fails, and the others continue.

## Intent registry and test coverage

- \`scene/listComparisonModels\` — **B**, read-only configured-catalog query.
- \`scene/compareModels\` — **A**, creates user and assistant chat nodes
  using undoable commands.
- \`scene/cancelChatRequest\` — existing **B** lifecycle intent, extended
  only to recognize comparison-owned chat handles; ordinary chat stays on
  its original \`chat\` run kind.

Regression tests cover selection validation, runtime isolation, simultaneous
streaming, independent error and cancellation handling, per-node usage/cost,
the comparison picker and the original single-model Send path.
