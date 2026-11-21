# Context engine (C3 first slice)

## The existing chat prompt path

1. backend/api/intents_chat.py/send_message takes staged attachments, creates
   the user ChatNode, and calls SceneDocument.chat_branch_history(node.id).
   Extracted document text is appended to the user message; image/audio
   attachments are stored as content parts.
2. backend/domain/branches.py/chat_branch_history follows the canonical
   structural-parent chain, NOT arbitrary reference edges, returning
   root-to-leaf role/content history. It preserves multimodal parts.
3. backend/agent_dispatch/core.py/_dispatch resolves the branch-root System
   Prompt note, or the settings persona, and the node/branch/workspace model
   override. A branch note REPLACES the default persona; it is not concatenated.
4. backend/agents.py/_call_chat_agent(_stream) calls ChatAgent. The default
   persona is composed with the Graphlink Assistant introduction, while the
   branch override is handed through raw.
5. graphlink_chat_agent.py/ChatWorker.run estimates the selected model window,
   reserves output tokens, and uses graphlink_memory.trim_history to keep a
   contiguous suffix, optionally summarizing discarded older turns. Before
   api_provider.chat or chat_stream, it routes the final messages through
   ContextAssembler. With room for every source, the wire messages are
   byte-for-byte the old messages.
6. backend/knowledge_retrieval.py has hybrid FTS5/vector retrieval, a token
   budgeter, stable citation IDs (PR #599) and untrusted source formatting.
   Ordinary chat Send, Regenerate and model comparison can search ONLY the current workspace's
   already-existing knowledge collection (with the legacy unscoped pool as
   the fallback for a session without a workspace), but ONLY when the composer's
**Use knowledge** toggle is on. With no override the toggle is on when that
workspace collection contains documents and off when empty. Overrides are
stored on individual chat branches: the nearest ancestor preference wins,
without leaking to sibling forks. The document-presence check is read-only.
Questions are first
   searched as written, then with significant terms if the full question
   has no lexical matches. They do not ingest or
   create a collection. backend/api/intents_knowledge.py still supplies the
   separate explicit search. Other agents retain their existing dispatch.

## Source contract

backend/context_engine/assembler.py defines ContextCandidate (source_id,
origin, trust, kind, message, required) and ContextPlan. Source identifiers
are stable within their namespace; for example node:n17,
knowledge:doc7:chunk4, draft:after:n17. The inspector uses real graph-node
origins; the worker has historical turn-index origins where no node ID was
passed to it. Callers must provide durable IDs for supplemental sources.
Duplicates and invalid trust/kind combinations are rejected.

Kinds are instructions, current_message, branch_ancestor, pinned_note,
attachment, summary and knowledge_hit. Trust labels are system, user,
generated and retrieved. The system prompt and current user question are
required. A user-authored System Prompt note has user provenance, despite
retaining the old system-role API message. Earlier assistant turns have
generated provenance but retain their original assistant role to preserve
the existing prompt shape.

For retrieved and generated supplementary text, callers use
ContextCandidate.text_source or typed_context_message. They yield an
untrusted user-role JSON data envelope whose origin, kind, trust and text
are explicit. HTML-style delimiters in content are escaped, and the
assembler validates the canonical wrapper to reject forged closing tags,
role changes and mismatched metadata. This does NOT grant model tools;
allowing tools must be enforced by the application's tool dispatcher
independently of source text. ContextPlan.allowed_tools comes only from
the trusted caller. A malicious retrieved document cannot add tool names.

Chat retrieval now uses a private, per-request `_knowledge_hits` sidecar on
the last in-memory history turn (backend/api/intents_chat.py). It is NOT
persisted in the user message, does not alter graph branch history, and is
stripped by clone_history before the provider call. This keeps ordinary chat
with no matching knowledge hits byte-identical to its pre-citation prompt.
Existing document extracts and image parts already included in a user
message retain their legacy transport shape.

Each ranked hit becomes its own ContextCandidate of kind `knowledge_hit`,
with stable sourceId `knowledge:kb:<digest>`, retrieved trust, and a typed
JSON reference-data envelope. Its citation ID, title, source file, section
and exact passage travel TOGETHER. The selected context window budgets the
whole candidate; no cropped excerpt or unsent hit is promoted to an approved
citation. If no knowledge candidate fits, the assembler falls back to the
original prompt with no additional citation instructions. The context
inspection sources contain metadata, not the raw passage text. A retrieved
document can never grant provider tools or become a system message.

The trusted system prompt asks the model to cite only stable `[kb:...]` IDs
that appear in supplied passages, inline with the supported claims. The
prompt instruction is conditional on at least one knowledge passage actually
being selected, and does not contain a made-up valid example ID.

### Reply citation provenance and validation

The provider result remains a string. When knowledge was included, ChatWorker
returns a `CitedChatReply(str)` with a separate, trusted, in-memory snapshot
of EXACTLY the selected passages. The model itself controls only answer text;
it cannot introduce source title, URL, section, offset or inclusion claims.
The backend parses inline `[kb:...]` IDs and marks them as verified ONLY
when they occur in that selected set. A hallucinated, malformed or never-sent
ID has no approved source to link to, and appears as **Unverified** instead.
A retrieved passage the model never cites is tracked as *Not cited*.
Repeated citations refer to one source, not multiple duplicated chips.

The existing chat node's `tool_invocations` persistence/wire path carries a
reserved `graphlink.knowledge.citations` metadata row with the included
source excerpts, cited flags and unverified IDs. This adds no graph schema,
wire contract, migration or dependency. Its ID/name distinguish it from real
tool calls, and the chat card hides it from the normal tool-call disclosure.
Send, Regenerate and parallel model-comparison replies all attach metadata
in the same command scope as the reply content, so undo/redo and save/reload
retain the citation ledger. Regenerate replaces an old ledger instead of
reusing stale source provenance.

In ChatNodeView, citation chips render under the assistant's markdown. Each
verified chip opens the exact saved excerpt with its document title, source
file, section and offsets; an HTTP(S) source also exposes a hardened external
link to the section-heading fragment. A local document opens its exact
source section in the in-app excerpt rather than fabricating a filesystem
URL. Unverified IDs appear as non-clickable labels. The chip renderer
cross-checks the inline ID against the server-authored ledger and never
constructs a source link from a model-written URL.

## Budget and priority

1. Use the selected model's actual existing window estimator, including a
   node/branch model pin. Ollama uses its cached served context size,
   llama.cpp its configured n_ctx, cloud models the existing model-family
   table, and lookup failures the ChatWorker fallback of 8,000 tokens.
2. Reserve min(4096, context_window // 4) for output. Prompt capacity is
   the remaining window. Every source cost uses graphlink_memory.message_tokens
   and the shared graphlink_token_estimator.TokenEstimator, including vision
   cost for image dimensions. Token counts are estimates, not provider bills.
3. Mandatory system instructions and current user message are never excluded.
   If the mandatory plain-text user message exceeds the remaining budget,
   deterministically retain its longest fitting prefix and append a
   truncation explanation if it fits. Never trim the system prompt.
   Impossible-to-fit mandatory image/media or system/current overhead raises
   ContextBudgetError before dispatch.
4. Select optional user-pinned notes, then explicit attachments, then
   generated summaries, then contiguous branch ancestors newest-to-oldest,
   then knowledge hits in caller-supplied ranking order. A too-large
   optional source is excluded whole. A too-large ancestor also blocks
   *older* ancestors, preventing holes in dialogue. The oldest orphaned
   assistant turn is excluded, preserving the existing dialogue rule.
5. Emit selected messages in their ORIGINAL order, never ranking order.
   For identical sources and model window, selection is deterministic.
   The inspection records original token cost, sent token cost (if
   truncated), inclusion, egress and an explicit decision reason.

The existing trim/summarize stage runs before this selector. The summary
cache and completion notification behavior are preserved. Only on an
overflow can the prompt change: generated summaries now carry lower-trust
wrappers and their cost is counted before the final model call.

## Read-only Context Inspector

scene/inspectChatContext(nodeId, draftText, useKnowledge?) previews the next chat send.
The intent returns provider/model, node, window, prompt budget, reserved
output, estimated prompt tokens, whether included content leaves the
device, and every candidate source with ID, origin, trust, kind, original
and sent token counts, included/excluded status, truncation, egress and
reason. It includes NO raw source text. The inspector does not run inference, consume staged attachments, edit nodes,
publish scene state or modify settings. If a draft is supplied, it runs the
same read-only, workspace-scoped local retrieval and context selection (only
when Use knowledge is enabled) to
preview possible knowledge candidates. The Inspector separately lists
knowledge hits actually included in the next request after token budgeting.
Preview metadata shows IDs, sections,
inclusion, estimated token costs, exclusion reasons and device egress.
The latest assistant ancestor's persisted citation ledger is audited
separately as `lastReplyCitations`: each sent passage is marked **Cited**
or **Not cited**, and hallucinated IDs are explicitly **Unverified**. Local Ollama/llama.cpp routes stay
on-device; cloud model routes disclose egress for included sources only.

web_ui/src/app/context/ContextInspectorLauncher.tsx and its CSS are mounted
by the existing App.tsx beside the composer. The panel targets the chosen
branch-from-here node, otherwise the selected chat node or latest chat.
Refresh takes a new snapshot of the current draft. It uses the app's
existing websocket request/reply transport and color variables.

**Preview limits:** Draft attachments not yet sent cannot be predicted;
fresh summaries require a separate inference and are not generated on a
read-only inspection. The provider/model/graph and knowledge corpus can
change between preview and send; the preview does not assert which sources
a future response will cite. Existing embedded image/audio/document parts
are accounted as part of their parent chat turn rather than as separate
inspector entries.

## Tests and follow-on work

backend/tests/test_context_engine_assembly.py compares old and new prompt
messages for fitting plain, branch-override, streaming and multimodal inputs;
checks deterministic priority, image cost and oversized mandatory content;
and proves malicious retrieved instructions cannot change allowed tools.

backend/tests/test_context_engine_inspector.py checks that inspection
doesn't mutate graph state or call inference, exposes exact branch sources
and exclusion reasons, and distinguishes local from remote sends. Existing
backend/tests/test_context_budget.py still covers history trimming and
summary behavior. The full CI additionally checks frontend lint/typecheck,
Python, Windows, wheel imports, POSIX permissions and Playwright.

backend/tests/test_chat_knowledge_citations.py covers exact per-passage
budget selection, lower-trust prompt formatting, no-hit chat parity, stream
and non-stream prompts, invented IDs, regenerated ledgers, workspace
isolation and ephemeral intent plumbing. The corresponding frontend tests
under ChatCitationChips.test.tsx and ContextInspectorPanel.test.tsx exercise
source chips, fragment links, unsafe URL rejection and citation auditing.

Still out of scope: C3 long-term memory, source revision invalidation,
graph-neighborhood reranking, source-level attachment breakdown and broad
agent integration beyond the existing ordinary chat dispatch.
