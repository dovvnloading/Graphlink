"""Manual Python editing and undo for Virtual Environment Runner nodes."""

from __future__ import annotations

import asyncio

import pytest

from backend.canvas import register_canvas
from backend.composer import ComposerDocument
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError
from backend.events import SessionBus
from backend.notifications import NotificationState


def _code_node():
    doc = SceneDocument()
    node = doc.add_code_sandbox_node(0, 0, None)
    return doc, node


def test_manual_code_write_preserves_model_approval_and_clears_stale_outputs():
    doc, node = _code_node()
    node.state.code_sandbox_code = "print('old')"
    node.state.code_sandbox_output = "old output"
    node.state.code_sandbox_analysis = "old explanation"
    node.state.code_sandbox_error = "old failure"
    doc.set_code_sandbox_code(node.id, "print('new')", "print('old')")
    assert node.state.code_sandbox_code == "print('new')"
    assert node.state.code_sandbox_output == ""
    assert node.state.code_sandbox_analysis == ""
    assert node.state.code_sandbox_error == ""
    # A manual code edit only changes what a future run will propose;
    # human approval of that code is still required for execution.
    assert node.state.code_sandbox_awaiting_approval is False
    assert node.state.code_sandbox_approved_fingerprint is None


def test_stale_manual_edits_are_rejected_before_overwriting_newer_script():
    doc, node = _code_node()
    doc.set_code_sandbox_code(node.id, "print(1)", "")
    with pytest.raises(SceneError, match="changed since editing"):
        doc.set_code_sandbox_code(node.id, "print('stale')", "")
    assert node.state.code_sandbox_code == "print(1)"


def test_edit_cannot_change_code_while_approval_or_execution_is_in_flight():
    doc, node = _code_node()
    node.state.code_sandbox_code = "print(1)"
    node.pending_request_id = "busy"
    with pytest.raises(SceneError, match="Stop the running"):
        doc.set_code_sandbox_code(node.id, "print(2)", "print(1)")
    node.pending_request_id = None
    node.state.code_sandbox_awaiting_approval = True
    with pytest.raises(SceneError, match="Stop the running"):
        doc.set_code_sandbox_code(node.id, "print(2)", "print(1)")
    assert node.state.code_sandbox_code == "print(1)"


def test_code_and_manifest_limits_reject_oversized_ws_payloads():
    doc, node = _code_node()
    with pytest.raises(SceneError, match="100000"):
        doc.set_code_sandbox_code(node.id, "x" * 100001, "")
    with pytest.raises(SceneError, match="16384"):
        doc.set_code_sandbox_requirements(node.id, "x" * 16385)
    with pytest.raises(SceneError, match="12000"):
        doc.start_code_sandbox_run(node.id, "x" * 12001)
    assert node.state.code_sandbox_code == ""
    assert node.state.code_sandbox_requirements == ""
    assert node.state.code_sandbox_prompt == ""


def test_failed_edits_are_atomic_and_the_valid_edit_is_undoable():
    doc, node = _code_node()
    old = "print('original')"
    doc.record_command(
        "setCodeSandboxCode", "user",
        lambda: doc.set_code_sandbox_code(node.id, old, ""),
        node_ids=[node.id],
    )
    with pytest.raises(SceneError, match="changed since editing"):
        doc.record_command(
            "setCodeSandboxCode", "user",
            lambda: doc.set_code_sandbox_code(node.id, "stale", ""),
            node_ids=[node.id],
        )
    assert doc.nodes[node.id].state.code_sandbox_code == old
    doc.undo()
    assert doc.nodes[node.id].state.code_sandbox_code == ""
    doc.redo()
    assert doc.nodes[node.id].state.code_sandbox_code == old


def test_intent_exposes_manual_code_edits_through_scene_bus():
    class FakeDispatcher:
        pass

    async def exercise():
        bus = SessionBus("manual-code-edit")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer = ComposerDocument()
        bus.register_topic("app-composer", composer.payload)
        document = register_canvas(bus, notifications, FakeDispatcher(), composer)
        node = document.add_code_sandbox_node(0, 0, None)
        assert await bus.dispatch_intent(
            "scene", "setCodeSandboxCode", [node.id, "print(42)", ""]
        ) == node.id
        assert document.nodes[node.id].state.code_sandbox_code == "print(42)"
        with pytest.raises(SceneError, match="changed since editing"):
            await bus.dispatch_intent(
                "scene", "setCodeSandboxCode", [node.id, "print(99)", ""]
            )

    asyncio.run(exercise())
