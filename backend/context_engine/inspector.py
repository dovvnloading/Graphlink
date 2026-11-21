"""Read-only C3 preview of the NEXT chat send on a graph node.

The preview accepts an explicit unsent draft; it does not consume the composer,
index documents, call a provider, or generate summaries. The real send still
builds ancestors in backend.domain.branches.chat_branch_history and resolves
the system/model in DispatcherCoreOps, which this module calls directly.
"""

from __future__ import annotations

import api_provider
from backend.context_engine import ContextAssembler, ContextBudgetError, selected_context_window
from backend.context_engine.citations import assemble_chat_context, inspected_reply_citations
from backend.knowledge_retrieval import chat_use_knowledge, search_chat_knowledge
from backend.domain.model import SceneError
from graphlink_chat_agent import ChatAgent
from graphlink_memory import message_tokens


def _branch_origins(document, node_id: str) -> list[str]:
    """Mirror chat_branch_history's structural-parent walk, not raw edges."""
    reversed_ids = []
    seen = set()
    current: str | None = node_id
    while current is not None and current not in seen:
        seen.add(current)
        node = document.nodes.get(current)
        if node is None:
            break
        reversed_ids.append(f"node:{current}")
        edge = document._branch_parent_edge(current)
        current = edge.source if edge is not None else None
    return list(reversed(reversed_ids))


def inspect_next_chat_send(
    document, node_id: str, dispatcher, *, bus=None, draft_text: str = "",
    use_knowledge: bool | None = None,
) -> dict:
    """Explain the next dispatch and audit the last reply without mutation.

    Incoming draft attachments cannot be inferred. An existing knowledge
    database is searched read-only against this workspace, as on real Send.
    """
    node = document.nodes.get(node_id)
    if node is None or node.kind != "chat":
        raise SceneError(f"node is not a chat node: {node_id}")
    if not isinstance(draft_text, str):
        raise ValueError("draft_text must be a string")
    if use_knowledge is not None and not isinstance(use_knowledge, bool):
        raise ValueError("use_knowledge must be a boolean or None")
    enabled = chat_use_knowledge(document, node_id) if use_knowledge is None else use_knowledge
    # Keep the full walk and its origins from the same immutable-in-this-call
    # graph snapshot; neither operation creates a node or modifies the model.
    history = document.chat_branch_history(node_id)
    origins = _branch_origins(document, node_id)
    if len(history) != len(origins):
        raise SceneError("branch history changed during inspection")
    history.append({"role": "user", "content": draft_text})
    origins.append(f"draft:after:{node_id}")
    last_reply_citations = inspected_reply_citations(document, origins)

    override = dispatcher._resolve_branch_system_prompt(document, node_id)
    persona = override if override is not None else dispatcher.persona()
    system_prompt = persona if override is not None else ChatAgent("Graphlink Assistant", persona).system_prompt
    system_origin = "system:settings"
    if override is not None:
        # Match the dispatcher's first connected System Prompt note, using
        # structural source identity rather than a long nested expression.
        system_origin = "system-note:unknown"
        root = document.get_branch_root(node_id)
        if root is not None:
            for edge in document.edges.values():
                if edge.target != root.id:
                    continue
                source = document.nodes.get(edge.source)
                if (
                    source is not None and source.kind == "note"
                    and getattr(source.state, "is_system_prompt", False)
                ):
                    system_origin = f"system-note:{source.id}"
                    break

    model_ref = dispatcher._resolve_model_ref_for_dispatch(document, node_id, bus=bus)
    runtime = dispatcher._provider_runtime if dispatcher._provider_runtime is not None else api_provider.DEFAULT_RUNTIME
    try:
        window = selected_context_window(runtime, model_ref)
    except Exception:
        window = 8000  # ChatWorker.run's own last-resort fallback
    provider, model = dispatcher.active_provider_model(model_ref)
    local = provider.lower() in ("ollama", "llama.cpp")
    assembler = ContextAssembler()
    candidates = assembler.chat_sources(
        system_prompt, history, origins=origins, system_origin=system_origin,
    )
    try:
        knowledge_hits = (
            search_chat_knowledge(draft_text[:1500], document.current_workspace_id)
            if enabled and draft_text.strip() else []
        )
    except Exception:
        # Inspection is best-effort, never an ingestion or provider call.
        knowledge_hits = []
    try:
        plan, _included_knowledge = assemble_chat_context(
            assembler, system_prompt, history,
            context_window=window, knowledge_hits=knowledge_hits,
            origins=origins, system_origin=system_origin,
            leaves_device=not local,
        )
    except ContextBudgetError as exc:
        # A preview must disclose the failure without claiming a send can
        # proceed. No provider is called and no context is silently dropped.
        # Nothing is dispatched in this case: still enumerate every source
        # with its original measured cost, instead of hiding the context
        # precisely when the user most needs to inspect it.
        reserve = min(4096, window // 4)
        return {
            "nodeId": node_id,
            "provider": provider,
            "model": model,
            "contextWindow": window,
            "outputReserve": reserve,
            "promptBudget": window - reserve,
            "promptTokens": 0,
            "leavesDevice": not local,
            "draftIncluded": bool(draft_text),
            "useKnowledge": enabled,
            "error": str(exc),
            **({"lastReplyCitations": last_reply_citations} if last_reply_citations else {}),
            "sources": [
                {
                    "sourceId": src.source_id,
                    "origin": src.origin,
                    "kind": src.kind,
                    "trust": src.trust,
                    "tokenCount": message_tokens(src.message, assembler.token_estimator),
                    "sentTokenCount": 0,
                    "included": False,
                    "truncated": False,
                    "leavesDevice": False,
                    "reason": "not sent: mandatory context exceeds model budget",
                }
                for src in candidates
            ],
        }
    result = plan.inspect()
    result.update({
        "nodeId": node_id,
        "provider": provider,
        "model": model,
        "leavesDevice": not local,
        "draftIncluded": bool(draft_text),
        "useKnowledge": enabled,
        "notice": (
            "Knowledge retrieval is on: the listed knowledge hits are selected using the "
            "same budget as Send. Unsent attachments and generated summaries are not "
            "available; the corpus or model may change."
            if enabled else
            "Knowledge retrieval is off for this branch. Unsent attachments and "
            "generated summaries are not available until send."
        ),
        **({"lastReplyCitations": last_reply_citations} if last_reply_citations else {}),
    })
    return result
