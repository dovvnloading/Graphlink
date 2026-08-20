"""ADR-002 stage 2.6: Web Research node run/cancel.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 316-371; registration calls from the former tail block
at lines 1139-1143) - pure code motion, no behavior change. Node creation
itself lives in backend/plugins.py's executePlugin (the "Web Research"
branch), not here - these two intents drive an EXISTING web_research-kind
node.
"""

from __future__ import annotations

import asyncio

from backend.agents import AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus
from backend.knowledge_store import DEFAULT_DB_PATH as KNOWLEDGE_DEFAULT_DB_PATH
from backend.knowledge_store import get_or_create_workspace_collection
from backend.notifications import NotificationState


def register_web_research_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
) -> None:
    # Deferred import - backend.canvas itself imports register_web_research_
    # intents from this module, so a module-level `from backend.canvas
    # import _research_result_wire` here would race that same cycle
    # depending on which module happens to be imported first (see
    # backend/api/intents_chat.py's own docstring/comment for the identical
    # reasoning, first established there).
    from backend.canvas import _research_result_wire

    publish_scene = make_publish_scene(bus)

    async def run_web_research(node_id, query_text):
        if agent_dispatcher.is_web_research_busy():
            # Checked BEFORE touching document state: start_web_research_run
            # resets a node's progress/error fields unconditionally, and the
            # dispatcher only allows one web-research run at a time anyway -
            # without this early check, clicking Run on a different node
            # while one is already in flight would silently wipe that node's
            # prior result/error banner even though no new run actually starts.
            notifications.show("A web research request is already running.", "info")
            await bus.publish("notification")
            return None
        try:
            node = document.start_web_research_run(node_id, query_text)
        except SceneError:
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []

        async def _on_progress(event):
            if node_id not in document.nodes:
                return
            document.apply_web_research_progress(node_id, event)
            await bus.publish("scene")

        async def _on_success(result):
            if node_id not in document.nodes:
                return
            document.complete_web_research_run(node_id, _research_result_wire(result))
            await bus.publish("scene")

        async def _on_failure(exc):
            if node_id not in document.nodes:
                return
            cancelled = type(exc).__name__ == "RequestCancelled"
            document.fail_web_research_run(node_id, cancelled=cancelled, message=str(exc))
            await bus.publish("scene")

        # ADR-020 stage 20.3: resolves the calling session's current
        # workspace's own knowledge collection BEFORE dispatch, exactly
        # like every other real ingestion call site in this ADR stage -
        # cheap (a single indexed SELECT, or an INSERT the very first time
        # this workspace ever retains anything) but still real blocking
        # SQLite I/O, so it goes through asyncio.to_thread rather than
        # running inline on the event loop, same posture as backend/api/
        # intents_knowledge.py's own search()/set_chat_index_into_
        # knowledge(). document.current_workspace_id is None for a session
        # that has not yet loaded/created any chat with a real workspace
        # context - falls back to 0, the pre-20.3 global/unscoped sentinel
        # (backend/knowledge_store.py's own module docstring), matching
        # every other real call site's identical fallback.
        workspace_id = document.current_workspace_id
        if workspace_id is None:
            knowledge_collection_id = 0
        else:
            knowledge_collection_id = await asyncio.to_thread(
                get_or_create_workspace_collection, KNOWLEDGE_DEFAULT_DB_PATH, workspace_id,
            )

        await agent_dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id=node_id,
            query=query_text,
            branch_history=branch_history,
            on_progress=_on_progress,
            on_success=_on_success,
            on_failure=_on_failure,
            knowledge_collection_id=knowledge_collection_id,
            # ADR-021 stage 21.5: the node's own opt-in. Read AFTER
            # start_web_research_run above (which never touches it), so a
            # toggle flipped between runs takes effect on the next one.
            retain_to_knowledge=bool(node.state.research_retain_to_knowledge),
        )
        return node_id

    async def set_web_research_retain_to_knowledge(node_id, retain):
        # ADR-021 stage 21.5: not record_command-wrapped - this is a run
        # OPTION for the next research run, not document content, the same
        # posture setCodeSandboxAllowSourceBuilds takes for its own
        # per-node run flag.
        try:
            document.set_web_research_retain_to_knowledge(node_id, retain)
        except SceneError:
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return
        await publish_scene()

    async def cancel_web_research_request(request_id):
        agent_dispatcher.cancel_web_research(request_id)

    # R5.1: Web Research node run/cancel - node creation itself lives in
    # backend/plugins.py's executePlugin (the "Web Research" branch), not
    # here; these two intents drive an EXISTING web_research-kind node.
    bus.register_intent("scene", "runWebResearch", run_web_research)
    bus.register_intent(
        "scene", "setWebResearchRetainToKnowledge", set_web_research_retain_to_knowledge,
    )
    bus.register_intent("scene", "cancelWebResearchRequest", cancel_web_research_request)
