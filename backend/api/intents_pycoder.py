"""ADR-002 stage 2.6: Py-Coder node mode/run/cancel, plus the shared
approve/deny code-execution intents.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 907-959 and 1020-1024; registration calls from the
former tail block at lines 961-963 and 1026-1027) - pure code motion, no
behavior change.

approveCodeExecution/denyCodeExecution live here (not in
backend/api/intents_code_sandbox.py) because Py-Coder is the older of the
two approval-gated features and the two intents share one request_id
namespace across both kinds - see intents_code_sandbox.py's own comment
pointing back here.
"""

from __future__ import annotations

from backend.agents import _CODE_EXEC_RUN_CLAIM_PLACEHOLDER, AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_pycoder_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def set_pycoder_mode(node_id, mode):
        document.set_pycoder_mode(node_id, mode)
        await publish_scene()

    async def run_pycoder(node_id, input_text):
        # R5.3 post-review FIX 4(b)'s own Run-vs-Run race fix, reused
        # verbatim for this new kind: claim the busy slot with a shared
        # placeholder SYNCHRONOUSLY, in the same stretch as the busy
        # pre-check just above - before document.start_pycoder_run or any
        # await - so a second concurrent runPyCoder for this SAME node_id
        # can never pass the same pre-check during the `await
        # publish_scene()` gap below. Critically, this placeholder stays
        # claimed for the ENTIRE span from here through generation, through
        # the human-approval pause, through execution, through analysis - so
        # a second runPyCoder DURING the pause is refused by this SAME
        # check, no new logic needed for that case specifically (see the
        # R5.4 design spec's own section on this).
        node_for_check = document.nodes.get(node_id)
        if node_for_check is not None and node_for_check.pending_request_id:
            notifications.show("Py-Coder is already busy for this node.", "info")
            await bus.publish("notification")
            return None
        if node_for_check is not None:
            node_for_check.pending_request_id = _CODE_EXEC_RUN_CLAIM_PLACEHOLDER
        try:
            node = document.start_pycoder_run(node_id, input_text)
        except SceneError:
            if node_for_check is not None:
                node_for_check.pending_request_id = None
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []

        def _on_success(code, output, analysis, last_run_failed):
            document.complete_pycoder_run(node_id, code, output, analysis, last_run_failed)

        def _on_failure(message):
            document.fail_pycoder_run(node_id, message)

        await agent_dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            mode=node.pycoder_mode, prompt=node.pycoder_prompt, code=node.pycoder_code,
            conversation_history=branch_history,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_pycoder_request(request_id):
        agent_dispatcher.cancel_pycoder(request_id)

    bus.register_intent("scene", "setPyCoderMode", set_pycoder_mode)
    bus.register_intent("scene", "runPyCoder", run_pycoder)
    bus.register_intent("scene", "cancelPyCoderRequest", cancel_pycoder_request)

    # -- shared approve/deny - one request_id namespace across both Py-Coder
    # and Execution Sandbox kinds.

    async def approve_code_execution(request_id):
        agent_dispatcher.approve_code_execution(request_id)

    async def deny_code_execution(request_id):
        agent_dispatcher.deny_code_execution(request_id)

    bus.register_intent("scene", "approveCodeExecution", approve_code_execution)
    bus.register_intent("scene", "denyCodeExecution", deny_code_execution)
