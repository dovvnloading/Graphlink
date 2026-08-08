"""ADR-007 stage 7.2: ToolRegistry - scopes + approval policy wiring.

Exit criterion this file proves: an out-of-scope call is denied BEFORE its
handler ever runs, and a destructive (non-"auto") tool always goes through
RunContext.request_approval before its handler runs - the two invariants
backend/tools.py's own module docstring names.
"""

from __future__ import annotations

import asyncio

import api_provider
import pytest

from backend.providers import CancelToken, ToolCall
from backend.tools import (
    CODE_EXECUTE,
    FS_READ,
    GRAPH_MUTATE,
    GRAPH_READ,
    NET_FETCH,
    ToolRegistry,
    ToolResult,
    RunContext,
)
from backend.providers.base import ToolSpec

ECHO_SPEC = ToolSpec(name="echo", description="Echoes back.", input_schema={"type": "object"})


def _run(coro):
    return asyncio.run(coro)


async def _echo_handler(call: ToolCall, ctx: RunContext) -> ToolResult:
    return ToolResult(content=f"echo: {call.arguments.get('message', '')}")


def _always_approve(call: ToolCall) -> bool:
    return True


def _always_deny(call: ToolCall) -> bool:
    return False


def _ctx(granted_scopes=(), approve=_always_approve, cancel=None) -> RunContext:
    async def request_approval(call: ToolCall) -> bool:
        return approve(call)

    return RunContext(granted_scopes=frozenset(granted_scopes), request_approval=request_approval, cancel=cancel)


# -- register() validation ------------------------------------------------


def test_register_rejects_an_unknown_scope():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="Unknown scope"):
        registry.register(ECHO_SPEC, _echo_handler, scopes={"not.a.real.scope"}, approval="auto")


def test_register_rejects_an_unknown_approval_policy():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="Unknown approval policy"):
        registry.register(ECHO_SPEC, _echo_handler, scopes={GRAPH_READ}, approval="sometimes")


def test_register_rejects_a_duplicate_tool_name():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={GRAPH_READ}, approval="auto")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ECHO_SPEC, _echo_handler, scopes={GRAPH_READ}, approval="auto")


def test_specs_returns_every_registered_tools_neutral_spec():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={GRAPH_READ}, approval="auto")
    assert registry.specs() == (ECHO_SPEC,)


# -- unknown tool -----------------------------------------------------------


def test_invoke_an_unregistered_tool_is_an_error_result_not_a_raise():
    registry = ToolRegistry()
    result = _run(registry.invoke(ToolCall(id="1", name="nope", arguments={}), _ctx()))
    assert result.is_error is True
    assert "Unknown tool" in result.content


# -- scope gate: exit criterion "out-of-scope call denied pre-handler" -----


def test_out_of_scope_call_is_denied_before_the_handler_runs():
    registry = ToolRegistry()
    handler_calls = []

    async def tracked_handler(call, ctx):
        handler_calls.append(call)
        return ToolResult(content="should never run")

    registry.register(ECHO_SPEC, tracked_handler, scopes={GRAPH_MUTATE}, approval="auto")

    result = _run(registry.invoke(
        ToolCall(id="1", name="echo", arguments={}), _ctx(granted_scopes={GRAPH_READ}),
    ))

    assert result.is_error is True
    assert "scope" in result.content.lower()
    assert handler_calls == [], "the handler must never run for an out-of-scope call"


def test_in_scope_call_reaches_the_handler():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={GRAPH_READ}, approval="auto")

    result = _run(registry.invoke(
        ToolCall(id="1", name="echo", arguments={"message": "hi"}), _ctx(granted_scopes={GRAPH_READ, GRAPH_MUTATE}),
    ))

    assert result == ToolResult(content="echo: hi")


def test_a_run_must_be_granted_every_scope_the_tool_requires_not_just_one():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={GRAPH_READ, FS_READ}, approval="auto")

    result = _run(registry.invoke(
        ToolCall(id="1", name="echo", arguments={}), _ctx(granted_scopes={GRAPH_READ}),  # missing FS_READ
    ))
    assert result.is_error is True


# -- approval policy: exit criterion "destructive tool prompts approval" ---


def test_auto_policy_never_calls_request_approval():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={GRAPH_READ}, approval="auto")
    prompted = []

    async def request_approval(call):
        prompted.append(call)
        return True

    ctx = RunContext(granted_scopes=frozenset({GRAPH_READ}), request_approval=request_approval)
    result = _run(registry.invoke(ToolCall(id="1", name="echo", arguments={}), ctx))

    assert result.is_error is False
    assert prompted == [], "an auto-policy tool must never prompt for approval"


def test_once_policy_prompts_every_single_call_even_with_identical_arguments():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={CODE_EXECUTE}, approval="once")
    prompted = []

    async def request_approval(call):
        prompted.append(call)
        return True

    ctx = RunContext(granted_scopes=frozenset({CODE_EXECUTE}), request_approval=request_approval)
    call = ToolCall(id="1", name="echo", arguments={"message": "hi"})
    _run(registry.invoke(call, ctx))
    _run(registry.invoke(call, ctx))

    assert len(prompted) == 2, "'once' must re-prompt every call, unlike 'always'"


