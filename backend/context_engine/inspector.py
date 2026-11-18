"""Read-only C3 preview of the NEXT chat send on a graph node.

The preview accepts an explicit unsent draft; it does not consume the composer,
index documents, call a provider, or generate summaries. The real send still
builds ancestors in backend.domain.branches.chat_branch_history and resolves
the system/model in DispatcherCoreOps, which this module calls directly.
"""

from __future__ import annotations

import api_provider
from backend.context_engine import ContextAssembler, ContextBudgetError, selected_context_window
from backend.domain.model import SceneError
from graphlink_chat_agent import ChatAgent


def _branch_origins(document, node_id: str) -> list[str]:
    """Mirror chat_branch_history's structural-parent walk, not raw edges."""
    reversed_ids = []
    seen = set()
    current = node_id
    while current is not None and current not in seen:
        seen.add(current)
        node = document.nodes.get(current)
        if node is None:
            break
        reversed_ids.append(f"node:{current}")
        edge = document._branch_parent_edge(current)
        current = edge.source if edge is not None else None
    return list(reversed(reversed_ids))


def inspect_next_chat_send(document, node_id: str, dispatcher, *, bus=None, draft_text: str = "") -> dict:
    """Explain the next dispatch from node_id, without mutating anything.

    The preview is limited to known history and the supplied draft. Incoming
    document/image attachments not yet sent cannot be inferred here. The
    existing chat reply path does not auto-inject pinned notes or knowledge
    hits; this preview does not invent them or run retrieval on a read.
    """
    node = document.nodes.get(node_id)
    if node is None or node.kind != "chat":
        raise SceneError(f"node is not a chat node: {node_id}")
    if not isinstance(draft_text, str):
        raise ValueError("draft_text must be a string")
    # Keep the full walk and its origins from the same immutable-in-this-call
    # graph snapshot; neither operation creates a node or modifies the model.
    history = document.chat_branch_history(node_id)
    origins = _branch_origins(document, node_id)
    if len(history) != len(origins):
        raise SceneError("branch history changed during inspection")
    history.append({"role": "user", "content": draft_text})
    origins.append(f"draft:after:{node_id}")

    override = dispatcher._resolve_branch_system_prompt(document, node_id)
    persona = override if override is not None else dispatcher.persona()
    system_prompt = persona if override is not None else ChatAgent("Graphlink Assistant", persona).system_prompt
    system_origin = (
        f"system-note:{next((edge.source for edge in document.edges.values() if edge.target == document.get_branch_root(node_id).id and (source := document.nodes.get(edge.source)) is not None and source.kind == 'note' and getattr(source.state, 'is_system_prompt', False)), 'unknown')}"
        if override is not None else "system:settings"
    )

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
        plan = assembler.assemble(candidates, context_window=window, leaves_device=not local)
    except ContextBudgetError as exc:
        # A preview must disclose the failure without claiming a send can
        # proceed. No provider is called and no context is silently dropped.
        return {
            "nodeId": node_id,
            "provider": provider,
            "model": model,
            "leavesDevice": not local,
            "error": str(exc),
            "sources": [],
        }
    result = plan.inspect()
    result.update({
        "nodeId": node_id,
        "provider": provider,
        "model": model,
        "leavesDevice": not local,
        "draftIncluded": bool(draft_text),
        "notice": "Preview of the supplied draft and persisted branch only. Unsent attachments and model-generated summaries are not available until send. Knowledge search and ordinary notes are not auto-injected by this chat path.",
    })
    return result
