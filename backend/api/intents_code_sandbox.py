"""ADR-002 stage 2.6: Execution Sandbox node requirements/run/cancel.

Relocated VERBATIM from backend/canvas.py's former register_canvas
(closures at lines 967-1012; registration calls from the former tail block
at lines 1014-1016) - pure code motion, no behavior change.

approveCodeExecution/denyCodeExecution originally lived here alongside a
sibling intents_pycoder.py module, sharing one request_id namespace across
both kinds (Py-Coder being the older of the two approval-gated features).
PLAN-2026-08-24 H5 retired Py-Coder; the two shared intents relocate here
(their only remaining owner) rather than being deleted, since Execution
Sandbox still depends on them.
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
        document.record_command(
            "setCodeSandboxRequirements", "user",
            lambda: document.set_code_sandbox_requirements(node_id, requirements_text),
            node_ids=[node_id],
        )
        await publish_scene()

    async def set_code_sandbox_allow_source_builds(node_id, allow):
        document.set_code_sandbox_allow_source_builds(node_id, allow)
        await publish_scene()

    async def run_code_sandbox(node_id, input_text):
        # Same busy-claim-placeholder pattern as run_gitlink_change_set
        # (backend/api/intents_gitlink.py) - see that function's own
        # comment for the exact race this closes.
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
    bus.register_intent(
        "scene", "setCodeSandboxAllowSourceBuilds", set_code_sandbox_allow_source_builds
    )
    bus.register_intent("scene", "runCodeSandbox", run_code_sandbox)
    bus.register_intent("scene", "cancelCodeSandboxRequest", cancel_code_sandbox_request)

    # -- the shared approve/deny code-execution gate (see this module's own
    # docstring for why these live here now)

    async def approve_code_execution(request_id):
        agent_dispatcher.approve_code_execution(request_id)

    async def deny_code_execution(request_id):
        agent_dispatcher.deny_code_execution(request_id)

    bus.register_intent("scene", "approveCodeExecution", approve_code_execution)
    bus.register_intent("scene", "denyCodeExecution", deny_code_execution)