def test_destructive_tool_is_denied_when_approval_is_refused_and_the_handler_never_runs():
    registry = ToolRegistry()
    handler_calls = []

    async def tracked_handler(call, ctx):
        handler_calls.append(call)
        return ToolResult(content="should never run")

    registry.register(ECHO_SPEC, tracked_handler, scopes={CODE_EXECUTE}, approval="once")

    result = _run(registry.invoke(
        ToolCall(id="1", name="echo", arguments={}),
        _ctx(granted_scopes={CODE_EXECUTE}, approve=_always_deny),
    ))

    assert result.is_error is True
    assert "denied" in result.content.lower()
    assert handler_calls == []


def test_always_policy_remembers_an_approved_fingerprint_and_skips_the_second_prompt():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={NET_FETCH}, approval="always")
    prompted = []

    async def request_approval(call):
        prompted.append(call)
        return True

    ctx = RunContext(granted_scopes=frozenset({NET_FETCH}), request_approval=request_approval)
    call = ToolCall(id="1", name="echo", arguments={"message": "hi"})

    first = _run(registry.invoke(call, ctx))
    second = _run(registry.invoke(ToolCall(id="2", name="echo", arguments={"message": "hi"}), ctx))

    assert first.is_error is False and second.is_error is False
    assert len(prompted) == 1, "'always' must not re-prompt an identical (name, arguments) call in the same run"


def test_always_policy_still_prompts_for_a_call_with_different_arguments():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={NET_FETCH}, approval="always")
    prompted = []

    async def request_approval(call):
        prompted.append(call)
        return True

    ctx = RunContext(granted_scopes=frozenset({NET_FETCH}), request_approval=request_approval)
    _run(registry.invoke(ToolCall(id="1", name="echo", arguments={"message": "hi"}), ctx))
    _run(registry.invoke(ToolCall(id="2", name="echo", arguments={"message": "different"}), ctx))

    assert len(prompted) == 2


def test_always_policy_memory_does_not_leak_across_separate_run_contexts():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={NET_FETCH}, approval="always")
    prompted = []

    async def request_approval(call):
        prompted.append(call)
        return True

    call = ToolCall(id="1", name="echo", arguments={"message": "hi"})
    ctx_a = RunContext(granted_scopes=frozenset({NET_FETCH}), request_approval=request_approval)
    ctx_b = RunContext(granted_scopes=frozenset({NET_FETCH}), request_approval=request_approval)

    _run(registry.invoke(call, ctx_a))
    _run(registry.invoke(call, ctx_b))

    assert len(prompted) == 2, "approval memory is per-RunContext, never global to the registry"


# -- handler exceptions become error results, not raises --------------------


def test_a_raising_handler_becomes_an_error_result_not_an_exception():
    registry = ToolRegistry()

    async def broken_handler(call, ctx):
        raise RuntimeError("boom")

    registry.register(ECHO_SPEC, broken_handler, scopes={GRAPH_READ}, approval="auto")
    result = _run(registry.invoke(ToolCall(id="1", name="echo", arguments={}), _ctx(granted_scopes={GRAPH_READ})))

    assert result.is_error is True
    assert "boom" in result.content


# -- cancellation: mirrors every Provider.stream()'s own first line ---------


def test_invoke_raises_the_same_cancellation_error_a_provider_would():
    import threading

    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={GRAPH_READ}, approval="auto")

    cancel_event = threading.Event()
    cancel_event.set()
    ctx = _ctx(granted_scopes={GRAPH_READ}, cancel=CancelToken(cancel_event))

    with pytest.raises(api_provider.RequestCancelledError):
        _run(registry.invoke(ToolCall(id="1", name="echo", arguments={}), ctx))


def test_invoke_with_no_cancel_token_never_raises_for_cancellation():
    registry = ToolRegistry()
    registry.register(ECHO_SPEC, _echo_handler, scopes={GRAPH_READ}, approval="auto")
    result = _run(registry.invoke(ToolCall(id="1", name="echo", arguments={}), _ctx(granted_scopes={GRAPH_READ})))
    assert result.is_error is False


def test_a_cancel_that_lands_during_a_long_approval_wait_still_raises_and_never_runs_the_handler():
    """The approval prompt is the one place invoke() can be awaiting for a
    genuinely long time (a human deciding) - cancellation that arrives DURING
    that wait must still be caught on return, not just at entry."""
    import threading

    registry = ToolRegistry()
    handler_calls = []

    async def tracked_handler(call, ctx):
        handler_calls.append(call)
        return ToolResult(content="should never run")

    registry.register(ECHO_SPEC, tracked_handler, scopes={CODE_EXECUTE}, approval="once")

    cancel_event = threading.Event()

    async def request_approval(call):
        cancel_event.set()  # simulates a cancel landing while the human is deciding
        return True

    ctx = RunContext(
        granted_scopes=frozenset({CODE_EXECUTE}),
        request_approval=request_approval,
        cancel=CancelToken(cancel_event),
    )

    with pytest.raises(api_provider.RequestCancelledError):
        _run(registry.invoke(ToolCall(id="1", name="echo", arguments={}), ctx))
    assert handler_calls == []
