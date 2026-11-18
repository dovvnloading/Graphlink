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
   budgeter, and an untrusted source formatter. Ordinary chat Send DOES NOT
   automatically call it. backend/api/intents_knowledge.py supplies separate
   explicit search. backend/api/intents_conversation.py shares the dispatcher.

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

The first slice can accept pinned, retrieval and independent attachment
candidates, but does not silently introduce new auto-retrieval or context
pinning behavior in ordinary chat. Existing document extracts and image
parts already included in a user message retain their legacy transport shape.

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

scene/inspectChatContext(nodeId, draftText) previews the next chat send.
The intent returns provider/model, node, window, prompt budget, reserved
output, estimated prompt tokens, whether included content leaves the
device, and every candidate source with ID, origin, trust, kind, original
and sent token counts, included/excluded status, truncation, egress and
reason. It includes NO raw source text. The inspector does not run
inference, search knowledge, consume staged attachments, edit nodes,
publish scene state or modify settings. Local Ollama/llama.cpp routes stay
on-device; cloud model routes disclose egress for included sources only.

web_ui/src/app/context/ContextInspectorLauncher.tsx and its CSS are mounted
by the existing App.tsx beside the composer. The panel targets the chosen
branch-from-here node, otherwise the selected chat node or latest chat.
Refresh takes a new snapshot of the current draft. It uses the app's
existing websocket request/reply transport and color variables.

**Preview limits:** Draft attachments not yet sent cannot be predicted;
fresh summaries require a separate inference and are not generated on a
read-only inspection. The provider/model/graph can change between preview
and send. Existing embedded image/audio/document parts are accounted as
part of their parent chat turn rather than as separate inspector entries.

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

This first slice does NOT implement C3's full long-term memory, source
revision invalidation, graph neighborhood reranking, automatic knowledge
hits, source-level attachment breakdown, or broad agent integration.
