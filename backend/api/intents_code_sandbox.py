"""ADR-002 stage 2.6: Execution Sandbox node requirements/run/cancel.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 967-1012; registration calls from the former tail block
at lines 1014-1016) - pure code motion, no behavior change.

approveCodeExecution/denyCodeExecution (the shared approve/deny pair this
kind also depends on) live in backend/api/intents_pycoder.py instead - see
that module's own docstring for why.
"""

from __future__ import annotations

from backend.agents import _CODE_EXEC_RUN_CLAIM_PLACEHOLDER, AgentDispatcher
from backend.api._shared import make_publish_scene
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState


def register_code_sandbox_intents(
    bus: SessionBus,
    document: SceneDocument,
    notifications: NotificationState,
    agent_dispatcher: AgentDispatcher,
) -> None:
    publish_scene = make_publish_scene(bus)

    async def set_code_sandbox_requirements(node_id, requirements_text):
        document.set_code_sandbox_requirements(node_id, requirements_text)
        await publish_scene()

    async def run_code_sandbox(node_id, input_text):
        # Same busy-claim-placeholder pattern as run_pycoder (backend/api/
        # intents_pycoder.py, and run_gitlink_change_set before it,
        # backend/api/intents_gitlink.py) - see that function's own comment
        # for the exact race this closes.
        node_for_check = document.nodes.get(node_id)
        if node_for_check is not None and node_for_check.pending_request_id:
            notifications.show("Virtual Environment Runner is already busy for this node.", "info")
            await bus.publish("notification")
            return None
        if node_for_check is not None:
            node_for_check.pending_request_id = _CODE_EXEC_RUN_CLAIM_PLACEHOLDER
        try:
            node = document.start_code_sandbox_run(node_id, input_text)
        except SceneError:
            if node_for_check is not None:
                node_for_check.pending_request_id = None
            notifications.show("This node no longer exists.", "warning")
            await bus.publish("notification")
            return None
        await publish_scene()

        parent_edge = document._branch_parent_edge(node_id)
        branch_history = document.chat_branch_history(parent_edge.source) if parent_edge else []

        def _on_success(code, output, analysis):
            document.complete_code_sandbox_run(node_id, code, output, analysis)

        def _on_failure(message):
            document.fail_code_sandbox_run(node_id, message)

        await agent_dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id=node_id,
            sandbox_id=node.state.code_sandbox_sandbox_id,
            prompt=node.state.code_sandbox_prompt, existing_code=node.state.code_sandbox_code,
            requirements_manifest=node.state.code_sandbox_requirements,
            conversation_history=branch_history,
            on_success=_on_success, on_failure=_on_failure,
        )
        return node_id

    async def cancel_code_sandbox_request(request_id):
        agent_dispatcher.cancel_code_sandbox(request_id)

    bus.register_intent("scene", "setCodeSandboxRequirements", set_code_sandbox_requirements)
    bus.register_intent("scene", "runCodeSandbox", run_code_sandbox)
    bus.register_intent("scene", "cancelCodeSandboxRequest", cancel_code_sandbox_request)
