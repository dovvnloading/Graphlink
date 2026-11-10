"""Fresh consent for the real persistent Python tool, using harmless fixtures.

The registry, handler and subprocess are real. Only the human's decision is
scripted here; browser/transport coverage lives in the integrated evidence.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from api_provider import RequestCancelledError
from backend.harness.loop import HarnessRunContext, _approval_summary, _is_dangerous_call
from backend.harness.tools_python import PythonReplRegistry, register_harness_python_tool
from backend.providers.base import CancelToken, ToolCall
from backend.tools import CODE_EXECUTE, RunContext, ToolRegistry, _tool_call_fingerprint


@pytest.fixture
def python_tool(tmp_path):
    registry = ToolRegistry()
    repls = PythonReplRegistry()
    register_harness_python_tool(registry, repls)
    try:
        yield registry, repls, tmp_path
    finally:
        repls.stop_all()


def _call(code, cid="python-fixture"):
    return ToolCall(id=cid, name="python.exec", arguments={"code": code})


def _ctx(workspace, approve, **kwargs):
    return HarnessRunContext(
        granted_scopes=frozenset({CODE_EXECUTE}), request_approval=approve,
        harness_workspace_id="python-approval-fixture", harness_workspace_dir=workspace,
        **kwargs,
    )


def test_complete_exact_source_is_disclosed_after_character_400():
    source = "\n# " + "harmless padding " * 40 + "\nprint('<script>visible</script>')\n\t\n"
    assert _approval_summary(_call(source)) == "python.exec\n--- source\n" + source
    assert _is_dangerous_call(_call(source)) is True


def test_identical_source_reprompts_and_denial_preserves_persistent_state(python_tool):
    registry, _repls, workspace = python_tool
    code = (
        "from pathlib import Path\n"
        "counter = globals().get('counter', 0) + 1\n"
        "Path('counter.txt').write_text(str(counter), encoding='utf-8')\n"
        "print(counter)\n"
    )
    decisions = iter([True, False, True])
    prompts = []

    async def approve(call):
        prompts.append(call.arguments["code"])
        return next(decisions)

    ctx = _ctx(workspace, approve)
    first = asyncio.run(registry.invoke(_call(code, "1"), ctx))
    assert first.content.strip() == "1"
    denied = asyncio.run(registry.invoke(_call(code, "2"), ctx))
    assert denied.is_error and "denied" in denied.content
    assert (workspace / "counter.txt").read_bytes() == b"1"
    third = asyncio.run(registry.invoke(_call(code, "3"), ctx))
    assert third.content.strip() == "2"
    assert (workspace / "counter.txt").read_bytes() == b"2"
    assert prompts == [code, code, code]
    assert ctx._approved_fingerprints == set()


def test_old_session_and_fingerprint_grants_cannot_authorize_any_code(python_tool):
    registry, repls, workspace = python_tool
    calls = [_call("open('one.txt', 'w').write('one')"), _call("open('two.txt', 'w').write('two')")]
    prompts = []

    async def deny(call):
        prompts.append(call)
        return False

    ctx = _ctx(workspace, deny, session_grants={"python.exec"})
    ctx._approved_fingerprints.add(_tool_call_fingerprint(calls[0]))
    results = [asyncio.run(registry.invoke(call, ctx)) for call in calls]
    assert all(result.is_error and "denied" in result.content for result in results)
    assert len(prompts) == 2
    assert list(workspace.iterdir()) == []
    assert repls._repls == {}, "denied code must not even start an interpreter"


def test_session_decision_is_only_one_call_and_never_remembered(python_tool):
    registry, _repls, workspace = python_tool
    prompts = []

    async def approve(call):
        prompts.append(call)
        return "session"

    grants = set()
    ctx = _ctx(workspace, approve, session_grants=grants)
    for source in ["print('one')", "print('two')", "print('two')"]:
        assert not asyncio.run(registry.invoke(_call(source), ctx)).is_error
    assert len(prompts) == 3
    assert grants == set()


@pytest.mark.parametrize("mutation_target", ["original", "disclosed"])
def test_mutated_pending_arguments_refuse_until_fresh_approval(python_tool, mutation_target):
    registry, repls, workspace = python_tool
    original = _call("open('original.txt', 'w').write('original')")
    changed = "open('changed.txt', 'w').write('changed')"

    async def approve(disclosed):
        target = original if mutation_target == "original" else disclosed
        target.arguments["code"] = changed
        return True

    result = asyncio.run(registry.invoke(original, _ctx(workspace, approve)))
    assert result.is_error and "changed" in result.content.lower()
    assert list(workspace.iterdir()) == []
    assert repls._repls == {}

    async def fresh_approval(call):
        assert call.arguments["code"] == changed
        return True

    result = asyncio.run(registry.invoke(_call(changed), _ctx(workspace, fresh_approval)))
    assert not result.is_error
    assert (workspace / "changed.txt").read_bytes() == b"changed"
    assert not (workspace / "original.txt").exists()


def test_cancellation_during_prompt_prevents_execution(python_tool):
    registry, repls, workspace = python_tool
    cancelled = threading.Event()

    async def approve(call):
        cancelled.set()
        return True

    with pytest.raises(RequestCancelledError):
        asyncio.run(registry.invoke(
            _call("open('cancelled.txt', 'w').write('bad')"),
            _ctx(workspace, approve, cancel=CancelToken(cancelled)),
        ))
    assert list(workspace.iterdir()) == []
    assert repls._repls == {}


@pytest.mark.parametrize("source", ["#" * 20_001, 42, None, "  \n\t"], ids=["oversized", "number", "null", "blank"])
def test_invalid_or_oversized_source_is_rejected_without_prompt_or_execution(python_tool, source):
    registry, repls, workspace = python_tool
    prompts = []

    async def approve(call):
        prompts.append(call)
        return True

    result = asyncio.run(registry.invoke(_call(source), _ctx(workspace, approve)))
    assert result.is_error
    assert prompts == []
    assert list(workspace.iterdir()) == []
    assert repls._repls == {}


def test_accepted_source_reaches_interpreter_without_stripping(python_tool, monkeypatch):
    registry, repls, workspace = python_tool
    source = "\n# " + "padding " * 70 + "\nprint('tail-visible')\n\n"
    observed = []
    repl = repls.get("python-approval-fixture", workspace, manage_cwd=False)
    execute = repl.execute

    def observe(code):
        observed.append(code)
        return execute(code)

    monkeypatch.setattr(repl, "execute", observe)

    async def approve(call):
        assert call.arguments["code"] == source
        return True

    result = asyncio.run(registry.invoke(_call(source), _ctx(workspace, approve)))
    assert not result.is_error and result.content.strip() == "tail-visible"
    assert observed == [source]


@pytest.mark.parametrize("character", ["\x00", "\x1b", "\x85", "\u202e", "\ufeff", "\ud800"])
def test_invisible_controls_are_explicitly_refused_not_hidden(python_tool, character):
    registry, repls, workspace = python_tool

    async def unexpected_approval(call):
        pytest.fail("unsupported source must be rejected before disclosure")

    result = asyncio.run(registry.invoke(_call("# " + character), _ctx(workspace, unexpected_approval)))
    assert result.is_error and "unsupported invisible character" in result.content
    assert repls._repls == {}


def test_builder_exposure_discloses_full_source_but_cannot_execute_without_workspace(python_tool):
    from backend.builder import _approval_summary as builder_summary

    registry, repls, workspace = python_tool
    source = "# " + "harmless " * 60 + f"\nopen({str(workspace / 'outside-context.txt')!r}, 'w').write('bad')\n"

    async def approve(call):
        assert builder_summary(call, None) == "python.exec\n--- source\n" + source
        return True

    result = asyncio.run(registry.invoke(
        _call(source), RunContext(granted_scopes=frozenset({CODE_EXECUTE}), request_approval=approve),
    ))
    assert result.is_error and "No harness workspace" in result.content
    assert repls._repls == {} and list(workspace.iterdir()) == []


def test_python_approval_intents_bind_each_disclosure_and_reject_other_resolvers():
    """Real intent registration/bus/dispatcher; pending futures are fixtures.

    The live-loop test below checks those futures/tokens are actually issued
    by the production harness. Browser tests exercise the WebSocket transport.
    """
    from backend.agents import AgentDispatcher
    from backend.api.intents_code_sandbox import register_code_sandbox_intents
    from backend.api.intents_harness import register_harness_intents
    from backend.domain.graph import SceneDocument
    from backend.events import SessionBus
    from backend.notifications import NotificationState

    async def run():
        document = SceneDocument()
        bus = SessionBus("python-approval-test")
        notifications = NotificationState()
        dispatcher = AgentDispatcher(None)
        register_harness_intents(bus, document, notifications, dispatcher)
        register_code_sandbox_intents(bus, document, notifications, dispatcher)
        loop = asyncio.get_running_loop()
        handle = dispatcher._runs.claim("harness", approval_future=loop.create_future())
        handle.approval_token = "first-disclosure"
        first = handle.approval_future

        for topic, intent, tail in [
            ("scene", "approveCodeExecution", []),
            ("scene", "denyCodeExecution", []),
            ("harness", "approveTool", []),
            ("harness", "approveTool", ["wrong-disclosure"]),
            ("harness", "approveToolForSession", []),
            ("harness", "answer", ["yes"]),
        ]:
            await bus.dispatch_intent(topic, intent, [handle.request_id, *tail])
            assert not first.done(), f"{topic}/{intent} must not authorize the Python prompt"

        await bus.dispatch_intent("harness", "approveTool", [handle.request_id, "first-disclosure"])
        assert first.result() is True
        handle.approval_future = loop.create_future()
        handle.approval_token = "second-disclosure"
        second = handle.approval_future
        await bus.dispatch_intent("harness", "approveTool", [handle.request_id, "first-disclosure"])
        assert not second.done(), "an old confirmation cannot authorize the next call in the same run"
        await bus.dispatch_intent("harness", "denyTool", [handle.request_id, "second-disclosure"])
        assert second.result() is False
        await bus.dispatch_intent("harness", "approveTool", [handle.request_id, "second-disclosure"])
        assert second.result() is False, "double-submit cannot turn a denial into approval"
        handle.approval_future = loop.create_future()
        handle.approval_token = ""
        other_tool = handle.approval_future
        await bus.dispatch_intent("harness", "approveTool", [handle.request_id, "second-disclosure"])
        assert not other_tool.done(), "a Python confirmation cannot authorize a later unrelated tool"
        await bus.dispatch_intent("harness", "approveTool", [handle.request_id])
        assert other_tool.result() is True, "existing unrelated-tool approval remains compatible"
        dispatcher._runs.release(handle.request_id)

    asyncio.run(run())


def test_harness_issues_distinct_python_disclosures_without_session_option(tmp_path, monkeypatch):
    from backend.agents import AgentDispatcher
    from backend.api.intents_harness import register_harness_intents
    from backend.domain.graph import SceneDocument
    from backend.events import SessionBus
    from backend.harness import workspace as workspace_module
    from backend.harness.loop import run_harness
    from backend.notifications import NotificationState
    from backend.tests.test_harness import scripted_turns

    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "workspaces")
    source = "# " + "visible padding " * 40 + "\nprint('approved fixture')\n"
    scripted_turns(monkeypatch, [
        {"tool_calls": [_call(source, "first"), _call(source, "second")]},
        {"content": "done"},
    ])

    async def run():
        document = SceneDocument()
        node = document.add_harness_node(0, 0, "harmless approval fixture")
        dispatcher = AgentDispatcher(None)
        bus = SessionBus("python-live-loop")
        bus.register_topic("scene", document.scene_payload)
        notifications = NotificationState()
        register_harness_intents(bus, document, notifications, dispatcher)
        registry = ToolRegistry()
        repls = PythonReplRegistry()
        register_harness_python_tool(registry, repls)
        cancelled = threading.Event()
        handle = dispatcher._runs.claim("harness", node_id=node.id, cancel_event=cancelled)
        observed = []

        async def decide():
            while len(observed) < 2:
                token = node.state.harness_approval_id
                if node.state.harness_awaiting_approval and token not in observed:
                    assert token
                    assert node.state.harness_approval_summary == "python.exec\n--- source\n" + source
                    assert node.state.harness_approval_session_offered is False
                    observed.append(token)
                    await bus.dispatch_intent("harness", "approveTool", [handle.request_id, token])
                await asyncio.sleep(0)

        try:
            await asyncio.wait_for(asyncio.gather(
                run_harness(
                    document=document, dispatcher=dispatcher, registry=registry, bus=bus, notifications=None,
                    harness_node_id=node.id, user_text="go", request_id=handle.request_id,
                    handle=handle, cancel_event=cancelled,
                ),
                decide(),
            ), timeout=10)
            assert len(observed) == 2 and observed[0] != observed[1]
            assert node.state.harness_status == "done"
            assert node.state.harness_approval_id == handle.approval_token == ""
        finally:
            dispatcher._runs.release(handle.request_id)
            repls.stop_all()

    asyncio.run(run())
