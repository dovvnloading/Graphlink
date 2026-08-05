"""ADR-002 stage 2.6: generic node/edge CRUD intents.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 311-382, 1566-1630; registration calls from the former
tail block at lines 1679-1687, 1733-1737) - pure code motion, no behavior
change. Every closure here previously captured `document`/`publish_scene`
(and, for remove_nodes alone, `agent_dispatcher`) from register_canvas's
own local scope; those are now explicit parameters instead.
"""

from __future__ import annotations

import base64

from backend.agents import AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.events import SessionBus


def register_node_intents(
    bus: SessionBus,
    document: SceneDocument,
    agent_dispatcher: AgentDispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def add_node(x, y, title=""):
        node = document.add_node(x, y, title)
        await publish_scene()
        return node.id

    async def add_chat_node(x, y, content, is_user, parent_id=None):
        node = document.add_chat_node(x, y, content, is_user, parent_id)
        await publish_scene()
        return node.id

    async def add_code_node(x, y, code, language, parent_id=None):
        node = document.add_code_node(x, y, code, language, parent_id)
        await publish_scene()
        return node.id

    async def add_document_node(
        x,
        y,
        title,
        content,
        attachment_kind,
        parent_id,
        file_path="",
        mime_type="",
        duration_seconds=None,
        byte_size=None,
        preview_label="",
    ):
        node = document.add_document_node(
            x,
            y,
            title,
            content,
            attachment_kind,
            parent_id,
            file_path=file_path,
            mime_type=mime_type,
            duration_seconds=duration_seconds,
            byte_size=byte_size,
            preview_label=preview_label,
        )
        await publish_scene()
        return node.id

    async def add_thinking_node(x, y, thinking_text, parent_id):
        node = document.add_thinking_node(x, y, thinking_text, parent_id)
        await publish_scene()
        return node.id

    async def add_html_node(x, y, html_content, parent_id):
        node = document.add_html_node(x, y, html_content, parent_id)
        await publish_scene()
        return node.id

    async def set_html_splitter_state(node_id, value):
        document.set_html_splitter_state(node_id, value)
        await publish_scene()

    async def add_image_node(x, y, image_bytes_base64, prompt, parent_id, mime_type="image/png"):
        # Unlike every prior wrapper, the WS intent transport is JSON, which
        # cannot carry raw bytes - the caller sends base64 text, decoded here
        # before it ever reaches SceneDocument (which only ever deals in real
        # bytes, same as the HTTP asset route on the read side).
        image_bytes = base64.b64decode(image_bytes_base64)
        node = document.add_image_node(x, y, image_bytes, prompt, parent_id, mime_type=mime_type)
        await publish_scene()
        return node.id

    async def add_conversation_node(x, y, parent_id):
        node = document.add_conversation_node(x, y, parent_id)
        await publish_scene()
        return node.id

    async def move_node(node_id, x, y):
        document.move_node(node_id, x, y)
        await publish_scene()

    async def move_nodes(positions):
        # positions: a JSON array of [node_id, x, y] triples - see
        # SceneDocument.move_nodes's own docstring for why a group drag's
        # commit uses this batched intent instead of N calls to moveNode.
        document.move_nodes([(p[0], p[1], p[2]) for p in positions])
        await publish_scene()

    async def remove_nodes(node_ids):
        ids = list(node_ids)
        # R5.4: a deleted Py-Coder node's REPL subprocess must not outlive
        # it - kind is captured BEFORE document.remove_nodes pops the node,
        # since afterward there is nothing left to read it from.
        # ADR-005 stage 5.3 (review-fix): pycoder_repl_id is captured
        # alongside node_id, not derived from it - the on-disk scratch dir
        # is keyed by the node's STABLE repl id, not its (reload-volatile)
        # node id, so dispose_pycoder_repl needs both: node_id to find/pop
        # any live in-memory REPL, repl_id to deterministically locate and
        # remove the directory even if no live REPL is currently tracked
        # (e.g. a prior execute timeout already popped it) - see that
        # method's own docstring.
        pycoder_ids = [
            (node_id, document.nodes[node_id].state.pycoder_repl_id)
            for node_id in ids
            if document.nodes.get(node_id) is not None and document.nodes[node_id].kind == "pycoder"
        ]
        # ADR-005 stage 5.3: a deleted Execution Sandbox node's on-disk
        # venv must not outlive it either - same capture-before-pop timing
        # as pycoder_ids above. sandbox_id is minted once at node creation
        # (graph.py's add_code_sandbox_node) or self-healed on session
        # load if a saved payload was ever missing it (see
        # session_load.py's _restore_code_sandbox_payload), so it is
        # always non-blank for a real code_sandbox node reached through
        # this app's own code paths - remove_code_sandbox_scratch_dir
        # additionally refuses to act on a blank id anyway, as defense in
        # depth against a payload this app didn't produce.
        sandbox_ids = [
            document.nodes[node_id].state.code_sandbox_sandbox_id
            for node_id in ids
            if document.nodes.get(node_id) is not None and document.nodes[node_id].kind == "code_sandbox"
        ]
        # R5.4 post-review FIX 2: a deleted pycoder/code_sandbox node's
        # DISPATCHER-SIDE in-flight request must not outlive it either - captured
        # here, BEFORE document.remove_nodes pops the node, for the same reason
        # pycoder_ids above is. dispose_pycoder_repl alone only tears down the
        # REPL subprocess; it does nothing about a request parked on `await
        # approval_future` on AgentDispatcher's own self._runs registry
        # ("pycoder"/"code_sandbox" kinds), which has NO timeout by design (the whole
        # point is "wait for a human, however long that takes"). Without this,
        # deleting a node mid-approval-pause would leave that future - and the
        # asyncio.Task awaiting it - alive forever, and a stale/duplicate
        # approve-or-deny message arriving later could still resolve it, lazily
        # recreating a REPL or spinning up a fresh sandbox subprocess for a
        # node_id no longer present anywhere in the scene.
        code_exec_cancels = [
            (document.nodes[node_id].kind, document.nodes[node_id].pending_request_id)
            for node_id in ids
            if document.nodes.get(node_id) is not None
            and document.nodes[node_id].kind in ("pycoder", "code_sandbox")
            and document.nodes[node_id].pending_request_id
        ]
        document.remove_nodes(ids)
        for kind, request_id in code_exec_cancels:
            # cancel_pycoder/cancel_code_sandbox resolve any pending
            # approval_future with False (exactly like a manual Cancel/Deny)
            # and trip the run's cancel_event - a safe no-op if request_id
            # does not name a live registry entry of the matching kind (e.g.
            # it was only ever the synchronous busy-claim placeholder, never
            # a real dispatcher request_id, or the request already finished
            # on its own between the capture above and here).
            #
            # Review-fix: done BEFORE the scratch-dir removal below, but be
            # precise about what that ordering actually buys - it differs
            # by kind. For code_sandbox, VirtualEnvSandbox._run_subprocess
            # polls should_continue() roughly every 100ms, so tripping
            # cancel_event here gives a real, if brief, head start before
            # remove_code_sandbox_scratch_dir's rmtree runs (removal is
            # still best-effort regardless - see that method's own
            # docstring - this only improves the odds). For pycoder,
            # PythonREPL.execute() blocks on a plain readline() with no
            # polling hook at all, so cancel_pycoder has NO effect on a
            # call already in flight - what actually stops that subprocess
            # is dispose_pycoder_repl's own kill()+wait() a moment later,
            # which happens regardless of this ordering. Kept first anyway
            # because it IS the only thing that matters for a run still
            # parked on the approval gate (resolving approval_future(False)
            # before it can even start), which is what code_exec_cancels
            # exists for in the first place.
            if kind == "pycoder":
                agent_dispatcher.cancel_pycoder(request_id)
            else:
                agent_dispatcher.cancel_code_sandbox(request_id)
        for node_id, repl_id in pycoder_ids:
            # ADR-005 stage 5.3: remove_scratch_dir=True - this IS real node
            # deletion, unlike the execute-timeout caller of the same method.
            await agent_dispatcher.dispose_pycoder_repl(node_id, repl_id=repl_id, remove_scratch_dir=True)
        for sandbox_id in sandbox_ids:
            await agent_dispatcher.remove_code_sandbox_scratch_dir(sandbox_id)
        await publish_scene()

    async def connect_nodes(source, target):
        edge = document.connect(source, target)
        await publish_scene()
        return edge.id

    async def remove_edges(edge_ids):
        document.remove_edges(list(edge_ids))
        await publish_scene()

    bus.register_intent("scene", "addNode", add_node)
    bus.register_intent("scene", "addChatNode", add_chat_node)
    bus.register_intent("scene", "addCodeNode", add_code_node)
    bus.register_intent("scene", "addDocumentNode", add_document_node)
    bus.register_intent("scene", "addThinkingNode", add_thinking_node)
    bus.register_intent("scene", "addHtmlNode", add_html_node)
    bus.register_intent("scene", "setHtmlSplitterState", set_html_splitter_state)
    bus.register_intent("scene", "addImageNode", add_image_node)
    bus.register_intent("scene", "addConversationNode", add_conversation_node)
    bus.register_intent("scene", "moveNode", move_node)
    bus.register_intent("scene", "moveNodes", move_nodes)
    bus.register_intent("scene", "removeNodes", remove_nodes)
    bus.register_intent("scene", "connectNodes", connect_nodes)
    bus.register_intent("scene", "removeEdges", remove_edges)
