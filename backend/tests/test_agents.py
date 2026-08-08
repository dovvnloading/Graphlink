"""Agent-dispatch service tests (Qt-removal plan R4).

Mocks `api_provider.chat` directly (mirroring
graphlink_app/tests/test_provider_state_snapshot.py's monkeypatch-provider-
globals pattern) rather than a deeper transport layer - this validates the
real wiring end to end (event loop -> asyncio.to_thread -> ChatWorker ->
api_provider.chat -> back through the WATCHDOG_TIMEOUT_SECONDS/cancellation
plumbing) without ever needing a live Ollama daemon, a real API key, or real
network access, while still catching a wiring bug in the new dispatch code
itself.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# R7.2: api_provider/graphlink_task_config/graphlink_settings_store (imported
# below, directly or via backend.agents) sit at the repo root, a sibling of
# backend/ - already on sys.path whenever this package is, no ordering
# constraint relative to the import below.
import backend.agents as agents_module
from backend.agents import AgentDispatcher
from backend.canvas import SceneDocument, register_canvas
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.tests.conftest import (
    artifact_slots,
    busy_count,
    chat_slots,
    code_sandbox_slots,
    drain_runs,
    gitlink_apply_slots,
    gitlink_run_slots,
    image_slots,
    pycoder_slots,
    web_research_slots,
)

import api_provider
import graphlink_task_config as config
from graphlink_settings_store import SettingsManager
from graphlink_plugins.web_research.domain import RequestCancelled, ResearchFailure
from graphlink_scratch_dirs import EXECUTION_SANDBOX_ROOT


class _FakeSettingsManager:
    """A minimal stand-in exposing only what AgentDispatcher.persona() reads -
    the bootstrap_provider_state tests below use a real SettingsManager
    instead, since they exercise its persistence surface directly."""

    def __init__(self, enable_system_prompt: bool = True):
        self._enable_system_prompt = enable_system_prompt

    def get_enable_system_prompt(self) -> bool:
        return self._enable_system_prompt


def _make_dispatch_env(enable_system_prompt: bool = True):
    bus = SessionBus("agents-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    composer_document = ComposerDocument()
    bus.register_topic("app-composer", composer_document.payload)
    # A real "scene" topic - the success path publishes it after on_reply.
    bus.register_topic("scene", lambda: {})
    dispatcher = AgentDispatcher(_FakeSettingsManager(enable_system_prompt))
    return bus, notifications, composer_document, dispatcher


def _configure_fake_ollama(monkeypatch, chat_fn, *, model="test-model"):
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, model)
    monkeypatch.setattr(api_provider, "chat", chat_fn)

    # R4.4: start_chat_reply now always streams (backend/agents.py's
    # _call_chat_agent_stream always passes a non-None on_chunk), so
    # ChatWorker.run always calls api_provider.chat_stream, never
    # api_provider.chat, for that path. Every pre-existing test in this file
    # built around mocking api_provider.chat alone - most of which predate
    # R4.4 - would otherwise fall through to chat_stream's REAL Ollama
    # branch (since these fixtures also set LOCAL_PROVIDER_TYPE to Ollama)
    # and attempt a genuine network call. This fake delivers the reply as
    # one blocking call plus one synthetic full-text on_chunk - the shape
    # the real chat_stream's non-Ollama fallback used before ADR-006 stage
    # 6.5b made every provider stream for real; it remains a valid TEST
    # double because on_chunk's contract is delta-agnostic - just
    # delegating the blocking call to chat_fn instead of the real chat(),
    # so chat_fn's return value/exception/cancellation behavior is
    # preserved unchanged for both dispatch paths. Tests that care about
    # the streaming semantics THEMSELVES (batching, reset events, ...)
    # monkeypatch api_provider.chat_stream directly instead - see the
    # "R4.4: true token streaming" section below.
    #
    # Calls api_provider.chat (the module attribute, looked up fresh on
    # every invocation) rather than closing over chat_fn directly - this
    # matters for tests that re-monkeypatch api_provider.chat again later
    # (e.g. simulating a third, different reply after the first call
    # completes): this fallback must see that later reassignment too, same
    # as the real chat_stream's own fallback branch would.
    def _fallback_chat_stream(task, messages, on_chunk, **kwargs):
        response = api_provider.chat(task, messages, **kwargs)
        on_chunk(response["message"].get("content", ""), False)
        return response

    monkeypatch.setattr(api_provider, "chat_stream", _fallback_chat_stream)


def _configure_fake_chat_stream(monkeypatch, chat_stream_fn, *, model="test-model"):
    """Sibling of _configure_fake_ollama for tests that care about the
    streaming semantics THEMSELVES (batching, cancel-mid-stream, reset
    events, ...) rather than delegating through a plain chat_fn - sets the
    same is_configured()-satisfying provider state, then installs
    chat_stream_fn directly as api_provider.chat_stream (no synthetic
    fallback wrapper in between, unlike _configure_fake_ollama's own)."""
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, model)
    monkeypatch.setattr(api_provider, "chat_stream", chat_stream_fn)


# -- 1. successful reply ------------------------------------------------------


def test_successful_reply_calls_on_reply_with_the_agent_text(monkeypatch):
    _configure_fake_ollama(monkeypatch, lambda task, messages, **kwargs: {"message": {"content": "canned reply"}})

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        replies = []
        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
        )
        # The reply happens inside a scheduled (not awaited) task - grab the
        # task reference start_chat_reply left in the registry and await it
        # directly rather than assuming start_chat_reply itself blocks.
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert replies == ["canned reply"]
        assert chat_slots(dispatcher) == {}
        assert composer_document.request_state == "idle"

    asyncio.run(run())


# -- 2. provider-not-configured clean error ----------------------------------


def test_provider_not_configured_returns_quickly_with_an_error_notification(monkeypatch):
    chat_calls = []
    _configure_fake_ollama(
        monkeypatch,
        lambda task, messages, **kwargs: chat_calls.append(1),
        model="",  # empty -> is_configured() is False
    )

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[],
            on_reply=lambda text: None,
        )

        assert chat_slots(dispatcher) == {}, "no task/thread work started"
        assert chat_calls == [], "api_provider.chat was never reached"
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == (
            "No AI provider is configured yet. Open Settings to choose Ollama, "
            "Llama.cpp, or an API provider."
        )

    asyncio.run(run())


# -- 3. cancellation mid-flight -----------------------------------------------


def test_cancellation_mid_flight_fires_info_notification_and_clears_registry(monkeypatch):
    started = threading.Event()

    def blocking_then_cancelled(task, messages, cancellation_event=None, **kwargs):
        started.set()
        while not cancellation_event.is_set():
            time.sleep(0.01)
        raise api_provider.RequestCancelledError("Request cancelled.")

    _configure_fake_ollama(monkeypatch, blocking_then_cancelled)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=lambda text: None,
        )
        request_id, entry = next(iter(chat_slots(dispatcher).items()))

        # Wait until the worker thread has actually entered chat() before
        # cancelling, so this is a genuine mid-flight cancel.
        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel(request_id) is True

        await entry["task"]

        assert chat_slots(dispatcher) == {}
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "Request cancelled."

    asyncio.run(run())


def test_cancel_returns_false_for_an_unknown_request_id():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    assert dispatcher.cancel("no-such-request") is False


# -- 4. timeout ---------------------------------------------------------------


def test_timeout_fires_the_exact_message_and_clears_registry(monkeypatch):
    monkeypatch.setattr(agents_module, "WATCHDOG_TIMEOUT_SECONDS", 0.05)

    def slow_chat(task, messages, **kwargs):
        time.sleep(0.3)
        return {"message": {"content": "too late"}}

    _configure_fake_ollama(monkeypatch, slow_chat)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=lambda text: None,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert chat_slots(dispatcher) == {}
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == (
            "The model stopped responding before the request completed. "
            "Please try again or choose a faster model."
        )

    asyncio.run(run())


# -- 5. concurrent same-session guard -----------------------------------------


def test_concurrent_calls_second_rejected_first_completes_third_succeeds(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        started.set()
        release.wait(5)
        return {"message": {"content": "first reply"}}

    _configure_fake_ollama(monkeypatch, blocking_chat)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        replies = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "first"}],
            on_reply=replies.append,
        )
        await asyncio.to_thread(started.wait, 5)

        # Second call while the first is still in flight must be rejected
        # and must not disturb the first request.
        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "second"}],
            on_reply=replies.append,
        )
        assert notifications.visible is True
        assert notifications.message == "A response is already being generated."
        assert len(chat_slots(dispatcher)) == 1

        release.set()
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]
        assert replies == ["first reply"]
        assert chat_slots(dispatcher) == {}

        # Third call, after the first has fully completed, succeeds normally.
        monkeypatch.setattr(api_provider, "chat", lambda task, messages, **kwargs: {"message": {"content": "third reply"}})
        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "third"}],
            on_reply=replies.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]
        assert replies == ["first reply", "third reply"]

    asyncio.run(run())


# -- persona() -----------------------------------------------------------------


def test_persona_is_empty_when_system_prompt_disabled():
    dispatcher = AgentDispatcher(_FakeSettingsManager(enable_system_prompt=False))
    assert dispatcher.persona() == ""


def test_persona_is_the_base_persona_text_when_enabled():
    dispatcher = AgentDispatcher(_FakeSettingsManager(enable_system_prompt=True))
    persona_text = dispatcher.persona()
    assert persona_text
    # ADR-006 stage 6.7: the "chat-system-core" v2 identity - one identity,
    # the Graphlink Assistant; the retired "Vertex" alias must never return.
    assert "Graphlink Assistant" in persona_text
    assert "Vertex" not in persona_text


# -- R6.1: _resolve_branch_system_prompt (System Prompt note override) --------
#
# Legacy graphlink_chat_agent.py's resolve_branch_system_prompt, ported: a
# note (kind="note", is_system_prompt=True) connected note -> root REPLACES
# persona()'s resolution entirely for any send on that branch. These first
# few tests call _resolve_branch_system_prompt directly against a bare
# SceneDocument (no dispatch pipeline involved) - fast, precise unit
# coverage of the resolution logic itself; the send_message/regenerate_
# response end-to-end tests further below prove the real wiring.


def test_resolve_branch_system_prompt_returns_none_with_no_canvas_context():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    document = SceneDocument()
    root = document.add_chat_node(0, 0, "root message", True)
    assert dispatcher._resolve_branch_system_prompt(None, root.id) is None
    assert dispatcher._resolve_branch_system_prompt(document, None) is None


def test_resolve_branch_system_prompt_returns_none_when_no_note_is_attached():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    document = SceneDocument()
    root = document.add_chat_node(0, 0, "root message", True)
    assert dispatcher._resolve_branch_system_prompt(document, root.id) is None


def test_resolve_branch_system_prompt_ignores_a_note_not_marked_is_system_prompt():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    document = SceneDocument()
    root = document.add_chat_node(0, 0, "root message", True)
    plain_note = document.add_note(0, -150)  # is_system_prompt=False (default)
    document.connect(plain_note.id, root.id)
    assert dispatcher._resolve_branch_system_prompt(document, root.id) is None


def test_resolve_branch_system_prompt_finds_a_note_attached_to_the_true_branch_root():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    document = SceneDocument()
    root = document.add_chat_node(0, 0, "root message", True)
    mid = document.add_chat_node(0, 100, "mid reply", False, parent_id=root.id)
    leaf = document.add_chat_node(0, 200, "leaf message", True, parent_id=mid.id)

    note = document.add_note(0, -150, is_system_prompt=True)
    document.set_note_content(note.id, "Custom branch persona.")
    document.connect(note.id, root.id)

    # Resolving from the LEAF (2 hops from root) must still find the note
    # attached to the TRUE root, not stop early at the immediate parent.
    assert dispatcher._resolve_branch_system_prompt(document, leaf.id) == "Custom branch persona."
    assert dispatcher._resolve_branch_system_prompt(document, mid.id) == "Custom branch persona."
    assert dispatcher._resolve_branch_system_prompt(document, root.id) == "Custom branch persona."


def test_resolve_branch_system_prompt_note_does_not_leak_into_chat_branch_history():
    # Regression guard for the exact hazard _branch_parent_edge exists to
    # prevent: the note -> root edge must NOT make chat_branch_history (the
    # real conversation_history builder) treat the note as a fake extra
    # parent turn, and must NOT make get_branch_root resolve to the note
    # itself instead of the true chat root.
    document = SceneDocument()
    root = document.add_chat_node(0, 0, "root message", True)
    child = document.add_chat_node(0, 100, "child reply", False, parent_id=root.id)

    note = document.add_note(0, -150, is_system_prompt=True)
    document.set_note_content(note.id, "Custom branch persona.")
    document.connect(note.id, root.id)

    history = document.chat_branch_history(child.id)
    assert history == [
        {"role": "user", "content": "root message"},
        {"role": "assistant", "content": "child reply"},
    ]
    assert document.get_branch_root(child.id).id == root.id


# -- R6.1: end-to-end - sendMessage/regenerateResponse actually resolve the
# note override through the real dispatch pipeline ----------------------------


def _configure_fake_ollama_provider_only(monkeypatch, *, model="test-model"):
    # Sets just enough api_provider/config state for is_configured() to
    # return True, WITHOUT installing a chat/chat_stream fake - these tests
    # monkeypatch backend.agents._call_chat_agent_stream directly instead
    # (one level below ChatAgent/api_provider.chat_stream), to assert
    # exactly what persona_text _dispatch resolved and handed it, without
    # depending on ChatAgent's own system-prompt-string-building internals.
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, model)


def test_send_message_uses_the_branch_attached_system_prompt_note_instead_of_the_default(monkeypatch):
    _configure_fake_ollama_provider_only(monkeypatch)
    captured = {}

    def fake_stream(conversation_history, persona_text, cancel_event, on_chunk, *,
                    persona_is_override=False, **kwargs):
        # ADR-006 stage 6.7: the override path now passes
        # persona_is_override=True (raw passthrough) - captured below.
        # **kwargs absorbs 6.6's always-passed on_context_trimmed.
        captured["persona_text"] = persona_text
        captured["persona_is_override"] = persona_is_override
        on_chunk("a reply", False)
        return "a reply"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", fake_stream)

    async def run():
        bus = SessionBus("agents-system-prompt-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        dispatcher = AgentDispatcher(_FakeSettingsManager(enable_system_prompt=True))
        document = register_canvas(bus, notifications, dispatcher, composer_document)

        # Seed a branch root manually, attach a System Prompt note to it,
        # then continue the branch through the real "sendMessage" intent -
        # proving the resolved persona comes from the note, not persona()'s
        # BASE_SYSTEM_PROMPT default.
        root = document.add_chat_node(0, 0, "root message", True)
        document.last_chat_node_id = root.id
        note = document.add_note(0, -150, is_system_prompt=True)
        document.set_note_content(note.id, "Custom branch persona.")
        document.connect(note.id, root.id)

        await bus.dispatch_intent("scene", "sendMessage", ["continue the branch"])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert captured["persona_text"] == "Custom branch persona."
        assert "Graphlink Assistant" not in captured["persona_text"]  # the default never got a look-in
        # ADR-006 stage 6.7: an override is flagged so it reaches the wire
        # RAW, never wrapped in "You are Graphlink Assistant. {override}".
        assert captured["persona_is_override"] is True

    asyncio.run(run())


def test_send_message_falls_back_to_the_default_persona_when_no_note_is_attached(monkeypatch):
    _configure_fake_ollama_provider_only(monkeypatch)
    captured = {}

    def fake_stream(conversation_history, persona_text, cancel_event, on_chunk, **kwargs):
        captured["persona_text"] = persona_text
        on_chunk("a reply", False)
        return "a reply"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", fake_stream)

    async def run():
        bus = SessionBus("agents-system-prompt-fallback-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        dispatcher = AgentDispatcher(_FakeSettingsManager(enable_system_prompt=True))
        register_canvas(bus, notifications, dispatcher, composer_document)

        await bus.dispatch_intent("scene", "sendMessage", ["first message, no note attached"])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert captured["persona_text"] == dispatcher.persona()

    asyncio.run(run())


def test_regenerate_response_also_resolves_the_branch_attached_system_prompt_note(monkeypatch):
    # regenerate_response's call site passes node_id=parent_id (not the
    # node being regenerated) - proves that wiring independently of
    # send_message's own node_id=node.id wiring above.
    _configure_fake_ollama_provider_only(monkeypatch)
    captured = {}

    # ADR-006 stage 6.4: regenerate now streams, so the STREAMING driver is
    # the one that must see the resolved persona.
    def fake_stream(conversation_history, persona_text, cancel_event, on_chunk, *,
                    persona_is_override=False, **kwargs):
        # ADR-006 stage 6.7: this is an override-path dispatch, so the fake
        # must accept the persona_is_override kwarg (only override-path
        # dispatches pass it - default-path fakes keep the pre-6.7 arity).
        # **kwargs absorbs 6.6's always-passed on_context_trimmed.
        captured["persona_text"] = persona_text
        on_chunk("regenerated reply", False)
        return "regenerated reply"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", fake_stream)

    async def run():
        bus = SessionBus("agents-regenerate-system-prompt-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        dispatcher = AgentDispatcher(_FakeSettingsManager(enable_system_prompt=True))
        document = register_canvas(bus, notifications, dispatcher, composer_document)

        root = document.add_chat_node(0, 0, "root message", True)
        assistant_reply = document.add_chat_node(0, 100, "old reply", False, parent_id=root.id)
        note = document.add_note(0, -150, is_system_prompt=True)
        document.set_note_content(note.id, "Custom branch persona.")
        document.connect(note.id, root.id)

        await bus.dispatch_intent("scene", "regenerateResponse", [assistant_reply.id])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert captured["persona_text"] == "Custom branch persona."

    asyncio.run(run())


# -- ADR-006 stage 6.7: system-prompt wire shape at the api_provider seam -----
#
# These drive the REAL _call_chat_agent_stream -> ChatAgent -> ChatWorker
# path with api_provider.chat_stream monkeypatched, asserting what actually
# reaches the wire: disabled -> NO system message at all, override -> the
# EXACT raw note text, default -> the composed "You are ..." core.


def _capture_chat_stream_messages(monkeypatch):
    import api_provider

    captured = {}

    def fake_chat_stream(*, task, messages, on_chunk, cancellation_event=None, **kwargs):
        captured["messages"] = messages
        return {"message": {"content": "a reply"}}

    monkeypatch.setattr(api_provider, "chat_stream", fake_chat_stream)
    return captured


def test_disabled_system_prompt_sends_no_system_message_at_all(monkeypatch):
    captured = _capture_chat_stream_messages(monkeypatch)
    history = [{"role": "user", "content": "hi"}]

    agents_module._call_chat_agent_stream(history, "", threading.Event(), lambda d, r: None)

    roles = [m["role"] for m in captured["messages"]]
    assert "system" not in roles  # disable genuinely disables (6.7 fix)
    assert captured["messages"] == history


def test_note_override_reaches_the_wire_raw_and_unwrapped(monkeypatch):
    captured = _capture_chat_stream_messages(monkeypatch)
    history = [{"role": "user", "content": "hi"}]

    agents_module._call_chat_agent_stream(
        history, "Custom branch persona.", threading.Event(), lambda d, r: None,
        persona_is_override=True,
    )

    system_messages = [m for m in captured["messages"] if m["role"] == "system"]
    assert len(system_messages) == 1
    assert system_messages[0]["content"] == "Custom branch persona."  # EXACT raw text


def test_default_persona_reaches_the_wire_as_the_composed_core(monkeypatch):
    from graphlink_prompts import BASE_SYSTEM_PROMPT

    captured = _capture_chat_stream_messages(monkeypatch)
    history = [{"role": "user", "content": "hi"}]

    agents_module._call_chat_agent_stream(
        history, BASE_SYSTEM_PROMPT, threading.Event(), lambda d, r: None
    )

    system_messages = [m for m in captured["messages"] if m["role"] == "system"]
    assert len(system_messages) == 1
    assert system_messages[0]["content"] == f"You are Graphlink Assistant. {BASE_SYSTEM_PROMPT}"


# -- ADR-006 stage 6.6: context-trim notification -----------------------------


def test_context_trim_signal_surfaces_a_notification(monkeypatch):
    # The dispatcher hands the chat driver a marshaling on_context_trimmed
    # closure; when the worker reports dropped turns, an info notification
    # must surface loop-side.
    _configure_fake_ollama_provider_only(monkeypatch)

    def fake_stream(conversation_history, persona_text, cancel_event, on_chunk, *,
                    on_context_trimmed, **kwargs):
        on_context_trimmed(7, True)  # as ChatWorker would, from the worker thread
        on_chunk("a reply", False)
        return "a reply"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", fake_stream)

    async def run():
        bus = SessionBus("agents-context-trim-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        dispatcher = AgentDispatcher(_FakeSettingsManager(enable_system_prompt=True))
        register_canvas(bus, notifications, dispatcher, composer_document)

        await bus.dispatch_intent("scene", "sendMessage", ["a long conversation"])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        payload = notifications.payload()
        assert "summarized" in str(payload).lower()

    asyncio.run(run())


# -- ADR-006 stage 6.8: real usage -> token counter + reply-node stamping -----


def test_real_usage_flows_to_the_token_counter_and_reply_node(monkeypatch):
    from backend.token_counter import TokenCounterState

    _configure_fake_ollama_provider_only(monkeypatch)

    def fake_stream(conversation_history, persona_text, cancel_event, on_chunk, *,
                    on_usage=None, **kwargs):
        on_chunk("a real reply", False)
        if on_usage is not None:
            on_usage({"prompt_tokens": 111, "completion_tokens": 22})
        return "a real reply"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", fake_stream)

    async def run():
        bus = SessionBus("agents-usage-flow-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        token_counter = TokenCounterState()
        bus.register_topic("token-counter", token_counter.payload)
        dispatcher = AgentDispatcher(_FakeSettingsManager(enable_system_prompt=True))
        document = register_canvas(
            bus, notifications, dispatcher, composer_document, token_counter
        )

        await bus.dispatch_intent("scene", "sendMessage", ["hello"])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        payload = token_counter.payload()
        assert payload["usageIsReal"] is True
        assert payload["promptTokens"] == 111
        assert payload["completionTokens"] == 22
        assert payload["totalTokens"] == 133  # exact, replaces the estimate sum

        reply_nodes = [
            n for n in document.nodes.values()
            if n.kind == "chat" and not n.state.is_user
        ]
        assert len(reply_nodes) == 1
        reply = reply_nodes[0]
        # Per-node stamping: real counts plus provider/model provenance
        # (ordinary replies now carry it, not just branch synthesis).
        assert reply.state.prompt_tokens == 111
        assert reply.state.completion_tokens == 22
        assert reply.state.provider == "ollama"
        assert reply.state.model  # the fake-configured chat model id
        # ADR-016 stage 16.2: local providers cost $0.00, not None (a real
        # answer, not "unknown") - stamped as a point-in-time snapshot
        # alongside the counts above.
        assert reply.state.estimated_cost_usd == 0.0
        assert payload["sessionPromptTokens"] == 111
        assert payload["sessionCompletionTokens"] == 22
        assert payload["sessionEstimatedCostUsd"] == 0.0

    asyncio.run(run())


# -- 6. bootstrap_provider_state -----------------------------------------------


def test_bootstrap_never_configured_settings_manager_does_not_raise(tmp_path):
    settings_manager = SettingsManager(tmp_path / "session.dat")
    agents_module.bootstrap_provider_state(settings_manager)  # must not raise


def test_bootstrap_api_endpoint_mode_calls_initialize_api_with_provider_key_and_base_url(tmp_path, monkeypatch):
    settings_manager = SettingsManager(tmp_path / "session.dat")
    settings_manager.set_current_mode(config.MODE_API_ENDPOINT)
    settings_manager.set_api_settings(
        config.API_PROVIDER_OPENAI,
        "https://example.test/v1",
        "sk-fake-openai-key",
        "",
        "",
    )
    settings_manager.set_api_models({config.TASK_CHAT: "gpt-test"}, config.API_PROVIDER_OPENAI)

    calls = []

    def fake_initialize_api(provider, api_key, base_url=None):
        calls.append((provider, api_key, base_url))
        return {"provider": provider}

    monkeypatch.setattr(api_provider, "initialize_api", fake_initialize_api)

    agents_module.bootstrap_provider_state(settings_manager)

    assert calls == [(config.API_PROVIDER_OPENAI, "sk-fake-openai-key", "https://example.test/v1")]
    assert settings_manager.get_current_mode() == config.MODE_API_ENDPOINT


def test_bootstrap_ollama_local_mode_seeds_initialize_local_provider_with_persisted_reasoning_level(
    tmp_path, monkeypatch, caplog
):
    # Regression test: _apply_mode's Ollama-local branch used to call the
    # removed get_ollama_reasoning_mode() under the wrong dict key
    # ("reasoning_mode" instead of "reasoning_level", the key
    # initialize_local_provider actually reads) - bootstrap_provider_state's
    # own broad except Exception silently swallowed the resulting
    # AttributeError every time, so the persisted reasoning level was never
    # actually applied on startup. Asserting BOTH the real call args AND that
    # no warning was logged catches a regression that "must not raise" alone
    # (see the fixture above) cannot.
    settings_manager = SettingsManager(tmp_path / "session.dat")
    settings_manager.set_current_mode(config.MODE_OLLAMA_LOCAL)
    settings_manager.set_ollama_reasoning_level("low")

    calls = []

    def fake_initialize_local_provider(provider, settings=None, *, preload_model=False):
        calls.append((provider, settings))

    monkeypatch.setattr(api_provider, "initialize_local_provider", fake_initialize_local_provider)

    with caplog.at_level("WARNING"):
        agents_module.bootstrap_provider_state(settings_manager)

    assert calls == [(config.LOCAL_PROVIDER_OLLAMA, {"reasoning_level": "low"})]
    assert settings_manager.get_current_mode() == config.MODE_OLLAMA_LOCAL
    assert not any(record.levelname == "WARNING" for record in caplog.records)


def test_bootstrap_falls_back_to_ollama_when_apply_mode_raises(tmp_path, monkeypatch, caplog):
    settings_manager = SettingsManager(tmp_path / "session.dat")
    settings_manager.set_current_mode(config.MODE_API_ENDPOINT)
    settings_manager.set_api_settings(config.API_PROVIDER_OPENAI, "https://example.test/v1", "sk-fake", "", "")
    settings_manager.set_api_models({config.TASK_CHAT: "gpt-test"}, config.API_PROVIDER_OPENAI)

    def raising_initialize_api(provider, api_key, base_url=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(api_provider, "initialize_api", raising_initialize_api)

    with caplog.at_level("WARNING"):
        agents_module.bootstrap_provider_state(settings_manager)  # must not raise

    assert settings_manager.get_current_mode() == config.MODE_OLLAMA_LOCAL
    assert settings_manager.get_current_mode() == "Ollama (Local)"
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_apply_mode_raises_value_error_for_an_unrecognized_mode(tmp_path):
    settings_manager = SettingsManager(tmp_path / "session.dat")
    with pytest.raises(ValueError):
        agents_module._apply_mode("Some Nonsense Mode", settings_manager)


# -- promoted from the R4 concurrency/security review's own adversarial probes -----


def test_two_sessions_concurrent_inflight_no_cross_contamination(monkeypatch):
    """Two DIFFERENT sessions (two separate AgentDispatcher/SceneDocument/
    ComposerDocument instances) with in-flight chat requests at the same
    time. Confirms no cross-contamination of replies/notifications/composer
    state - the property the whole per-session-instance design exists to
    guarantee, not previously exercised by any test with two REAL concurrent
    in-flight dispatchers."""
    a_started = threading.Event()
    b_started = threading.Event()
    a_release = threading.Event()
    b_release = threading.Event()

    def fake_chat(task, messages, **kwargs):
        # Identify which session's request this is purely from the message
        # content - if state ever leaked/crossed between the two
        # dispatchers, this is where it would show up as the wrong reply
        # landing on the wrong session.
        user_texts = [m["content"] for m in messages if m.get("role") == "user"]
        if "hello from A" in user_texts:
            a_started.set()
            a_release.wait(5)
            return {"message": {"content": "reply for A"}}
        if "hello from B" in user_texts:
            b_started.set()
            b_release.wait(5)
            return {"message": {"content": "reply for B"}}
        raise AssertionError(f"unexpected messages: {messages!r}")

    _configure_fake_ollama(monkeypatch, chat_fn=fake_chat)

    async def run():
        bus_a, notif_a, composer_a, dispatcher_a = _make_dispatch_env()
        bus_b, notif_b, composer_b, dispatcher_b = _make_dispatch_env()
        replies_a: list[str] = []
        replies_b: list[str] = []

        # Kick off both sessions' requests - neither awaited to completion.
        await dispatcher_a.start_chat_reply(
            bus=bus_a, notifications_state=notif_a, composer_document=composer_a,
            conversation_history=[{"role": "user", "content": "hello from A"}], on_reply=replies_a.append,
        )
        await dispatcher_b.start_chat_reply(
            bus=bus_b, notifications_state=notif_b, composer_document=composer_b,
            conversation_history=[{"role": "user", "content": "hello from B"}], on_reply=replies_b.append,
        )

        await asyncio.to_thread(a_started.wait, 5)
        await asyncio.to_thread(b_started.wait, 5)
        assert a_started.is_set() and b_started.is_set()

        assert len(chat_slots(dispatcher_a)) == 1
        assert len(chat_slots(dispatcher_b)) == 1
        assert set(chat_slots(dispatcher_a).keys()).isdisjoint(chat_slots(dispatcher_b).keys())
        assert composer_a.request_state == "generating"
        assert composer_b.request_state == "generating"

        # Release out of start order - completion order must not matter.
        b_release.set()
        await next(iter(chat_slots(dispatcher_b).values()))["task"]
        a_release.set()
        await next(iter(chat_slots(dispatcher_a).values()))["task"]

        assert replies_a == ["reply for A"]
        assert replies_b == ["reply for B"]
        assert chat_slots(dispatcher_a) == {}
        assert chat_slots(dispatcher_b) == {}
        assert composer_a.request_state == "idle"
        assert composer_b.request_state == "idle"
        assert notif_a.visible is False
        assert notif_b.visible is False

    asyncio.run(run())


def test_rapid_fire_double_send_same_session_second_is_rejected(monkeypatch):
    """start_chat_reply has no `await` between the `if self._runs.is_busy
    ("chat"):` check and the registry claim right after it - so two calls
    fired back-to-back on the same dispatcher, with no await of the first's
    completion in between, must never both be admitted. This is the closest
    this transport model gets to "two sendMessage frames arriving one right
    after another" on the same WS connection."""
    release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        release.wait(5)
        return {"message": {"content": "first"}}

    _configure_fake_ollama(monkeypatch, chat_fn=blocking_chat)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        replies: list[str] = []

        await dispatcher.start_chat_reply(
            bus=bus, notifications_state=notifications, composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "one"}], on_reply=replies.append,
        )
        await dispatcher.start_chat_reply(
            bus=bus, notifications_state=notifications, composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "two"}], on_reply=replies.append,
        )

        assert len(chat_slots(dispatcher)) == 1, "both calls must never be admitted concurrently"
        assert notifications.message == "A response is already being generated."

        release.set()
        await next(iter(chat_slots(dispatcher).values()))["task"]
        assert replies == ["first"]

    asyncio.run(run())


# -- R4.3: start_conversation_reply (ConversationNode real reply + per-node
# cancel) - mirrors the start_chat_reply tests above one-for-one, using a
# small stand-in node object in place of composer_document. -----------------


class _Recorder:
    """Minimal connection stand-in recording which topics got published, in
    order - used here to confirm start_conversation_reply republishes
    "scene" (never "app-composer") around its state change, unlike
    start_chat_reply's "app-composer"."""

    def __init__(self):
        self.topics: list[str] = []

    async def send_json(self, data):
        if data.get("kind") == "state":
            self.topics.append(data["topic"])


def _make_node():
    return SimpleNamespace(pending_request_id=None)


def test_conversation_reply_sets_then_clears_pending_request_id_and_calls_on_reply(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        started.set()
        release.wait(5)
        return {"message": {"content": "canned reply"}}

    _configure_fake_ollama(monkeypatch, blocking_chat)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()
        replies = []

        await dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
        )
        await asyncio.to_thread(started.wait, 5)
        assert node.pending_request_id is not None, "set mid-flight, before the blocking call returns"

        release.set()
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert replies == ["canned reply"]
        assert chat_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_conversation_reply_publishes_scene_not_app_composer_on_begin_and_end(monkeypatch):
    _configure_fake_ollama(monkeypatch, lambda task, messages, **kwargs: {"message": {"content": "canned reply"}})

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        recorder = _Recorder()
        bus.attach(recorder)
        node = _make_node()

        await dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=lambda text: None,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert "app-composer" not in recorder.topics
        # Once after on_begin, once after on_reply (hardcoded "scene" in
        # _dispatch regardless of state_topic), once after on_end.
        assert recorder.topics.count("scene") == 3
        assert composer_document.request_state == "idle", "a conversation reply must never touch composer state"

    asyncio.run(run())


def test_conversation_reply_provider_not_configured_returns_quickly_with_an_error_notification(monkeypatch):
    chat_calls = []
    _configure_fake_ollama(
        monkeypatch,
        lambda task, messages, **kwargs: chat_calls.append(1),
        model="",  # empty -> is_configured() is False
    )

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()

        await dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=[],
            on_reply=lambda text: None,
        )

        assert chat_slots(dispatcher) == {}, "no task/thread work started"
        assert chat_calls == [], "api_provider.chat was never reached"
        assert node.pending_request_id is None, "never touched on the fail-fast path"
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == (
            "No AI provider is configured yet. Open Settings to choose Ollama, "
            "Llama.cpp, or an API provider."
        )

    asyncio.run(run())


def test_conversation_reply_cancellation_mid_flight_fires_info_notification_and_clears_registry(monkeypatch):
    started = threading.Event()

    def blocking_then_cancelled(task, messages, cancellation_event=None, **kwargs):
        started.set()
        while not cancellation_event.is_set():
            time.sleep(0.01)
        raise api_provider.RequestCancelledError("Request cancelled.")

    _configure_fake_ollama(monkeypatch, blocking_then_cancelled)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()

        await dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=lambda text: None,
        )
        request_id, entry = next(iter(chat_slots(dispatcher).items()))

        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel(request_id) is True

        await entry["task"]

        assert chat_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "Request cancelled."

    asyncio.run(run())


def test_conversation_reply_timeout_fires_the_exact_message_and_clears_registry(monkeypatch):
    monkeypatch.setattr(agents_module, "WATCHDOG_TIMEOUT_SECONDS", 0.05)

    def slow_chat(task, messages, **kwargs):
        time.sleep(0.3)
        return {"message": {"content": "too late"}}

    _configure_fake_ollama(monkeypatch, slow_chat)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()

        await dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=lambda text: None,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert chat_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == (
            "The model stopped responding before the request completed. "
            "Please try again or choose a faster model."
        )

    asyncio.run(run())


def test_conversation_on_reply_raising_still_clears_pending_request_id_and_frees_the_registry(monkeypatch):
    """Simulates a node deleted mid-flight: on_reply (which in production
    calls document.append_conversation_assistant_message) raises. This must
    surface via the existing "AI response failed: ..." notification path
    (same as any other _dispatch exception, e.g. api_provider.chat itself
    raising), and the registry must still free up - node.pending_request_id
    cleared, and a subsequent call admitted normally."""
    _configure_fake_ollama(monkeypatch, lambda task, messages, **kwargs: {"message": {"content": "reply text"}})

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()

        def raising_on_reply(text):
            raise KeyError("node deleted mid-flight")

        await dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=raising_on_reply,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert chat_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message.startswith("AI response failed:")

        # The registry actually frees up: a subsequent call is admitted.
        monkeypatch.setattr(
            api_provider, "chat", lambda task, messages, **kwargs: {"message": {"content": "next reply"}}
        )
        replies = []
        await dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=[{"role": "user", "content": "hi again"}],
            on_reply=replies.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]
        assert replies == ["next reply"]
        assert node.pending_request_id is None

    asyncio.run(run())


# -- cross-channel guard: Composer and ConversationNode share ONE in-flight
# slot per dispatcher - this locks in that shared-single-slot design
# decision explicitly, not just implied by the same-channel tests above. ----


def test_composer_call_in_flight_blocks_a_concurrent_conversation_call_on_the_same_dispatcher(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        started.set()
        release.wait(5)
        return {"message": {"content": "composer reply"}}

    _configure_fake_ollama(monkeypatch, blocking_chat)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()
        composer_replies = []
        conversation_replies = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "composer msg"}],
            on_reply=composer_replies.append,
        )
        await asyncio.to_thread(started.wait, 5)

        await dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=[{"role": "user", "content": "conversation msg"}],
            on_reply=conversation_replies.append,
        )

        assert notifications.visible is True
        assert notifications.message == "A response is already being generated."
        assert node.pending_request_id is None, "the bounced call must never touch the node"
        assert len(chat_slots(dispatcher)) == 1, "only the composer's original request stays in flight"

        release.set()
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert composer_replies == ["composer reply"]
        assert conversation_replies == [], "the bounced call never ran at all"
        assert chat_slots(dispatcher) == {}

    asyncio.run(run())


def test_conversation_call_in_flight_blocks_a_concurrent_composer_call_on_the_same_dispatcher(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        started.set()
        release.wait(5)
        return {"message": {"content": "conversation reply"}}

    _configure_fake_ollama(monkeypatch, blocking_chat)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()
        composer_replies = []
        conversation_replies = []

        await dispatcher.start_conversation_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            conversation_history=[{"role": "user", "content": "conversation msg"}],
            on_reply=conversation_replies.append,
        )
        await asyncio.to_thread(started.wait, 5)

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "composer msg"}],
            on_reply=composer_replies.append,
        )

        assert notifications.visible is True
        assert notifications.message == "A response is already being generated."
        assert composer_document.request_state == "idle", "the bounced call must never touch composer state"
        assert len(chat_slots(dispatcher)) == 1, "only the conversation node's original request stays in flight"

        release.set()
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert conversation_replies == ["conversation reply"]
        assert composer_replies == [], "the bounced call never ran at all"
        assert chat_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


# -- R4.4a: start_image_reply - the independent image-generation slot --------
#
# Unlike start_chat_reply/start_conversation_reply above, these tests never
# touch Ollama/api_provider.chat plumbing at all - start_image_reply's only
# real dependency is api_provider.generate_image, monkeypatched directly.


def _make_image_env():
    bus = SessionBus("agents-image-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", lambda: {})
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    return bus, notifications, dispatcher


def test_start_image_reply_calls_on_reply_with_the_image_bytes(monkeypatch):
    monkeypatch.setattr(api_provider, "generate_image", lambda prompt, **kwargs: b"canned-image-bytes")

    async def run():
        bus, notifications, dispatcher = _make_image_env()
        replies = []
        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="a cat", on_reply=replies.append,
        )
        entry = next(iter(image_slots(dispatcher).values()))
        await entry["task"]

        assert replies == [b"canned-image-bytes"]
        assert image_slots(dispatcher) == {}
        assert notifications.visible is False

    asyncio.run(run())


def test_start_image_reply_second_call_while_in_flight_is_rejected_with_info_notification(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_generate_image(prompt, **kwargs):
        started.set()
        release.wait(5)
        return b"first-image-bytes"

    monkeypatch.setattr(api_provider, "generate_image", blocking_generate_image)

    async def run():
        bus, notifications, dispatcher = _make_image_env()
        replies = []

        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="first prompt", on_reply=replies.append,
        )
        await asyncio.to_thread(started.wait, 5)

        # Second call while the first is still in flight must be rejected
        # and must not disturb the first request.
        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="second prompt", on_reply=replies.append,
        )
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "An image is already being generated."
        assert len(image_slots(dispatcher)) == 1

        release.set()
        entry = next(iter(image_slots(dispatcher).values()))
        await entry["task"]
        assert replies == [b"first-image-bytes"]
        assert image_slots(dispatcher) == {}

    asyncio.run(run())


def test_image_request_and_chat_request_run_concurrently_both_dicts_non_empty(monkeypatch):
    """THE key concurrency-slot regression guard (R4.4a): a chat/composer
    request occupies self._runs's "chat" kind while an image-generation
    request occupies the SEPARATE "image" kind at the same time - neither
    blocks nor is blocked by the other, and both are simultaneously
    non-empty at least once, proving these are two genuinely independent
    slots rather than aliases of the same guard (the whole point of
    "image" existing as its own kind - see AgentDispatcher's own comment
    in backend/agents.py)."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    image_started = threading.Event()
    image_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_generate_image(prompt, **kwargs):
        image_started.set()
        image_release.wait(5)
        return b"image-bytes"

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(api_provider, "generate_image", blocking_generate_image)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        chat_replies = []
        image_replies = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=chat_replies.append,
        )
        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="a cat", on_reply=image_replies.append,
        )

        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(image_started.wait, 5)

        # THE key assertion: both slots are genuinely occupied at the same
        # time - neither request bounced the other, and neither notification
        # fired.
        assert len(chat_slots(dispatcher)) == 1
        assert len(image_slots(dispatcher)) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(chat_slots(dispatcher).values()))
        await chat_entry["task"]
        image_release.set()
        image_entry = next(iter(image_slots(dispatcher).values()))
        await image_entry["task"]

        assert chat_replies == ["chat reply"]
        assert image_replies == [b"image-bytes"]
        assert chat_slots(dispatcher) == {}
        assert image_slots(dispatcher) == {}
        assert composer_document.request_state == "idle"

    asyncio.run(run())


@pytest.mark.parametrize("error_message", [
    "Image generation is only available in API Endpoint mode.",
    "API client not initialized. Configure API settings first.",
    "Image generation is not available for Anthropic Claude in Graphlink yet.",
    "No image generation model configured.\nPlease select one in API Settings.",
    "Image generation quota exceeded.\n\nPlease use a lower-cost image model or "
    "verify billing is enabled for the selected provider.",
])
def test_start_image_reply_runtime_error_cases_forward_the_exact_message_verbatim(monkeypatch, error_message):
    """Each of api_provider.generate_image's real RuntimeError gating
    messages (not API mode / no client / Anthropic unsupported / no model
    configured / quota exceeded) must be forwarded to the user VERBATIM
    after one shared "Image generation failed: " prefix - the WS/dispatch
    layer never duplicates api_provider.py's own gating knowledge."""
    def raising_generate_image(prompt, **kwargs):
        raise RuntimeError(error_message)

    monkeypatch.setattr(api_provider, "generate_image", raising_generate_image)

    async def run():
        bus, notifications, dispatcher = _make_image_env()
        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="a cat", on_reply=lambda image_bytes: None,
        )
        entry = next(iter(image_slots(dispatcher).values()))
        await entry["task"]

        assert image_slots(dispatcher) == {}
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == f"Image generation failed: {error_message}"

    asyncio.run(run())


def test_start_image_reply_timeout_fires_the_exact_message_and_clears_the_slot(monkeypatch):
    monkeypatch.setattr(agents_module, "WATCHDOG_TIMEOUT_SECONDS", 0.05)

    def slow_generate_image(prompt, **kwargs):
        time.sleep(0.3)
        return b"too-late-bytes"

    monkeypatch.setattr(api_provider, "generate_image", slow_generate_image)

    async def run():
        bus, notifications, dispatcher = _make_image_env()
        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="a cat", on_reply=lambda image_bytes: None,
        )
        entry = next(iter(image_slots(dispatcher).values()))
        await entry["task"]

        assert image_slots(dispatcher) == {}, "the slot must not leak/deadlock future requests"
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == (
            "Image generation stopped responding before the request completed. Please try again."
        )

    asyncio.run(run())


def test_start_image_reply_slot_does_not_leak_a_subsequent_request_is_admitted_after_failure(monkeypatch):
    def raising_generate_image(prompt, **kwargs):
        raise RuntimeError("No image generation model configured.")

    monkeypatch.setattr(api_provider, "generate_image", raising_generate_image)

    async def run():
        bus, notifications, dispatcher = _make_image_env()
        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="a cat", on_reply=lambda image_bytes: None,
        )
        entry = next(iter(image_slots(dispatcher).values()))
        await entry["task"]
        assert image_slots(dispatcher) == {}

        monkeypatch.setattr(api_provider, "generate_image", lambda prompt, **kwargs: b"next-bytes")
        replies = []
        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="a dog", on_reply=replies.append,
        )
        entry = next(iter(image_slots(dispatcher).values()))
        await entry["task"]
        assert replies == [b"next-bytes"]

    asyncio.run(run())


def test_no_cancel_image_method_or_intent_exists():
    """Deliberate absence-of-API test (R4.4a design spec §3): image
    generation has zero cancellation, matching legacy's real, complete
    absence of a working cancel affordance for image generation
    (ImageGenerationWorkerThread.stop() exists but is never called from any
    UI path). A cancel_image method/intent must never be added for as long
    as this design decision holds."""
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    assert not hasattr(dispatcher, "cancel_image")


# -- R4.4: true token streaming (dispatcher half) -----------------------------
#
# These tests monkeypatch graphlink_app.api_provider.chat_stream directly
# (the same seam graphlink_chat_agent.py's ChatWorker.run calls when
# on_chunk is not None), so they exercise the REAL _call_chat_agent_stream ->
# ChatAgent.get_response -> ChatWorker.run -> api_provider.chat_stream chain,
# not just a fake standing in for backend/agents.py's own driver function -
# the dispatcher-side pump/thread-to-loop-handoff logic in _dispatch's _run()
# is what is actually under test here.


class _StreamRecorderConnection:
    """Connection double recording every 'stream'-kind frame broadcast to
    it, in order - lets tests assert the pump's batching/flush behavior end
    to end without a real WebSocket. (Distinct from _Recorder above, which
    only tracks 'state'-kind topic names.)"""

    def __init__(self):
        self.frames: list[dict] = []

    async def send_json(self, data):
        if data.get("kind") == "stream":
            self.frames.append(data)


def test_streaming_happy_path_recorder_receives_ordered_stream_frames_and_on_reply_once(monkeypatch):
    def fake_chat_stream(task, messages, on_chunk, **kwargs):
        on_chunk("Hel", False)
        on_chunk("lo", False)
        return {"message": {"content": "Hello"}}

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        recorder = _StreamRecorderConnection()
        bus.attach(recorder)
        replies = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert replies == ["Hello"]
        assert chat_slots(dispatcher) == {}
        assert composer_document.request_state == "idle"

        assert recorder.frames, "must have received at least one stream frame"
        assert recorder.frames[-1]["done"] is True
        assert recorder.frames[-1]["delta"] == ""
        concatenated = "".join(f["delta"] for f in recorder.frames if not f["done"])
        assert concatenated == "Hello"
        assert all(f["topic"] == "app-composer" for f in recorder.frames)
        request_ids = {f["requestId"] for f in recorder.frames}
        assert len(request_ids) == 1 and next(iter(request_ids))
        seqs = [f["seq"] for f in recorder.frames]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "seq must be strictly increasing"

    asyncio.run(run())


def test_cancel_mid_stream_no_on_reply_and_stream_frames_still_end_with_done_true(monkeypatch):
    started = threading.Event()

    def fake_chat_stream(task, messages, on_chunk, cancellation_event=None, **kwargs):
        on_chunk("a", False)
        on_chunk("b", False)
        started.set()
        while not cancellation_event.is_set():
            time.sleep(0.01)
        raise api_provider.RequestCancelledError("Request cancelled.")

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        recorder = _StreamRecorderConnection()
        bus.attach(recorder)
        replies = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
        )
        request_id, entry = next(iter(chat_slots(dispatcher).items()))

        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel(request_id) is True

        await entry["task"]

        assert replies == [], "cancel discards everything - no partial-text on_reply, matching R4.2 precedent"
        assert chat_slots(dispatcher) == {}
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "Request cancelled."

        assert recorder.frames, "the pump must still have flushed something before terminating"
        assert recorder.frames[-1]["done"] is True, "the pump never hangs - it always sends its final frame"

    asyncio.run(run())


def test_stream_error_mid_way_generic_notification_and_stream_frames_still_end_done_true(monkeypatch):
    def fake_chat_stream(task, messages, on_chunk, **kwargs):
        on_chunk("partial", False)
        raise RuntimeError("boom")

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        recorder = _StreamRecorderConnection()
        bus.attach(recorder)
        replies = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert replies == []
        assert chat_slots(dispatcher) == {}
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == "AI response failed: boom"

        assert recorder.frames, "the pump must still have flushed the partial chunk before terminating"
        assert recorder.frames[-1]["done"] is True

    asyncio.run(run())


def test_throttle_batches_many_small_chunks_into_materially_fewer_publish_stream_calls(monkeypatch):
    chars = [chr(ord("a") + (i % 26)) for i in range(200)]
    expected = "".join(chars)

    def fake_chat_stream(task, messages, on_chunk, **kwargs):
        for c in chars:
            on_chunk(c, False)
        return {"message": {"content": expected}}

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        recorder = _StreamRecorderConnection()
        bus.attach(recorder)
        replies = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert replies == [expected]
        assert len(recorder.frames) < 100, (
            "200 one-character on_chunk calls fired with no delay must batch into "
            "materially fewer publish_stream calls than input chunks"
        )
        concatenated = "".join(f["delta"] for f in recorder.frames if not f["done"])
        assert concatenated == expected
        assert recorder.frames[-1]["done"] is True

    asyncio.run(run())


def test_completion_handoff_parity_streaming_send_message_creates_identical_nodes(monkeypatch):
    """R4.4 spec section 6, item 7: re-run the R4.3b thinking+text+code
    send_message fixture (test_canvas.py's own
    test_send_message_reply_with_thinking_text_and_code_creates_both_children_on_same_parent)
    through the real streaming dispatch path (api_provider.chat_stream
    monkeypatched instead of api_provider.chat) via the real "sendMessage"
    scene intent, and assert IDENTICAL ChatNode/ThinkingNode/CodeNode
    creation results to the non-streaming fixture for the same canned full
    text - proves the completion hand-off (on_reply -> parse_response ->
    add_chat_node/add_thinking_node/add_code_node, all in backend/canvas.py,
    untouched by this increment) is truly unchanged by streaming."""
    canned_text = (
        "<think>working it out</think>\n"
        "Here's the plan.\n"
        "```python\nprint('plan')\n```"
    )

    def fake_chat_stream(task, messages, on_chunk, **kwargs):
        on_chunk(canned_text, False)
        return {"message": {"content": canned_text}}

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)

    async def run():
        bus = SessionBus("agents-stream-parity-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        dispatcher = AgentDispatcher(_FakeSettingsManager())
        document = register_canvas(bus, notifications, dispatcher, composer_document)

        user_node_id = await bus.dispatch_intent("scene", "sendMessage", ["plan it out"])
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assistant_nodes = [
            n for n in document.nodes.values() if n.kind == "chat" and n.id != user_node_id
        ]
        assert len(assistant_nodes) == 1
        assistant_node = assistant_nodes[0]
        assert assistant_node.content == "Here's the plan."

        thinking_nodes = [n for n in document.nodes.values() if n.kind == "thinking"]
        code_nodes = [n for n in document.nodes.values() if n.kind == "code"]
        assert len(thinking_nodes) == 1
        assert len(code_nodes) == 1

        assert any(
            e.source == assistant_node.id and e.target == thinking_nodes[0].id
            for e in document.edges.values()
        )
        assert any(
            e.source == assistant_node.id and e.target == code_nodes[0].id
            for e in document.edges.values()
        )
        assert not any(
            e.source == thinking_nodes[0].id and e.target == code_nodes[0].id
            for e in document.edges.values()
        ), "thinking and code children are not chained to each other"
        assert document.last_chat_node_id == assistant_node.id

    asyncio.run(run())


def test_image_request_runs_independently_while_a_chat_stream_is_paused_mid_flight(monkeypatch):
    """R4.4 spec section 6, item 8: cross-slot concurrency during an active
    stream. A chat stream paused mid-flight (self._runs's "chat" kind) must
    not block, or be blocked by, a concurrent image-generation request
    (self._runs's "image" kind) - the two independent slots this
    dispatcher already guarantees (R4.4a) must keep holding under
    streaming too."""
    chat_started = threading.Event()
    chat_release = threading.Event()

    def fake_chat_stream(task, messages, on_chunk, **kwargs):
        on_chunk("partial chat text", False)
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "final chat reply"}}

    _configure_fake_chat_stream(monkeypatch, fake_chat_stream)
    monkeypatch.setattr(api_provider, "generate_image", lambda prompt, **kwargs: b"image-bytes")

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        recorder = _StreamRecorderConnection()
        bus.attach(recorder)
        chat_replies = []
        image_replies = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=chat_replies.append,
        )
        await asyncio.to_thread(chat_started.wait, 5)
        # chat_started only guarantees on_chunk queued its delta - give the
        # pump's own flush timer (FLUSH_INTERVAL_S=0.06s) a moment to
        # actually broadcast it before asserting.
        await asyncio.sleep(0.15)

        assert len(chat_slots(dispatcher)) == 1
        assert recorder.frames, "at least the first buffered delta should have flushed by now"

        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="a cat", on_reply=image_replies.append,
        )
        image_entry = next(iter(image_slots(dispatcher).values()))
        await image_entry["task"]

        # The image request completed independently, without waiting on the
        # still-paused chat stream.
        assert image_replies == [b"image-bytes"]
        assert image_slots(dispatcher) == {}
        assert len(chat_slots(dispatcher)) == 1, "the chat stream is still in flight, untouched by the image request"
        assert notifications.visible is False, "neither request was rejected"

        chat_release.set()
        chat_entry = next(iter(chat_slots(dispatcher).values()))
        await chat_entry["task"]

        assert chat_replies == ["final chat reply"]
        assert chat_slots(dispatcher) == {}
        assert recorder.frames[-1]["done"] is True, "stream frames kept recording throughout the image request"

    asyncio.run(run())


# -- R5.1: start_web_research - the independent web-research slot ------------
#
# Unlike start_chat_reply/start_conversation_reply above, these tests never
# touch Ollama/api_provider.chat plumbing (except the two dedicated
# concurrency tests) - start_web_research's only real dependency is
# WebResearchService.run, monkeypatched directly on the class (agents.py
# constructs a fresh WebResearchService() instance per call, so patching the
# class method is the seam, mirroring how api_provider.chat/chat_stream are
# patched as module-level seams for the chat path).


def _make_web_research_env():
    bus = SessionBus("agents-web-research-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", lambda: {})
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    return bus, notifications, dispatcher


def test_start_web_research_calls_on_success_with_the_result_then_clears_the_slot(monkeypatch):
    fake_result = SimpleNamespace(answer_markdown="the answer")

    def fake_run(self, request, *, token=None, progress=None):
        return fake_result

    monkeypatch.setattr(agents_module.WebResearchService, "run", fake_run)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node = _make_node()
        successes = []

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id="n1",
            query="what is this about?",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=successes.append,
            on_failure=lambda exc: None,
        )
        entry = next(iter(web_research_slots(dispatcher).values()))
        await entry["task"]

        assert successes == [fake_result]
        assert web_research_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is False

    asyncio.run(run())


def test_start_web_research_second_call_while_in_flight_is_rejected_first_still_completes(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_run(self, request, *, token=None, progress=None):
        started.set()
        release.wait(5)
        return SimpleNamespace(answer_markdown="first result")

    monkeypatch.setattr(agents_module.WebResearchService, "run", blocking_run)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node1 = _make_node()
        node2 = _make_node()
        successes = []

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node1,
            node_id="n1",
            query="first query",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=successes.append,
            on_failure=lambda exc: None,
        )
        await asyncio.to_thread(started.wait, 5)

        # Second call while the first is still in flight must be rejected and
        # must not disturb the first request.
        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node2,
            node_id="n2",
            query="second query",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=successes.append,
            on_failure=lambda exc: None,
        )
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "A web research request is already running."
        assert len(web_research_slots(dispatcher)) == 1
        assert node2.pending_request_id is None, "the bounced call must never touch node2"

        release.set()
        entry = next(iter(web_research_slots(dispatcher).values()))
        await entry["task"]

        assert successes == [SimpleNamespace(answer_markdown="first result")]
        assert web_research_slots(dispatcher) == {}

    asyncio.run(run())


def test_start_web_research_research_failure_forwards_via_on_failure_and_shows_error_notification(monkeypatch):
    failure = ResearchFailure("The search provider failed.", code="search_failed")

    def raising_run(self, request, *, token=None, progress=None):
        raise failure

    monkeypatch.setattr(agents_module.WebResearchService, "run", raising_run)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node = _make_node()
        failures = []

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id="n1",
            query="q",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=lambda result: None,
            on_failure=failures.append,
        )
        entry = next(iter(web_research_slots(dispatcher).values()))
        await entry["task"]

        assert failures == [failure]
        assert web_research_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == "Web research failed: The search provider failed."

    asyncio.run(run())


def test_start_web_research_request_cancelled_forwards_via_on_failure_and_shows_info_notification(monkeypatch):
    cancelled_exc = RequestCancelled("Web research was cancelled.")

    def raising_run(self, request, *, token=None, progress=None):
        raise cancelled_exc

    monkeypatch.setattr(agents_module.WebResearchService, "run", raising_run)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node = _make_node()
        failures = []

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id="n1",
            query="q",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=lambda result: None,
            on_failure=failures.append,
        )
        entry = next(iter(web_research_slots(dispatcher).values()))
        await entry["task"]

        assert failures == [cancelled_exc]
        assert web_research_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "Web research cancelled."

    asyncio.run(run())


def test_start_web_research_generic_exception_forwards_via_on_failure_and_shows_error_notification(monkeypatch):
    def raising_run(self, request, *, token=None, progress=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(agents_module.WebResearchService, "run", raising_run)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node = _make_node()
        failures = []

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id="n1",
            query="q",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=lambda result: None,
            on_failure=failures.append,
        )
        entry = next(iter(web_research_slots(dispatcher).values()))
        await entry["task"]

        assert len(failures) == 1
        assert isinstance(failures[0], RuntimeError)
        assert str(failures[0]) == "boom"
        assert web_research_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == "Web research failed: boom"

    asyncio.run(run())


def test_start_web_research_timeout_fires_the_exact_message_and_clears_the_slot(monkeypatch):
    monkeypatch.setattr(agents_module, "WEB_RESEARCH_WATCHDOG_TIMEOUT_SECONDS", 0.05)

    def slow_run(self, request, *, token=None, progress=None):
        time.sleep(0.3)
        return SimpleNamespace(answer_markdown="too late")

    monkeypatch.setattr(agents_module.WebResearchService, "run", slow_run)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node = _make_node()
        failures = []

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id="n1",
            query="q",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=lambda result: None,
            on_failure=failures.append,
        )
        entry = next(iter(web_research_slots(dispatcher).values()))
        await entry["task"]

        assert len(failures) == 1
        assert isinstance(failures[0], ResearchFailure)
        expected_message = (
            "Web research stopped responding before the request completed. Please try again."
        )
        assert str(failures[0]) == expected_message
        assert failures[0].code == "watchdog_timeout"
        assert web_research_slots(dispatcher) == {}, "the slot must not leak/deadlock future requests"
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == expected_message

    asyncio.run(run())


def test_start_web_research_stale_progress_event_after_timeout_is_dropped(monkeypatch):
    """Review-found regression guard: asyncio.to_thread's underlying thread is
    not actually killed when wait_for's timeout fires (Future.cancel() on an
    already-running thread is a no-op), so a slow WebResearchService.run()
    can keep calling progress() well after this request's own finally block
    has already released its registry claim and cleared
    node.pending_request_id. That stale event must be dropped, not delivered
    to on_progress - otherwise it can resurrect a since-failed node's stage
    or clobber a new run started on the same node afterward."""
    monkeypatch.setattr(agents_module, "WEB_RESEARCH_WATCHDOG_TIMEOUT_SECONDS", 0.05)
    late_event = SimpleNamespace(
        stage=SimpleNamespace(value="fetching"), completed=1, total=4, source_id="s1"
    )

    def slow_run(self, request, *, token=None, progress=None):
        time.sleep(0.3)  # past the watchdog timeout, so the timeout branch already ran
        progress(late_event)  # the "zombie" thread still calling back afterward
        return SimpleNamespace(answer_markdown="too late")

    monkeypatch.setattr(agents_module.WebResearchService, "run", slow_run)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node = _make_node()
        progress_events = []

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id="n1",
            query="q",
            branch_history=[],
            on_progress=progress_events.append,
            on_success=lambda result: None,
            on_failure=lambda exc: None,
        )
        entry = next(iter(web_research_slots(dispatcher).values()))
        await entry["task"]
        assert web_research_slots(dispatcher) == {}, "slot must already be cleared by the timeout branch"

        # Give the still-running background thread time to reach its
        # progress() call and for run_coroutine_threadsafe's scheduled
        # coroutine to actually execute on this loop.
        await asyncio.sleep(0.4)

        assert progress_events == [], (
            "a progress event emitted after this request's slot was cleared "
            "must be dropped, not delivered to on_progress"
        )

    asyncio.run(run())


def test_cancel_web_research_returns_false_for_an_unknown_request_id():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    assert dispatcher.cancel_web_research("no-such-request") is False


def test_cancel_web_research_actually_trips_the_real_cancellation_token(monkeypatch):
    """ADR-002 stage 2.4e: the first real proof, through the actual public
    dispatcher method, that RunHandle.on_cancel genuinely reaches the real
    CancellationToken passed into WebResearchService.run - not just that
    cancel_web_research() returns True (which a stub on_cancel would also
    satisfy)."""
    started = threading.Event()
    release = threading.Event()
    seen_token = {}

    def blocking_run(self, request, *, token=None, progress=None):
        seen_token["token"] = token
        started.set()
        release.wait(5)
        return SimpleNamespace(answer_markdown="result")

    monkeypatch.setattr(agents_module.WebResearchService, "run", blocking_run)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node = _make_node()

        task = asyncio.create_task(
            dispatcher.start_web_research(
                bus=bus, notifications_state=notifications, node=node, node_id="n1",
                query="q", branch_history=[], on_progress=lambda event: None,
                on_success=lambda result: None, on_failure=lambda exc: None,
            )
        )
        await asyncio.to_thread(started.wait, 5)
        request_id = next(iter(web_research_slots(dispatcher).keys()))

        assert dispatcher.cancel_web_research(request_id) is True
        assert seen_token["token"].is_set(), "cancel_web_research must trip the SAME token WebResearchService.run received"

        release.set()
        await task

    asyncio.run(run())


def test_cancel_web_research_and_cancel_chat_cannot_trip_each_others_request_id(monkeypatch):
    """Web research's counterpart to
    test_cancel_artifact_and_cancel_chat_cannot_trip_each_others_request_id
    (stage 2.4d) - chat and web_research are now both cancellable kinds
    (via cancel_event and on_cancel respectively) sharing self._runs."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    research_started = threading.Event()
    research_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_run(self, request, *, token=None, progress=None):
        research_started.set()
        research_release.wait(5)
        return SimpleNamespace(answer_markdown="result")

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(agents_module.WebResearchService, "run", blocking_run)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()

        await dispatcher.start_chat_reply(
            bus=bus, notifications_state=notifications, composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}], on_reply=lambda text: None,
        )
        research_task = asyncio.create_task(
            dispatcher.start_web_research(
                bus=bus, notifications_state=notifications, node=node, node_id="n1",
                query="q", branch_history=[], on_progress=lambda event: None,
                on_success=lambda result: None, on_failure=lambda exc: None,
            )
        )
        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(research_started.wait, 5)

        chat_request_id, chat_entry = next(iter(chat_slots(dispatcher).items()))
        research_request_id = next(iter(web_research_slots(dispatcher).keys()))
        chat_cancel_event = chat_entry["cancel_event"]

        assert dispatcher.cancel_web_research(chat_request_id) is False, (
            "a chat request_id must never be accepted by cancel_web_research"
        )
        assert not chat_cancel_event.is_set(), "the mismatched call must not have tripped chat's own event"

        assert dispatcher.cancel(research_request_id) is False, (
            "a web_research request_id must never be accepted by the generic cancel() (cancelChatRequest)"
        )

        chat_release.set()
        research_release.set()
        await chat_entry["task"]
        await research_task

    asyncio.run(run())


def test_web_research_request_and_chat_request_run_concurrently_both_dicts_non_empty(monkeypatch):
    """THE key concurrency-slot regression guard (R5.1, mirrors R4.4a's own
    chat/image guard test): a chat/composer request occupies self._runs's
    "chat" kind while a web-research request occupies the SEPARATE
    "web_research" kind at the same time - neither blocks nor is blocked by
    the other, and both are simultaneously non-empty at least once."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    research_started = threading.Event()
    research_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_run(self, request, *, token=None, progress=None):
        research_started.set()
        research_release.wait(5)
        return SimpleNamespace(answer_markdown="research result")

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(agents_module.WebResearchService, "run", blocking_run)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()
        chat_replies = []
        research_successes = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=chat_replies.append,
        )
        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id="n1",
            query="q",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=research_successes.append,
            on_failure=lambda exc: None,
        )

        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(research_started.wait, 5)

        # THE key assertion: both slots are genuinely occupied at the same
        # time - neither request bounced the other, and neither notification
        # fired.
        assert len(chat_slots(dispatcher)) == 1
        assert len(web_research_slots(dispatcher)) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(chat_slots(dispatcher).values()))
        await chat_entry["task"]
        research_release.set()
        research_entry = next(iter(web_research_slots(dispatcher).values()))
        await research_entry["task"]

        assert chat_replies == ["chat reply"]
        assert research_successes == [SimpleNamespace(answer_markdown="research result")]
        assert chat_slots(dispatcher) == {}
        assert web_research_slots(dispatcher) == {}
        assert composer_document.request_state == "idle"

    asyncio.run(run())


def test_start_web_research_progress_ordering_on_progress_invoked_in_the_same_order(monkeypatch):
    """Validates the run_coroutine_threadsafe handoff preserves emission
    order (see start_web_research's own docstring for why this holds even
    though on_progress is invoked from a worker thread)."""
    events = [
        SimpleNamespace(stage=SimpleNamespace(value=f"stage{i}"), completed=i, total=5, source_id=None)
        for i in range(5)
    ]

    def fake_run(self, request, *, token=None, progress=None):
        for event in events:
            progress(event)
        return SimpleNamespace(answer_markdown="done")

    monkeypatch.setattr(agents_module.WebResearchService, "run", fake_run)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node = _make_node()
        progress_calls = []

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id="n1",
            query="q",
            branch_history=[],
            on_progress=progress_calls.append,
            on_success=lambda result: None,
            on_failure=lambda exc: None,
        )
        entry = next(iter(web_research_slots(dispatcher).values()))
        await entry["task"]

        assert progress_calls == events

    asyncio.run(run())


def test_start_web_research_constructs_web_research_service_with_no_override_the_ssrf_safety_default(monkeypatch):
    """Confirms AgentDispatcher.start_web_research always constructs a
    default WebResearchService() - no fetcher/policy override - so the
    SSRF-safe RequestsDocumentFetcher()/FetchPolicy() defaults are always in
    effect for every dispatch. The dedicated SSRF/redirect/byte-cap tests
    themselves live in graphlink_app/tests/test_web_research_service.py and
    test_web_research_lifecycle.py (unchanged, untouched by this increment) -
    this is only the construction-site confirmation."""
    captured_args = []
    captured_kwargs = []
    original_init = agents_module.WebResearchService.__init__

    def spy_init(self, *args, **kwargs):
        captured_args.append(args)
        captured_kwargs.append(kwargs)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(agents_module.WebResearchService, "__init__", spy_init)
    monkeypatch.setattr(
        agents_module.WebResearchService,
        "run",
        lambda self, request, *, token=None, progress=None: SimpleNamespace(answer_markdown="x"),
    )

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        node = _make_node()

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=node,
            node_id="n1",
            query="q",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=lambda result: None,
            on_failure=lambda exc: None,
        )
        entry = next(iter(web_research_slots(dispatcher).values()))
        await entry["task"]

    asyncio.run(run())

    assert captured_args == [()]
    assert captured_kwargs == [{}]


# -- R5.2: start_artifact_reply/_call_artifact_agent - the independent
# artifact/drafter slot -------------------------------------------------------
#
# Mirrors the R5.1 web-research section's own structure: these tests never
# touch Ollama/api_provider.chat plumbing (except the two dedicated
# concurrency tests) - start_artifact_reply's only real dependency is
# ArtifactAgent.get_response, monkeypatched directly on the class (agents.py
# constructs a fresh ArtifactAgent() instance per call, so patching the class
# method is the seam, mirroring how WebResearchService.run is patched as a
# class-level seam for the research path).


def _make_artifact_env():
    bus = SessionBus("agents-artifact-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", lambda: {})
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    return bus, notifications, dispatcher


def test_call_artifact_agent_calls_get_response_and_returns_the_tuple(monkeypatch):
    captured = []

    def fake_get_response(self, current_artifact, history):
        captured.append((current_artifact, history))
        return "the new document", "an ai message"

    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", fake_get_response)

    result = agents_module._call_artifact_agent(
        "the old document", [{"role": "user", "content": "add a section"}]
    )

    assert result == ("the new document", "an ai message")
    assert captured == [("the old document", [{"role": "user", "content": "add a section"}])]


def test_call_artifact_agent_propagates_the_missing_tag_runtime_error(monkeypatch):
    # ArtifactAgent.get_response's own fail-closed contract (see
    # graphlink_artifact_agent.py): a reply missing <artifact>...</artifact>
    # tags raises RuntimeError rather than silently corrupting the document.
    # _call_artifact_agent must let that propagate straight out, unmodified,
    # for start_artifact_reply's own except Exception to catch.
    def raising_get_response(self, current_artifact, history):
        raise RuntimeError(
            "The model's response did not include the required <artifact>...</artifact> tags, "
            "so the document was left unchanged to avoid overwriting it with an unstructured reply."
        )

    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", raising_get_response)

    with pytest.raises(RuntimeError, match="did not include the required"):
        agents_module._call_artifact_agent("the old document", [])


def test_start_artifact_reply_calls_on_reply_with_the_tuple_then_clears_the_slot(monkeypatch):
    def fake_get_response(self, current_artifact, history):
        return "the new document", "an ai message"

    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", fake_get_response)

    async def run():
        bus, notifications, dispatcher = _make_artifact_env()
        node = _make_node()
        replies = []

        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            current_artifact="the old document",
            history=[{"role": "user", "content": "add a section"}],
            on_reply=lambda new_content, ai_message: replies.append((new_content, ai_message)),
        )
        entry = next(iter(artifact_slots(dispatcher).values()))
        await entry["task"]

        assert replies == [("the new document", "an ai message")]
        assert artifact_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is False

    asyncio.run(run())


def test_start_artifact_reply_second_call_while_in_flight_is_rejected(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_get_response(self, current_artifact, history):
        started.set()
        release.wait(5)
        return "first document", "first message"

    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, dispatcher = _make_artifact_env()
        node1 = _make_node()
        node2 = _make_node()
        replies = []

        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node1,
            current_artifact="doc1",
            history=[],
            on_reply=lambda new_content, ai_message: replies.append((new_content, ai_message)),
        )
        await asyncio.to_thread(started.wait, 5)

        # Second call while the first is still in flight must be rejected and
        # must not disturb the first request.
        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node2,
            current_artifact="doc2",
            history=[],
            on_reply=lambda new_content, ai_message: replies.append((new_content, ai_message)),
        )
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "An artifact request is already running."
        assert len(artifact_slots(dispatcher)) == 1
        assert node2.pending_request_id is None, "the bounced call must never touch node2"

        release.set()
        entry = next(iter(artifact_slots(dispatcher).values()))
        await entry["task"]

        assert replies == [("first document", "first message")]
        assert artifact_slots(dispatcher) == {}

    asyncio.run(run())


def test_start_artifact_reply_missing_tag_failure_shows_error_notification_and_never_calls_on_reply(monkeypatch):
    """SECURITY-CRITICAL: on the fail-closed tag-parsing RuntimeError,
    on_reply must NEVER be invoked - the document must be left completely
    untouched rather than being replaced with anything derived from the
    malformed reply."""
    def raising_get_response(self, current_artifact, history):
        raise RuntimeError(
            "The model's response did not include the required <artifact>...</artifact> tags, "
            "so the document was left unchanged to avoid overwriting it with an unstructured reply."
        )

    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", raising_get_response)

    async def run():
        bus, notifications, dispatcher = _make_artifact_env()
        node = _make_node()
        on_reply_calls = []

        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            current_artifact="the old document",
            history=[],
            on_reply=lambda new_content, ai_message: on_reply_calls.append((new_content, ai_message)),
        )
        entry = next(iter(artifact_slots(dispatcher).values()))
        await entry["task"]

        assert on_reply_calls == [], "on_reply must never be called on a tag-parsing failure"
        assert artifact_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == (
            "Artifact generation failed: The model's response did not include the required "
            "<artifact>...</artifact> tags, so the document was left unchanged to avoid "
            "overwriting it with an unstructured reply."
        )

    asyncio.run(run())


def test_start_artifact_reply_timeout_fires_the_exact_message_and_clears_the_slot(monkeypatch):
    monkeypatch.setattr(agents_module, "WATCHDOG_TIMEOUT_SECONDS", 0.05)

    def slow_get_response(self, current_artifact, history):
        time.sleep(0.3)
        return "too late", "too late message"

    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", slow_get_response)

    async def run():
        bus, notifications, dispatcher = _make_artifact_env()
        node = _make_node()
        replies = []

        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            current_artifact="doc",
            history=[],
            on_reply=lambda new_content, ai_message: replies.append((new_content, ai_message)),
        )
        entry = next(iter(artifact_slots(dispatcher).values()))
        await entry["task"]

        assert replies == []
        assert artifact_slots(dispatcher) == {}, "the slot must not leak/deadlock future requests"
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        expected_message = (
            "Artifact generation stopped responding before the request completed. Please try again."
        )
        assert notifications.message == expected_message

    asyncio.run(run())


def test_cancel_artifact_drops_the_result_and_never_calls_on_reply_even_on_a_late_return(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_get_response(self, current_artifact, history):
        started.set()
        release.wait(5)
        return "a document nobody should see", "a message nobody should see"

    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, dispatcher = _make_artifact_env()
        node = _make_node()
        replies = []

        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            current_artifact="doc",
            history=[],
            on_reply=lambda new_content, ai_message: replies.append((new_content, ai_message)),
        )
        await asyncio.to_thread(started.wait, 5)

        request_id = next(iter(artifact_slots(dispatcher).keys()))
        # ADR-006 stage 6.2 fire-and-forget: cancel now pops the handle
        # immediately (release-on-cancel), so grab the still-running worker
        # task BEFORE cancelling - it is unreachable via the slots after.
        entry = next(iter(artifact_slots(dispatcher).values()))
        assert dispatcher.cancel_artifact(request_id) is True
        # Release-on-cancel: the slot is freed the moment cancel returns,
        # not when the worker thread eventually observes the event.
        assert artifact_slots(dispatcher) == {}

        release.set()
        await entry["task"]

        assert replies == [], "on_reply must never be called once the request is cancelled"
        assert artifact_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "Artifact generation cancelled."

    asyncio.run(run())


def test_cancel_artifact_returns_false_for_an_unknown_request_id():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    assert dispatcher.cancel_artifact("no-such-request") is False


def test_cancel_artifact_and_cancel_chat_cannot_trip_each_others_request_id(monkeypatch):
    """ADR-002 stage 2.4d: the FIRST real exercise, through the actual
    public dispatcher methods (not the raw registry - see
    backend/tests/test_run_lifecycle.py's own unit-level proof), of
    RunRegistry.cancel()'s kind= filter added in stage 2.4b. Chat and
    artifact are now both cancel_event-bearing kinds sharing self._runs -
    a chat request_id handed to cancel_artifact(), or an artifact
    request_id handed to cancel() (the generic one backing
    cancelChatRequest), must be rejected rather than tripping the WRONG
    run's cancellation."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    artifact_started = threading.Event()
    artifact_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_get_response(self, current_artifact, history):
        artifact_started.set()
        artifact_release.wait(5)
        return "updated document", "an ai message"

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()

        await dispatcher.start_chat_reply(
            bus=bus, notifications_state=notifications, composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}], on_reply=lambda text: None,
        )
        await dispatcher.start_artifact_reply(
            bus=bus, notifications_state=notifications, node=node,
            current_artifact="old", history=[], on_reply=lambda content, msg: None,
        )
        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(artifact_started.wait, 5)

        chat_request_id, chat_entry = next(iter(chat_slots(dispatcher).items()))
        artifact_request_id, artifact_entry = next(iter(artifact_slots(dispatcher).items()))
        chat_cancel_event = chat_entry["cancel_event"]
        artifact_cancel_event = artifact_entry["cancel_event"]

        assert dispatcher.cancel_artifact(chat_request_id) is False, (
            "a chat request_id must never be accepted by cancel_artifact"
        )
        assert not chat_cancel_event.is_set(), "the mismatched call must not have tripped chat's own event"

        assert dispatcher.cancel(artifact_request_id) is False, (
            "an artifact request_id must never be accepted by the generic cancel() (cancelChatRequest)"
        )
        assert not artifact_cancel_event.is_set(), "the mismatched call must not have tripped artifact's own event"

        chat_release.set()
        artifact_release.set()
        await chat_entry["task"]
        await artifact_entry["task"]

    asyncio.run(run())


def test_artifact_request_and_chat_request_run_concurrently_both_dicts_non_empty(monkeypatch):
    """THE key concurrency-slot regression guard (R5.2, mirrors R4.4a/R5.1's
    own chat/image and chat/web-research guards): a chat/composer request
    occupies self._runs's "chat" kind while an artifact-generation request
    occupies the SEPARATE "artifact" kind at the same time - neither blocks
    nor is blocked by the other, and both are simultaneously non-empty at
    least once."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    artifact_started = threading.Event()
    artifact_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_get_response(self, current_artifact, history):
        artifact_started.set()
        artifact_release.wait(5)
        return "artifact document", "artifact message"

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        node = _make_node()
        chat_replies = []
        artifact_replies = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=chat_replies.append,
        )
        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            current_artifact="doc",
            history=[],
            on_reply=lambda new_content, ai_message: artifact_replies.append((new_content, ai_message)),
        )

        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(artifact_started.wait, 5)

        # THE key assertion: both slots are genuinely occupied at the same
        # time - neither request bounced the other, and neither notification
        # fired.
        assert len(chat_slots(dispatcher)) == 1
        assert len(artifact_slots(dispatcher)) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(chat_slots(dispatcher).values()))
        await chat_entry["task"]
        artifact_release.set()
        artifact_entry = next(iter(artifact_slots(dispatcher).values()))
        await artifact_entry["task"]

        assert chat_replies == ["chat reply"]
        assert artifact_replies == [("artifact document", "artifact message")]
        assert chat_slots(dispatcher) == {}
        assert artifact_slots(dispatcher) == {}
        assert composer_document.request_state == "idle"

    asyncio.run(run())


def test_artifact_request_and_web_research_request_run_concurrently(monkeypatch):
    """Mirrors test_web_research_request_and_chat_request_run_concurrently_
    both_dicts_non_empty: an artifact-generation request must also be able to
    run concurrently with a web-research request - self._runs's "artifact"
    and "web_research" kinds are two more genuinely independent slots,
    neither blocking nor blocked by the other."""
    research_started = threading.Event()
    research_release = threading.Event()
    artifact_started = threading.Event()
    artifact_release = threading.Event()

    def blocking_run(self, request, *, token=None, progress=None):
        research_started.set()
        research_release.wait(5)
        return SimpleNamespace(answer_markdown="research result")

    def blocking_get_response(self, current_artifact, history):
        artifact_started.set()
        artifact_release.wait(5)
        return "artifact document", "artifact message"

    monkeypatch.setattr(agents_module.WebResearchService, "run", blocking_run)
    monkeypatch.setattr(agents_module.ArtifactAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, dispatcher = _make_web_research_env()
        research_node = _make_node()
        artifact_node = _make_node()
        research_successes = []
        artifact_replies = []

        await dispatcher.start_web_research(
            bus=bus,
            notifications_state=notifications,
            node=research_node,
            node_id="n1",
            query="q",
            branch_history=[],
            on_progress=lambda event: None,
            on_success=research_successes.append,
            on_failure=lambda exc: None,
        )
        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=artifact_node,
            current_artifact="doc",
            history=[],
            on_reply=lambda new_content, ai_message: artifact_replies.append((new_content, ai_message)),
        )

        await asyncio.to_thread(research_started.wait, 5)
        await asyncio.to_thread(artifact_started.wait, 5)

        assert len(web_research_slots(dispatcher)) == 1
        assert len(artifact_slots(dispatcher)) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        research_release.set()
        research_entry = next(iter(web_research_slots(dispatcher).values()))
        await research_entry["task"]
        artifact_release.set()
        artifact_entry = next(iter(artifact_slots(dispatcher).values()))
        await artifact_entry["task"]

        assert research_successes == [SimpleNamespace(answer_markdown="research result")]
        assert artifact_replies == [("artifact document", "artifact message")]
        assert web_research_slots(dispatcher) == {}
        assert artifact_slots(dispatcher) == {}

    asyncio.run(run())


# -- R5.3: Gitlink -------------------------------------------------------
#
# The data-integrity core of this whole increment: the fingerprint
# check-and-freeze in start_gitlink_apply must be provably atomic (no await
# between recompute and freeze), the client-supplied fingerprint must be
# checked against BOTH a fresh recompute AND the server's own last-recorded
# fingerprint (a three-way check), and applyGitlinkChanges/start_gitlink_apply
# must never accept a changes/pending_changes payload from the caller.


def _make_gitlink_node(**overrides):
    """Duck-types a SceneNode for AgentDispatcher's gitlink methods, which
    never check isinstance(node, SceneNode) (see this module's own
    docstring). ADR-002 stage 2.5 PR8b: gitlink_* fields now live on
    node.state (a nested SimpleNamespace here), matching real SceneNode
    instances post-shim-removal - node.pending_request_id stays a
    top-level attribute, matching the real dataclass's own core field.
    Callers keep passing gitlink_* kwargs flat; this splits them into the
    nested state automatically, so no call site needs to change shape."""
    state_defaults = dict(
        gitlink_repo="octocat/hello-world",
        gitlink_branch="main",
        gitlink_scope_mode="selected",
        gitlink_local_root="",
        gitlink_imported_root="",
        gitlink_repo_file_paths=[],
        gitlink_selected_paths=[],
        gitlink_task_prompt="",
        gitlink_context_xml="<gitlink_context/>",
        gitlink_context_stats={},
        gitlink_context_summary="",
        gitlink_proposal_markdown="",
        gitlink_pending_changes=[],
        gitlink_preview_text="",
        gitlink_change_fingerprint=None,
        gitlink_change_local_root=None,
        gitlink_change_state="draft",
        gitlink_error="",
    )
    node_overrides = {"pending_request_id": None}
    for key, value in overrides.items():
        if key in state_defaults:
            state_defaults[key] = value
        else:
            node_overrides[key] = value
    return SimpleNamespace(state=SimpleNamespace(**state_defaults), **node_overrides)


def _make_gitlink_env():
    bus = SessionBus("agents-gitlink-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", lambda: {})
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    return bus, notifications, dispatcher


def test_call_gitlink_agent_calls_get_response(monkeypatch):
    captured = []
    fake_result = {
        "summary": "s", "write_intent": "changes_ready", "rationale": "r",
        "notes": [], "files": [], "change_count": 0, "raw_response": "{}",
    }

    def fake_get_response(self, payload):
        captured.append(payload)
        return fake_result

    monkeypatch.setattr(agents_module.GitlinkAgent, "get_response", fake_get_response)

    payload = {"task_prompt": "do x", "context_xml": "<x/>", "repo": "o/r", "branch": "main"}
    result = agents_module._call_gitlink_agent(payload)

    assert result is fake_result
    assert captured == [payload]


# -- start_gitlink_run --------------------------------------------------------


def test_start_gitlink_run_with_changes_calls_on_success_with_fingerprint(monkeypatch):
    fake_result = {
        "summary": "add a health check", "write_intent": "changes_ready", "rationale": "r",
        "notes": [], "change_count": 1,
        "files": [{"path": "a.py", "operation": "update", "reason": "x", "content": "y"}],
        "raw_response": "{}",
    }
    monkeypatch.setattr(agents_module.GitlinkAgent, "get_response", lambda self, payload: fake_result)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        node = _make_gitlink_node()
        successes = []

        await dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            repo="octocat/hello-world", branch="main", scope_mode="selected",
            task_prompt="add a health check", context_xml="<x/>", context_summary="s",
            local_root="",
            on_success=lambda *args: successes.append(args),
            on_failure=lambda message: None,
        )
        entry = next(iter(gitlink_run_slots(dispatcher).values()))
        await entry["task"]

        assert len(successes) == 1
        proposal_markdown, files, preview_text, fingerprint, local_root = successes[0]
        assert files == fake_result["files"]
        assert fingerprint == agents_module._fingerprint_changes(fake_result["files"])
        assert "octocat/hello-world" in proposal_markdown
        assert local_root == "", "the exact local_root this run used must be forwarded to on_success (FIX 2)"
        assert gitlink_run_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is False

    asyncio.run(run())


def test_start_gitlink_run_no_changes_calls_on_success_with_empty_files_and_none_fingerprint(monkeypatch):
    fake_result = {
        "summary": "nothing to change", "write_intent": "no_changes", "rationale": "r",
        "notes": ["no changes needed"], "files": [], "change_count": 0, "raw_response": "{}",
    }
    monkeypatch.setattr(agents_module.GitlinkAgent, "get_response", lambda self, payload: fake_result)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        node = _make_gitlink_node()
        successes = []

        await dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            repo="o/r", branch="main", scope_mode="selected", task_prompt="do nothing",
            context_xml="<x/>", context_summary="s", local_root="",
            on_success=lambda *args: successes.append(args),
            on_failure=lambda message: None,
        )
        entry = next(iter(gitlink_run_slots(dispatcher).values()))
        await entry["task"]

        assert len(successes) == 1
        _proposal_markdown, files, _preview_text, fingerprint, _local_root = successes[0]
        assert files == []
        assert fingerprint is None

    asyncio.run(run())


def test_start_gitlink_run_timeout_fires_the_exact_message_and_clears_the_slot(monkeypatch):
    monkeypatch.setattr(agents_module, "GITLINK_WATCHDOG_TIMEOUT_SECONDS", 0.05)

    def slow_get_response(self, payload):
        time.sleep(0.3)
        return {
            "summary": "s", "write_intent": "no_changes", "rationale": "r",
            "notes": [], "files": [], "change_count": 0, "raw_response": "",
        }

    monkeypatch.setattr(agents_module.GitlinkAgent, "get_response", slow_get_response)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        node = _make_gitlink_node()
        successes = []

        await dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            repo="o/r", branch="main", scope_mode="selected", task_prompt="x",
            context_xml="<x/>", context_summary="s", local_root="",
            on_success=lambda *args: successes.append(args),
            on_failure=lambda message: None,
        )
        entry = next(iter(gitlink_run_slots(dispatcher).values()))
        await entry["task"]

        assert successes == []
        assert gitlink_run_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == (
            "Gitlink generation stopped responding before the request completed. Please try again."
        )

    asyncio.run(run())


def test_start_gitlink_run_cancel_mid_flight_fires_info_notification_and_never_calls_on_success(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_get_response(self, payload):
        started.set()
        release.wait(5)
        return {
            "summary": "s", "write_intent": "changes_ready", "rationale": "r", "notes": [],
            "files": [{"path": "a.py", "operation": "update", "reason": "x", "content": "y"}],
            "change_count": 1, "raw_response": "{}",
        }

    monkeypatch.setattr(agents_module.GitlinkAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        node = _make_gitlink_node()
        successes = []

        await dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            repo="o/r", branch="main", scope_mode="selected", task_prompt="x",
            context_xml="<x/>", context_summary="s", local_root="",
            on_success=lambda *args: successes.append(args),
            on_failure=lambda message: None,
        )
        request_id, entry = next(iter(gitlink_run_slots(dispatcher).items()))

        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel_gitlink(request_id) is True
        release.set()
        await entry["task"]

        assert successes == [], "a cancelled run must never call on_success, even on a late return"
        assert gitlink_run_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "Gitlink generation cancelled."

    asyncio.run(run())


def test_cancel_gitlink_returns_false_for_an_unknown_request_id():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    assert dispatcher.cancel_gitlink("no-such-request") is False


def test_start_gitlink_run_busy_node_refuses_immediately_without_creating_a_request_entry():
    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        node = _make_gitlink_node(pending_request_id="already-busy")

        await dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            repo="o/r", branch="main", scope_mode="selected", task_prompt="x",
            context_xml="<x/>", context_summary="s", local_root="",
            on_success=lambda *args: None, on_failure=lambda message: None,
        )

        assert gitlink_run_slots(dispatcher) == {}
        assert notifications.visible is True
        assert notifications.msg_type == "info"

    asyncio.run(run())


# -- start_gitlink_apply: the data-integrity core -----------------------------


def test_gitlink_apply_rejects_client_fingerprint_mismatch(monkeypatch):
    def raising_apply_change_set(local_root, pending_changes):
        raise AssertionError("apply_change_set must never be reached on a fingerprint mismatch")

    monkeypatch.setattr(agents_module, "apply_change_set", raising_apply_change_set)

    async def run(tmp_path):
        bus, notifications, dispatcher = _make_gitlink_env()
        changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
        real_fingerprint = agents_module._fingerprint_changes(changes)
        node = _make_gitlink_node(
            gitlink_pending_changes=changes,
            gitlink_change_fingerprint=real_fingerprint,
            gitlink_local_root=str(tmp_path),
        )
        failures = []
        successes = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint="deliberately-wrong-fingerprint", local_root=str(tmp_path),
            on_success=successes.append, on_failure=failures.append,
        )

        assert failures == [
            "The proposed change set changed after approval. Review it again before applying."
        ]
        assert successes == []
        assert gitlink_apply_slots(dispatcher) == {}, "no apply task must ever have been scheduled"

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(run(Path(tmp)))


def test_gitlink_apply_rejects_when_pending_changes_mutated_between_generation_and_apply(monkeypatch, tmp_path):
    def raising_apply_change_set(local_root, pending_changes):
        raise AssertionError("apply_change_set must never be reached when the recorded fingerprint is stale")

    monkeypatch.setattr(agents_module, "apply_change_set", raising_apply_change_set)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        changes_a = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
        changes_b = [{"path": "b.py", "operation": "update", "reason": "r2", "content": "y"}]
        fingerprint_for_a = agents_module._fingerprint_changes(changes_a)
        # Simulates a second Run landing (mutating pending_changes) WITHOUT
        # going through complete_gitlink_run's own fingerprint-recording -
        # node.state.gitlink_change_fingerprint is left stale, still pointing at A.
        node = _make_gitlink_node(
            gitlink_pending_changes=changes_b,
            gitlink_change_fingerprint=fingerprint_for_a,
            gitlink_local_root=str(tmp_path),
        )
        failures = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint_for_a, local_root=str(tmp_path),
            on_success=lambda written_files: None, on_failure=failures.append,
        )

        assert failures == [
            "The proposed change set changed after approval. Review it again before applying."
        ]
        assert gitlink_apply_slots(dispatcher) == {}

    asyncio.run(run())


def test_gitlink_apply_rejects_when_client_fingerprint_matches_live_data_but_not_the_originally_approved_one(
    monkeypatch, tmp_path
):
    """Pins the 3-way fingerprint compare's third clause (audit finding G3,
    doc/adr/AUDIT-2026-08-03-approval-guards-results.md): start_gitlink_apply
    checks client-claimed vs freshly-recomputed vs originally-approved-and-
    stored, not just client vs current. This constructs the ONE scenario
    only the third clause catches: the client's claimed fingerprint agrees
    with a fresh recompute of the LIVE pending_changes (so the first clause,
    client-vs-current, passes clean) - but that live data was never actually
    the thing recorded as approved (node.state.gitlink_change_fingerprint
    still points at a DIFFERENT, earlier change set). The sibling test above
    (test_gitlink_apply_rejects_when_pending_changes_mutated_between_
    generation_and_apply) happens to pass a client_fingerprint that matches
    the STALE stored value instead of the live data, which only ever
    exercises the first clause - this is the direct test of the second
    comparison, which a prior mutation-testing audit found had zero
    coverage before this test existed."""
    def raising_apply_change_set(local_root, pending_changes):
        raise AssertionError(
            "apply_change_set must never be reached when the live data was never actually approved"
        )

    monkeypatch.setattr(agents_module, "apply_change_set", raising_apply_change_set)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        changes_a = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
        changes_b = [{"path": "b.py", "operation": "update", "reason": "r2", "content": "y"}]
        fingerprint_for_a = agents_module._fingerprint_changes(changes_a)
        fingerprint_for_b = agents_module._fingerprint_changes(changes_b)
        # Live pending_changes is B, and the client's own claim
        # (fingerprint_for_b) genuinely matches a fresh recompute of that
        # live data - clause 1 (client vs current) passes clean. But the
        # STORED fingerprint still points at A, meaning B was never actually
        # the change set recorded as approved. gitlink_change_local_root is
        # deliberately set to match local_root_text below, so the SEPARATE
        # local_root-binding check (a different guard entirely) cannot also
        # trip and mask which check is actually being exercised here.
        node = _make_gitlink_node(
            gitlink_pending_changes=changes_b,
            gitlink_change_fingerprint=fingerprint_for_a,
            gitlink_local_root=str(tmp_path),
            gitlink_change_local_root=str(tmp_path),
        )
        failures = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint_for_b, local_root=str(tmp_path),
            on_success=lambda written_files: None, on_failure=failures.append,
        )
        # The guard must trip SYNCHRONOUSLY, before any background task is
        # ever created - matching every other rejection test in this file.
        # If it does not (e.g. a future regression), fall through and await
        # whatever task WAS scheduled so the failure surfaces as a clean
        # assertion below, not a dangling unawaited task.
        entry = next(iter(gitlink_apply_slots(dispatcher).values()), None)
        if entry is not None:
            await entry["task"]

        assert failures == [
            "The proposed change set changed after approval. Review it again before applying."
        ]
        assert gitlink_apply_slots(dispatcher) == {}

    asyncio.run(run())


def test_gitlink_apply_freezes_changes_before_await(monkeypatch, tmp_path):
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "original"}]
    fingerprint = agents_module._fingerprint_changes(changes)
    node = _make_gitlink_node(
        gitlink_pending_changes=changes,
        gitlink_change_fingerprint=fingerprint,
        gitlink_local_root=str(tmp_path),
        gitlink_change_local_root=str(tmp_path),
    )
    captured = {}

    def mutating_apply_change_set(local_root, pending_changes):
        # Proves the write uses a DISTINCT, already-frozen list/copy - not
        # node.state.gitlink_pending_changes itself.
        assert pending_changes is not node.state.gitlink_pending_changes
        captured["frozen_content"] = pending_changes[0]["content"]
        # Mutate the LIVE node list from inside this patched function, to
        # prove the write still used the original frozen snapshot's content,
        # not whatever the live list is mutated to afterward.
        node.state.gitlink_pending_changes[0]["content"] = "mutated-after-freeze"
        return 1

    monkeypatch.setattr(agents_module, "apply_change_set", mutating_apply_change_set)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        successes = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root=str(tmp_path),
            on_success=successes.append, on_failure=lambda message: None,
        )
        entry = next(iter(gitlink_apply_slots(dispatcher).values()))
        await entry["task"]

        assert successes == [1]
        assert captured["frozen_content"] == "original", (
            "the write must use the frozen snapshot's content, unaffected by the later mutation"
        )
        assert node.state.gitlink_pending_changes[0]["content"] == "mutated-after-freeze"

    asyncio.run(run())


def test_gitlink_apply_busy_guard_blocks_concurrent_run_and_apply(tmp_path):
    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
        fingerprint = agents_module._fingerprint_changes(changes)
        node = _make_gitlink_node(
            pending_request_id="an-in-flight-request",
            gitlink_pending_changes=changes,
            gitlink_change_fingerprint=fingerprint,
            gitlink_local_root=str(tmp_path),
        )

        await dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            repo="o/r", branch="main", scope_mode="selected", task_prompt="x",
            context_xml="<x/>", context_summary="s", local_root="",
            on_success=lambda *args: None, on_failure=lambda message: None,
        )
        assert gitlink_run_slots(dispatcher) == {}, "Run must refuse immediately for a busy node"

        failures = []
        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root=str(tmp_path),
            on_success=lambda written_files: None, on_failure=failures.append,
        )
        assert gitlink_apply_slots(dispatcher) == {}, "Apply must refuse immediately for a busy node"
        assert failures == [], "the busy guard shows a notification, not an on_failure call"
        assert notifications.visible is True
        assert notifications.message == "Gitlink is already busy for this node."

    asyncio.run(run())


def test_gitlink_apply_no_pending_changes_calls_on_failure_without_touching_apply_change_set(monkeypatch, tmp_path):
    def raising_apply_change_set(local_root, pending_changes):
        raise AssertionError("apply_change_set must never be reached with no pending changes")

    monkeypatch.setattr(agents_module, "apply_change_set", raising_apply_change_set)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        node = _make_gitlink_node(gitlink_local_root=str(tmp_path))
        failures = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint="whatever", local_root=str(tmp_path),
            on_success=lambda written_files: None, on_failure=failures.append,
        )

        assert failures == ["There is no approved change set to write."]
        assert gitlink_apply_slots(dispatcher) == {}

    asyncio.run(run())


def test_gitlink_apply_missing_local_root_calls_on_failure(monkeypatch):
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    fingerprint = agents_module._fingerprint_changes(changes)
    node = _make_gitlink_node(
        gitlink_pending_changes=changes, gitlink_change_fingerprint=fingerprint, gitlink_local_root="",
    )

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        failures = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root="",
            on_success=lambda written_files: None, on_failure=failures.append,
        )

        assert failures == ["Select or import a local repository path before applying changes."]
        assert gitlink_apply_slots(dispatcher) == {}

    asyncio.run(run())


def test_gitlink_apply_nonexistent_local_root_calls_on_failure(monkeypatch):
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    fingerprint = agents_module._fingerprint_changes(changes)
    missing_root = "C:/this/path/does/not/exist/for/sure/gitlink-test"
    node = _make_gitlink_node(
        gitlink_pending_changes=changes, gitlink_change_fingerprint=fingerprint, gitlink_local_root=missing_root,
    )

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        failures = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root=missing_root,
            on_success=lambda written_files: None, on_failure=failures.append,
        )

        assert failures == ["The selected local repository path does not exist."]
        assert gitlink_apply_slots(dispatcher) == {}

    asyncio.run(run())


def test_gitlink_apply_success_calls_on_success_with_written_files_count(monkeypatch, tmp_path):
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    fingerprint = agents_module._fingerprint_changes(changes)
    node = _make_gitlink_node(
        gitlink_pending_changes=changes, gitlink_change_fingerprint=fingerprint, gitlink_local_root=str(tmp_path),
        gitlink_change_local_root=str(tmp_path),
    )
    monkeypatch.setattr(agents_module, "apply_change_set", lambda local_root, pending_changes: 3)
    monkeypatch.setattr(agents_module, "validate_pending_changes", lambda pending_changes: None)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        successes = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root=str(tmp_path),
            on_success=successes.append, on_failure=lambda message: None,
        )
        entry = next(iter(gitlink_apply_slots(dispatcher).values()))
        await entry["task"]

        assert successes == [3]
        assert gitlink_apply_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "Applied 3 file changes."

    asyncio.run(run())


def test_gitlink_apply_transitions_to_applying_before_the_write_starts(monkeypatch, tmp_path):
    """Pins the state-machine transition (audit finding G6,
    doc/adr/AUDIT-2026-08-03-approval-guards-results.md):
    node.state.gitlink_change_state must already be "applying" by the time
    the actual write (apply_change_set) is reached, not left at its prior
    value (here, "draft" - the GitlinkState default, since this node is
    constructed directly rather than via a real complete_gitlink_run call;
    a real approved node would be "previewed" instead) while a write is
    silently in flight. Unlike every other finding in this
    audit, this field is UI/observability-only - no other check in this
    file reads gitlink_change_state - so a regression here is a real, if
    non-security, correctness bug (the frontend would show a stale status
    while a write is actually happening)."""
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    fingerprint = agents_module._fingerprint_changes(changes)
    node = _make_gitlink_node(
        gitlink_pending_changes=changes, gitlink_change_fingerprint=fingerprint, gitlink_local_root=str(tmp_path),
        gitlink_change_local_root=str(tmp_path),
    )
    observed_state = []

    def capturing_apply_change_set(local_root, pending_changes):
        # Called from the worker thread apply_change_set actually runs on
        # (asyncio.to_thread) - a plain attribute read is safe here under
        # CPython's GIL, same posture as the fake sandboxes' own call-capture
        # mocks elsewhere in this file.
        observed_state.append(node.state.gitlink_change_state)
        return 1

    monkeypatch.setattr(agents_module, "apply_change_set", capturing_apply_change_set)
    monkeypatch.setattr(agents_module, "validate_pending_changes", lambda pending_changes: None)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        successes = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root=str(tmp_path),
            on_success=successes.append, on_failure=lambda message: None,
        )
        entry = next(iter(gitlink_apply_slots(dispatcher).values()))
        await entry["task"]

        assert successes == [1]
        assert observed_state == ["applying"], (
            'node.state.gitlink_change_state must already be "applying" by the time '
            "the write actually starts"
        )

    asyncio.run(run())


def test_gitlink_apply_rollback_message_surfaced_verbatim(monkeypatch, tmp_path):
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    fingerprint = agents_module._fingerprint_changes(changes)
    node = _make_gitlink_node(
        gitlink_pending_changes=changes, gitlink_change_fingerprint=fingerprint, gitlink_local_root=str(tmp_path),
        gitlink_change_local_root=str(tmp_path),
    )
    # The exact rollback RuntimeError shape repository.py's own apply_change_set
    # produces on a failed restore (see its own docstring/comment).
    rollback_message = (
        "disk full (rolled back all other changes, but could not restore: "
        f"{tmp_path}/a.py)"
    )

    def raising_apply_change_set(local_root, pending_changes):
        raise RuntimeError(rollback_message)

    monkeypatch.setattr(agents_module, "apply_change_set", raising_apply_change_set)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        failures = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root=str(tmp_path),
            on_success=lambda written_files: None, on_failure=failures.append,
        )
        entry = next(iter(gitlink_apply_slots(dispatcher).values()))
        await entry["task"]

        assert failures == [f"Failed to write approved changes: {rollback_message}"]
        assert gitlink_apply_slots(dispatcher) == {}
        assert notifications.visible is True
        assert notifications.msg_type == "error"

    asyncio.run(run())


def test_gitlink_apply_timeout_fires_the_exact_message_and_clears_the_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(agents_module, "GITLINK_APPLY_TIMEOUT_SECONDS", 0.05)
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    fingerprint = agents_module._fingerprint_changes(changes)
    node = _make_gitlink_node(
        gitlink_pending_changes=changes, gitlink_change_fingerprint=fingerprint, gitlink_local_root=str(tmp_path),
        gitlink_change_local_root=str(tmp_path),
    )

    def slow_apply_change_set(local_root, pending_changes):
        time.sleep(0.3)
        return 1

    monkeypatch.setattr(agents_module, "apply_change_set", slow_apply_change_set)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        failures = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root=str(tmp_path),
            on_success=lambda written_files: None, on_failure=failures.append,
        )
        entry = next(iter(gitlink_apply_slots(dispatcher).values()))
        await entry["task"]

        assert len(failures) == 1
        assert "stopped responding" in failures[0]
        assert gitlink_apply_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


# -- R5.3 post-review FIX 1/FIX 2 ---------------------------------------------


def test_gitlink_apply_rejects_when_local_root_changed_since_generation(monkeypatch, tmp_path):
    """R5.3 post-review FIX 2 (HIGH): _fingerprint_changes only hashes file
    content/paths/operations, never local_root - so an unchanged, still-valid
    fingerprint must NOT be enough to authorize a write once the local_root
    binding recorded at Run time no longer matches the local_root passed to
    Apply. Both checkout paths must actually exist on disk (the exists()
    check runs BEFORE this new check), so this uses two real tmp_path
    subdirectories rather than illustrative non-existent paths."""
    def raising_apply_change_set(local_root, pending_changes):
        raise AssertionError("apply_change_set must never be reached when local_root changed since generation")

    monkeypatch.setattr(agents_module, "apply_change_set", raising_apply_change_set)

    checkout_a = tmp_path / "checkout-a"
    checkout_a.mkdir()
    checkout_b = tmp_path / "checkout-b"
    checkout_b.mkdir()

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
        fingerprint = agents_module._fingerprint_changes(changes)
        # The change set was generated (Run) against checkout_a, but
        # gitlink_local_root has since been edited to checkout_b - the
        # fingerprint itself is still perfectly valid (nothing about the
        # CONTENT changed), which is exactly why FIX 2 exists.
        node = _make_gitlink_node(
            gitlink_pending_changes=changes,
            gitlink_change_fingerprint=fingerprint,
            gitlink_change_local_root=str(checkout_a),
            gitlink_local_root=str(checkout_b),
        )
        failures = []

        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root=str(checkout_b),
            on_success=lambda written_files: None, on_failure=failures.append,
        )

        assert failures == [
            "The local repository path changed since this proposal was generated. "
            "Regenerate the change set before applying."
        ]
        assert gitlink_apply_slots(dispatcher) == {}, "no apply task must ever have been scheduled"

    asyncio.run(run())


def test_gitlink_apply_cannot_be_replayed_after_success(monkeypatch, tmp_path):
    """R5.3 post-review FIX 1 (CRITICAL): a successful Apply must invalidate
    the approval it just consumed. Exercises the REAL
    canvas.SceneDocument.complete_gitlink_apply/complete_gitlink_run wiring
    (not a bespoke test stub) since that is where the fix actually lives -
    on_success below is exactly what backend/canvas.py's
    apply_gitlink_changes wires up in production. Runs start_gitlink_apply
    to a successful completion once, then attempts calling it AGAIN with the
    SAME original fingerprint, and asserts the replay is refused with the
    "no approved change set" message and apply_change_set is never invoked
    the second time."""
    changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
    fingerprint = agents_module._fingerprint_changes(changes)
    apply_calls = []

    def counting_apply_change_set(local_root, pending_changes):
        apply_calls.append(list(pending_changes))
        return 1

    monkeypatch.setattr(agents_module, "apply_change_set", counting_apply_change_set)
    monkeypatch.setattr(agents_module, "validate_pending_changes", lambda pending_changes: None)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        document = SceneDocument()
        parent = document.add_chat_node(0, 0, "root", True)
        node = document.add_gitlink_node(0, 0, parent.id)
        document.complete_gitlink_run(node.id, "## Gitlink Proposal", changes, "diff", fingerprint, str(tmp_path))

        def _on_success(written_files):
            document.complete_gitlink_apply(node.id, written_files)

        def _on_failure(message):
            document.fail_gitlink_apply(node.id, message)

        # First Apply: succeeds, consuming the approval.
        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id=node.id,
            client_fingerprint=fingerprint, local_root=str(tmp_path),
            on_success=_on_success, on_failure=_on_failure,
        )
        entry = next(iter(gitlink_apply_slots(dispatcher).values()))
        await entry["task"]

        assert len(apply_calls) == 1
        assert node.state.gitlink_change_state == "applied"
        assert node.state.gitlink_pending_changes == [], "the approval must be cleared on success (FIX 1)"
        assert node.state.gitlink_change_fingerprint is None, "a consumed approval must never be replayable"
        assert node.pending_request_id is None

        # Second Apply attempt with the SAME original fingerprint: must be
        # refused, and apply_change_set must never be invoked again.
        failures = []
        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id=node.id,
            client_fingerprint=fingerprint, local_root=str(tmp_path),
            on_success=_on_success, on_failure=failures.append,
        )

        assert failures == ["There is no approved change set to write."]
        assert len(apply_calls) == 1, "apply_change_set must NOT be invoked on the replay attempt"
        assert gitlink_apply_slots(dispatcher) == {}

    asyncio.run(run())


# -- R5.3 post-review FIX 5: real concurrent interleaving for Apply-vs-Apply,
# not a pre-set busy flag -----------------------------------------------------


def test_two_concurrent_start_gitlink_apply_calls_for_the_same_node_only_one_reaches_the_write_path(
    monkeypatch, tmp_path,
):
    """R5.3 post-review FIX 5: before this fix, node.pending_request_id was
    only claimed at the very END of start_gitlink_apply - AFTER the
    local_root_text validation, the `await asyncio.to_thread(local_root_
    path.exists)` yield point, and the entire atomic fingerprint/local_root
    check-and-freeze section - so two genuinely concurrent Apply calls for
    the SAME node (two different WebSocket connections on the same session,
    e.g. two browser tabs - not a single connection's sequential message
    loop) could both read node.pending_request_id as falsy before either
    claimed it, both pass every check, and both end up scheduling a write via
    apply_change_set concurrently.

    This drives TWO REAL coroutines through a genuine asyncio interleaving -
    both dispatcher.start_gitlink_apply(...) calls are fired via
    asyncio.gather without either being awaited to completion first, exactly
    the scenario the fix spec calls for - not the trivial
    pre-set-pending_request_id case
    test_gitlink_apply_busy_guard_blocks_concurrent_run_and_apply above
    already covers. Mirrors
    test_two_concurrent_run_gitlink_change_set_calls_for_the_same_node_only_one_reaches_the_agent's
    own mechanism in test_canvas.py (asyncio.gather, a call-counting patch,
    then draining the one admitted background task afterward). apply_change_set
    is ALSO patched to actually sleep (a real blocking sleep inside the
    asyncio.to_thread-wrapped worker call) so there is a genuine interleaving
    opportunity even if some future change removed the already-real
    `local_root_path.exists()` await this test also relies on."""
    call_count = {"n": 0}

    def slow_counting_apply_change_set(local_root, pending_changes):
        call_count["n"] += 1
        time.sleep(0.05)
        return len(pending_changes)

    monkeypatch.setattr(agents_module, "apply_change_set", slow_counting_apply_change_set)
    monkeypatch.setattr(agents_module, "validate_pending_changes", lambda pending_changes: None)

    async def run():
        bus, notifications, dispatcher = _make_gitlink_env()
        changes = [{"path": "a.py", "operation": "update", "reason": "r", "content": "x"}]
        fingerprint = agents_module._fingerprint_changes(changes)
        node = _make_gitlink_node(
            gitlink_pending_changes=changes,
            gitlink_change_fingerprint=fingerprint,
            gitlink_change_local_root=str(tmp_path),
            gitlink_local_root=str(tmp_path),
        )
        successes = []
        failures = []

        await asyncio.gather(
            dispatcher.start_gitlink_apply(
                bus=bus, notifications_state=notifications, node=node, node_id="n1",
                client_fingerprint=fingerprint, local_root=str(tmp_path),
                on_success=successes.append, on_failure=failures.append,
            ),
            dispatcher.start_gitlink_apply(
                bus=bus, notifications_state=notifications, node=node, node_id="n1",
                client_fingerprint=fingerprint, local_root=str(tmp_path),
                on_success=successes.append, on_failure=failures.append,
            ),
        )

        # Deterministic here, same reasoning as the Run-vs-Run test in
        # test_canvas.py: neither coroutine's own body has a genuine
        # suspension point before the busy claim, so asyncio's FIFO task
        # scheduling always lets the FIRST-created call win the claim; the
        # second one sees a truthy node.pending_request_id immediately and
        # is rejected via the plain "already busy" notification branch (no
        # on_failure call for that branch - see start_gitlink_apply's own
        # busy-check at the very top).
        assert len(gitlink_apply_slots(dispatcher)) == 1, (
            "only ONE Apply may ever be admitted for this node at a time"
        )
        entry = next(iter(gitlink_apply_slots(dispatcher).values()))
        await entry["task"]

        assert call_count["n"] == 1, "only ONE of the two concurrent calls may ever reach the write path"
        assert successes == [1], "the admitted call's on_success must fire exactly once"
        assert failures == [], "the rejected call is refused via the busy notification, not on_failure"
        assert gitlink_apply_slots(dispatcher) == {}
        assert node.pending_request_id is None, (
            "the busy slot must be fully released once the admitted Apply finishes"
        )

    asyncio.run(run())


def test_gitlink_apply_no_changes_payload_in_intent_signature():
    """Signature-inspection regression guard: the registered
    applyGitlinkChanges WS intent handler must take EXACTLY two parameters
    (node_id, fingerprint) - guards against a future regression that adds a
    changes/pending_changes argument, which would let a client inject
    arbitrary file content into the write path."""
    bus = SessionBus("gitlink-signature-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    composer_document = ComposerDocument()
    bus.register_topic("app-composer", composer_document.payload)

    class _FakeDispatcher:
        async def start_gitlink_apply(self, **kwargs):
            pass

    register_canvas(bus, notifications, _FakeDispatcher(), composer_document)

    handler = bus._intents[("scene", "applyGitlinkChanges")].handler
    signature = inspect.signature(handler)
    assert list(signature.parameters) == ["node_id", "fingerprint"], (
        "applyGitlinkChanges must take ONLY (node_id, fingerprint) - no changes/pending_changes param"
    )


def test_gitlink_request_and_other_kind_request_run_concurrently(monkeypatch):
    """Mirrors the other cross-kind concurrency tests: a Gitlink Run request
    must run concurrently with (neither blocking nor blocked by) a chat/
    composer request - self._runs's "gitlink_run" and "chat" kinds are two
    genuinely independent slots."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    gitlink_started = threading.Event()
    gitlink_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_get_response(self, payload):
        gitlink_started.set()
        gitlink_release.wait(5)
        return {
            "summary": "s", "write_intent": "changes_ready", "rationale": "r", "notes": [],
            "files": [{"path": "a.py", "operation": "update", "reason": "x", "content": "y"}],
            "change_count": 1, "raw_response": "{}",
        }

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(agents_module.GitlinkAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        chat_replies = []
        gitlink_successes = []
        gitlink_node = _make_gitlink_node()

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=chat_replies.append,
        )
        await dispatcher.start_gitlink_run(
            bus=bus, notifications_state=notifications, node=gitlink_node, node_id="n1",
            repo="o/r", branch="main", scope_mode="selected", task_prompt="x",
            context_xml="<x/>", context_summary="s", local_root="",
            on_success=lambda *args: gitlink_successes.append(args),
            on_failure=lambda message: None,
        )

        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(gitlink_started.wait, 5)

        assert len(chat_slots(dispatcher)) == 1
        assert len(gitlink_run_slots(dispatcher)) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(chat_slots(dispatcher).values()))
        await chat_entry["task"]
        gitlink_release.set()
        gitlink_entry = next(iter(gitlink_run_slots(dispatcher).values()))
        await gitlink_entry["task"]

        assert chat_replies == ["chat reply"]
        assert len(gitlink_successes) == 1
        assert chat_slots(dispatcher) == {}
        assert gitlink_run_slots(dispatcher) == {}

    asyncio.run(run())


def test_agents_never_imports_qt():
    # This is the regression gate for Step 0's providers.py fix - mirrors
    # test_plugins.py's own test_plugins_never_imports_qt's exact
    # subprocess-invocation style (a plain in-process assert is meaningless
    # once anything else in a shared pytest run has already imported
    # PySide6; only a fresh subprocess importing ONLY backend.agents actually
    # answers "does this transitively pull in Qt"). Before Step 0's fix,
    # `import backend.agents` -> WebResearchService -> providers.py ->
    # `import graphlink_config as config` -> PySide6.QtGui/QtWidgets at
    # module scope - a real regression this test would have caught.
    import subprocess
    import sys as _sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [_sys.executable, "-c", "import backend.agents, sys; assert 'PySide6' not in sys.modules"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# -- R5.4: Py-Coder / Execution Sandbox ---------------------------------------
#
# The approve/deny asyncio.Future mechanism is the novel piece this section
# exercises hardest: the future is created EAGERLY, before the background
# task is even scheduled (see AgentDispatcher.start_pycoder_run/
# start_code_sandbox_run's own docstrings) - so approve_code_execution/
# deny_code_execution/cancel_pycoder/cancel_code_sandbox/
# cancel_all_pending_approvals can all resolve it at ANY point in the
# request's lifetime, even before the pipeline itself ever reaches its own
# `await approval_future` line. Tests below rely on this: resolving the
# future immediately after start_*_run returns (before awaiting the task to
# completion) is equivalent to resolving it while genuinely parked on the
# gate, since asyncio.Future carries its resolved value regardless of when
# `await` is issued on it.


def _make_pycoder_node(**overrides):
    """Duck-types a SceneNode for AgentDispatcher's pycoder methods, which
    never check isinstance(node, SceneNode) (see this module's own
    docstring). ADR-002 stage 2.5 PR9b: pycoder_* fields now live on
    node.state (a nested SimpleNamespace here), matching real SceneNode
    instances post-shim-removal - node.pending_request_id stays a
    top-level attribute, matching the real dataclass's own core field.
    Callers keep passing pycoder_* kwargs flat; this splits them into the
    nested state automatically, so no call site needs to change shape."""
    state_defaults = dict(
        pycoder_mode="ai_driven",
        pycoder_prompt="",
        pycoder_code="",
        pycoder_output="",
        pycoder_analysis="",
        pycoder_last_run_failed=False,
        pycoder_awaiting_approval=False,
        pycoder_approved_fingerprint=None,
        pycoder_error="",
        pycoder_repl_id="fake-repl-id",
    )
    node_overrides = {"pending_request_id": None}
    for key, value in overrides.items():
        if key in state_defaults:
            state_defaults[key] = value
        else:
            node_overrides[key] = value
    return SimpleNamespace(state=SimpleNamespace(**state_defaults), **node_overrides)


def _make_code_sandbox_node(**overrides):
    """Duck-types a SceneNode for AgentDispatcher's code_sandbox methods,
    which never check isinstance(node, SceneNode) (see this module's own
    docstring). ADR-002 stage 2.5 PR10b: code_sandbox_* fields now live on
    node.state (a nested SimpleNamespace here), matching real SceneNode
    instances post-shim-removal - node.pending_request_id stays a
    top-level attribute, matching the real dataclass's own core field.
    Callers keep passing code_sandbox_* kwargs flat; this splits them into
    the nested state automatically, so no call site needs to change
    shape."""
    state_defaults = dict(
        code_sandbox_sandbox_id="sandbox-test-1",
        code_sandbox_requirements="",
        code_sandbox_prompt="",
        code_sandbox_code="",
        code_sandbox_output="",
        code_sandbox_analysis="",
        code_sandbox_awaiting_approval=False,
        code_sandbox_approval_requirements="",
        code_sandbox_approved_fingerprint=None,
        code_sandbox_approval_allow_source_builds=False,
        code_sandbox_error="",
    )
    node_overrides = {"pending_request_id": None}
    for key, value in overrides.items():
        if key in state_defaults:
            state_defaults[key] = value
        else:
            node_overrides[key] = value
    return SimpleNamespace(state=SimpleNamespace(**state_defaults), **node_overrides)


async def _approve_every_gate_until_done(dispatcher, request_id, task, max_iterations=400):
    """ADR-002 P0 test helper: the repair loop now opens a FRESH approval
    gate (a new asyncio.Future replacing the resolved one on the same
    registry handle) before every repaired execution attempt, instead of
    reusing the original approval. There is no single moment to "resolve
    the future" the way the old one-gate-per-run tests could (see this
    section's own docstring for why resolving before the task even starts
    still works for a SINGLE gate) - so this polls, approving whatever gate
    is currently open, until the task completes. approve_code_execution/
    deny_code_execution are idempotent on an already-resolved future (see
    AgentDispatcher._resolve_approval's own `future.done()` guard), so
    calling this every tick is safe even when no new gate has opened yet.

    ADR-002 stage 2.4g: `dispatcher._runs.get(request_id) is not None` is
    the direct registry-based liveness check replacing the old
    `request_id in requests_dict` membership test - one shared namespace
    now, so no per-kind dict name to pass in."""
    for _ in range(max_iterations):
        if task.done():
            return
        if dispatcher._runs.get(request_id) is not None:
            dispatcher.approve_code_execution(request_id)
        await asyncio.sleep(0.005)
    raise AssertionError("task did not complete after repeatedly approving every gate")


def _make_code_exec_env():
    bus = SessionBus("agents-code-exec-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", lambda: {})
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    return bus, notifications, dispatcher


class _FakeRepl:
    """A duck-typed stand-in for PythonREPL - avoids spawning a real
    subprocess for tests that only care about the dispatch pipeline's own
    control flow."""

    def __init__(self, script=None):
        # script: list of (output, failed) tuples, one per execute() call;
        # the last entry repeats if execute() is called more times than the
        # script has entries.
        self.script = list(script or [("", False)])
        self.last_run_failed = False
        self.calls = []
        self.stopped = False

    def execute(self, code):
        self.calls.append(code)
        output, failed = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        self.last_run_failed = failed
        return output

    def stop(self):
        self.stopped = True


# -- Py-Coder: manual mode -----------------------------------------------------


def test_pycoder_manual_mode_blank_code_calls_on_failure_without_creating_a_repl(monkeypatch):
    repl_created = []
    monkeypatch.setattr(
        AgentDispatcher, "get_pycoder_repl", lambda self, node_id, repl_id=None: repl_created.append(node_id) or _FakeRepl()
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="manual")
        failures = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="manual", prompt="", code="   ", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        entry = next(iter(pycoder_slots(dispatcher).values()))
        await entry["task"]

        assert failures == ["Add Python code before running Py-Coder."]
        assert repl_created == [], "a blank-code guard must never touch the REPL"
        assert pycoder_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_pycoder_manual_mode_success_executes_once_and_analyzes(monkeypatch):
    fake_repl = _FakeRepl(script=[("42", False)])
    monkeypatch.setattr(AgentDispatcher, "get_pycoder_repl", lambda self, node_id, repl_id=None: fake_repl)
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: f"analysis of {code_output}",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="manual")
        successes = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="manual", prompt="", code="print(42)", conversation_history=[],
            on_success=lambda *a: successes.append(a), on_failure=lambda m: None,
        )
        entry = next(iter(pycoder_slots(dispatcher).values()))
        await entry["task"]

        assert successes == [("print(42)", "42", "analysis of 42", False)]
        assert fake_repl.calls == ["print(42)"]
        assert pycoder_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_pycoder_manual_mode_reports_last_run_failed_from_the_repl(monkeypatch):
    fake_repl = _FakeRepl(script=[("Traceback...", True)])
    monkeypatch.setattr(AgentDispatcher, "get_pycoder_repl", lambda self, node_id, repl_id=None: fake_repl)
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "explains the error",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="manual")
        successes = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="manual", prompt="", code="raise ValueError()", conversation_history=[],
            on_success=lambda *a: successes.append(a), on_failure=lambda m: None,
        )
        entry = next(iter(pycoder_slots(dispatcher).values()))
        await entry["task"]

        assert len(successes) == 1
        code, output, analysis, last_run_failed = successes[0]
        assert last_run_failed is True
        assert output == "Traceback..."

    asyncio.run(run())


def test_pycoder_manual_mode_execute_timeout_disposes_the_repl_and_calls_on_failure(monkeypatch):
    monkeypatch.setattr(agents_module, "PYCODER_EXECUTE_TIMEOUT_SECONDS", 0.05)

    class _SlowRepl:
        def __init__(self):
            self.last_run_failed = False
            self.stopped = False

        def execute(self, code):
            time.sleep(0.3)
            return "too late"

        def stop(self):
            self.stopped = True

    fake_repl = _SlowRepl()

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        # Populate the REAL dict directly (not via a monkeypatched
        # get_pycoder_repl) so dispose_pycoder_repl's own real pop-and-stop
        # logic has something genuine to tear down.
        dispatcher._pycoder_repls["n1"] = fake_repl
        node = _make_pycoder_node(pycoder_mode="manual")
        failures = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="manual", prompt="", code="while True: pass", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        entry = next(iter(pycoder_slots(dispatcher).values()))
        await entry["task"]

        assert len(failures) == 1
        assert "timed out" in failures[0] or "stopped responding" in failures[0]
        assert fake_repl.stopped is True, "the hung REPL must be torn down on timeout"
        assert "n1" not in dispatcher._pycoder_repls
        assert pycoder_slots(dispatcher) == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "error"

    asyncio.run(run())


# -- Py-Coder: ai_driven mode ---------------------------------------------------


def test_pycoder_ai_driven_empty_prompt_calls_on_failure():
    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")
        failures = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="   ", code="", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        entry = next(iter(pycoder_slots(dispatcher).values()))
        await entry["task"]

        assert failures == ["Please enter a prompt."]
        assert pycoder_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_pycoder_ai_driven_no_code_generated_calls_on_success_with_placeholder_and_skips_approval(monkeypatch):
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "Here is a direct answer, no code needed.",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")
        successes = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="what is 1+1", code="", conversation_history=[],
            on_success=lambda *a: successes.append(a), on_failure=lambda m: None,
        )
        entry = next(iter(pycoder_slots(dispatcher).values()))
        await entry["task"]

        assert successes == [(
            "# No code was generated for this prompt.",
            "[Not applicable]",
            "Here is a direct answer, no code needed.",
            False,
        )]
        assert node.state.pycoder_awaiting_approval is False, "no code ever means no approval gate"

    asyncio.run(run())


def test_pycoder_ai_driven_denied_approval_calls_on_failure_with_the_exact_legacy_message(monkeypatch):
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "[TOOL:PYTHON]\nprint(1)\n[/TOOL]",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")
        failures = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="add 1+1", code="", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        request_id, entry = next(iter(pycoder_slots(dispatcher).items()))

        # Resolve BEFORE awaiting the task to completion - proves the future
        # carries its value regardless of when the pipeline's own `await
        # approval_future` actually executes (see this section's own
        # docstring).
        assert dispatcher.deny_code_execution(request_id) is True
        await entry["task"]

        assert failures == ["Py-Coder run cancelled: execution was not approved."]
        assert node.state.pycoder_awaiting_approval is False
        assert pycoder_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_pycoder_ai_driven_gate_discloses_the_current_runs_code_not_a_stale_value(monkeypatch):
    """Pins the human-approval gate's disclosure integrity (audit finding P1,
    doc/adr/AUDIT-2026-08-03-approval-guards-results.md): node.state.pycoder_code
    must reflect the CURRENT run's extracted code the instant the gate opens,
    before approval is even requested - never a stale value left over from a
    prior run. Deliberately independent of the fingerprint-based tests (e.g.
    test_pycoder_execution_blocked_when_approved_fingerprint_does_not_match):
    the fingerprint binds approval to the internal `current_code` local
    regardless of what is actually displayed, so a bug that silently stops
    refreshing the disclosed field would pass every fingerprint check while
    showing the human stale code to approve - a bait-and-switch on the
    reviewer, not a code-execution bypass, but a real breach of informed
    consent, which is the whole point of a human-approval gate."""
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "[TOOL:PYTHON]\nprint('fresh code')\n[/TOOL]",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")
        # Simulates exactly the failure mode this test exists to catch: if
        # the disclosure write at gate-open were ever silently dropped, this
        # stale value would still be here when the human is asked to approve.
        node.state.pycoder_code = "print('STALE FROM A PRIOR RUN')"

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="add 1+1", code="", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(pycoder_slots(dispatcher).items()))

        for _ in range(200):
            if node.state.pycoder_awaiting_approval or entry["task"].done():
                break
            await asyncio.sleep(0.005)
        assert node.state.pycoder_awaiting_approval is True, "must genuinely be parked on the approval gate"
        assert node.state.pycoder_code == "print('fresh code')", (
            "the disclosed code must be the CURRENT run's extracted code the instant "
            "the gate opens, not a stale value left over from a prior run"
        )

        assert dispatcher.deny_code_execution(request_id) is True
        await entry["task"]

    asyncio.run(run())


def test_pycoder_ai_driven_initial_gate_fingerprint_is_computed_over_the_disclosed_code(monkeypatch):
    """Hardens audit finding P2 (doc/adr/AUDIT-2026-08-03-approval-guards-results.md):
    the existing fingerprint-mismatch coverage
    (test_pycoder_execution_blocked_when_approved_fingerprint_does_not_match
    and its repair-loop siblings) is real but INDIRECT - those tests only
    catch a wrong-content fingerprint because a SEPARATE guard (the loop-top
    re-check, a defense-in-depth check independent of the gate-open write
    this test targets) happens to also depend on the same property and trips
    first. If that separate re-check were ever weakened in the same change
    as a bug here, this indirect coverage would silently disappear. This
    asserts directly, independent of any other guard, that node.state.
    pycoder_approved_fingerprint is exactly _fingerprint_changes({"code":
    <the disclosed code>}) the instant the gate opens - never executing any
    code, so no other check is ever in a position to also catch this."""
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "[TOOL:PYTHON]\nprint('exact code')\n[/TOOL]",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="add 1+1", code="", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(pycoder_slots(dispatcher).items()))

        for _ in range(200):
            if node.state.pycoder_awaiting_approval or entry["task"].done():
                break
            await asyncio.sleep(0.005)
        assert node.state.pycoder_awaiting_approval is True

        expected_fingerprint = agents_module._fingerprint_changes({"code": node.state.pycoder_code})
        assert node.state.pycoder_approved_fingerprint == expected_fingerprint, (
            "the approval fingerprint must be computed directly over the disclosed "
            "code, independent of any other guard that might happen to also verify this"
        )

        assert dispatcher.deny_code_execution(request_id) is True
        await entry["task"]

    asyncio.run(run())


def test_pycoder_ai_driven_approved_executes_successfully(monkeypatch):
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "[TOOL:PYTHON]\nprint(2)\n[/TOOL]",
    )
    fake_repl = _FakeRepl(script=[("2", False)])
    monkeypatch.setattr(AgentDispatcher, "get_pycoder_repl", lambda self, node_id, repl_id=None: fake_repl)
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "the answer is 2",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")
        successes = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="add 1+1", code="", conversation_history=[],
            on_success=lambda *a: successes.append(a), on_failure=lambda m: None,
        )
        request_id, entry = next(iter(pycoder_slots(dispatcher).items()))
        assert dispatcher.approve_code_execution(request_id) is True
        await entry["task"]

        assert successes == [("print(2)", "2", "the answer is 2", False)]
        assert node.state.pycoder_awaiting_approval is False
        assert fake_repl.calls == ["print(2)"]
        assert pycoder_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_pycoder_ai_driven_repair_loop_exhausts_retries_calls_on_success_with_last_run_failed_true(monkeypatch):
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "[TOOL:PYTHON]\nbroken\n[/TOOL]",
    )
    repair_calls = []
    monkeypatch.setattr(
        agents_module.PyCoderRepairAgent, "get_response",
        lambda self, code, error, is_final_attempt: repair_calls.append(1) or f"still broken v{len(repair_calls)}",
    )
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "explains the persistent failure",
    )
    fake_repl = _FakeRepl(script=[("err", True)])  # every execute() call fails
    monkeypatch.setattr(AgentDispatcher, "get_pycoder_repl", lambda self, node_id, repl_id=None: fake_repl)

    # ADR-002 P0 regression guard: every repaired variant must open its own
    # fresh gate - a local SimpleNamespace subclass (NOT a patch on the
    # shared builtin SimpleNamespace type) counts how many times
    # pycoder_awaiting_approval transitions to True, since its final value is
    # always False by the time the run completes and would hide a regression
    # back to "one gate, reused for every repair". ADR-002 stage 2.5 PR9b:
    # pycoder_awaiting_approval now lives on node.state (see
    # _make_pycoder_node's own docstring), so this wraps the STATE object,
    # not the node itself.
    class _CountingState(SimpleNamespace):
        def __setattr__(self, name, value):
            if name == "pycoder_awaiting_approval" and value is True:
                self.__dict__["gate_open_count"] = self.__dict__.get("gate_open_count", 0) + 1
            super().__setattr__(name, value)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")
        node.state = _CountingState(**vars(node.state), gate_open_count=0)
        successes = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="do something impossible", code="", conversation_history=[],
            on_success=lambda *a: successes.append(a), on_failure=lambda m: None,
        )
        request_id, entry = next(iter(pycoder_slots(dispatcher).items()))
        await _approve_every_gate_until_done(dispatcher, request_id, entry["task"])
        await entry["task"]

        assert len(successes) == 1, "an exhausted repair loop is still a completed run, never on_failure"
        code, output, analysis, last_run_failed = successes[0]
        assert last_run_failed is True
        assert analysis.startswith("**PROCESS FAILED**")
        assert len(fake_repl.calls) == 4, "max_retries=4, matching legacy exactly"
        assert repair_calls == [1, 1, 1], "3 repairs between the 4 execute attempts"
        assert node.state.gate_open_count == 4, (
            "ADR-002 P0: one gate for the original code, plus one fresh gate per repaired "
            "variant (3) - repaired code must never run under the original approval"
        )

    asyncio.run(run())


def test_pycoder_ai_driven_repair_gate_denied_stops_immediately_without_further_repairs(monkeypatch):
    """ADR-002 P0: denying a LATER gate (a repaired variant, not the
    original code) must stop the run with its own distinct message, and must
    not fall through to yet another repair attempt.

    The `await entry["task"]` below is deliberately bounded (see its own
    comment): this test's approve-then-deny handshake assumes each gate
    genuinely blocks, and a mutation-testing audit proved that if the
    FIRST gate ever stops blocking, the handshake slides out of sync and
    this test parks forever on a Future nothing will resolve - hanging the
    whole suite with no output, and masking the 5 other tests that DO
    correctly catch that same regression."""
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "[TOOL:PYTHON]\nbroken\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.PyCoderRepairAgent, "get_response",
        lambda self, code, error, is_final_attempt: "still broken",
    )
    fake_repl = _FakeRepl(script=[("err", True)])  # every execute() call fails
    monkeypatch.setattr(AgentDispatcher, "get_pycoder_repl", lambda self, node_id, repl_id=None: fake_repl)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")
        failures = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="do something impossible", code="", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        request_id, entry = next(iter(pycoder_slots(dispatcher).items()))

        # Approve the FIRST gate (the original code) only.
        for _ in range(200):
            if node.state.pycoder_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert dispatcher.approve_code_execution(request_id) is True

        # Wait for the SECOND gate (the repaired code) to open, then deny it.
        for _ in range(200):
            if len(fake_repl.calls) >= 1 and node.state.pycoder_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert node.state.pycoder_awaiting_approval is True, "the repaired variant must open its own gate"
        assert dispatcher.deny_code_execution(request_id) is True
        # Bounded, not a bare await - see this test's own docstring. A
        # regression that stops the first gate blocking desyncs the
        # handshake above and leaves this task parked on an unresolvable
        # Future; without this bound that is an infinite, output-less hang
        # rather than a clean, located failure.
        await asyncio.wait_for(entry["task"], timeout=10)

        assert failures == ["Py-Coder run cancelled: repaired code was not approved."]
        assert len(fake_repl.calls) == 1, "denying the repair gate must prevent the repaired code from ever running"
        assert node.state.pycoder_awaiting_approval is False
        assert pycoder_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_pycoder_ai_driven_repair_gate_discloses_the_repaired_code_not_the_original(monkeypatch):
    """Pins the repair gate's disclosure integrity (audit finding P4,
    doc/adr/AUDIT-2026-08-03-approval-guards-results.md): node.state.pycoder_code
    must be updated to the REPAIRED code the instant the repair gate opens,
    not left showing the original (pre-repair) code the human already saw at
    the first gate. Deliberately independent of the fingerprint/fresh-Future
    tests above (test_pycoder_ai_driven_repair_gate_denied_stops_immediately_
    without_further_repairs, test_pycoder_ai_driven_repair_loop_exhausts_
    retries_...): those bind approval to the internal `current_code` local
    regardless of what is actually displayed, so a bug that stops refreshing
    the disclosed field at the repair gate would pass every one of them
    while showing the human the WRONG code to approve - exactly the
    "approval is not bound to what was shown" gap the ADR-002 P0 security
    review named explicitly, just at the disclosure layer instead of the
    execution-binding layer those other tests already cover."""
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "[TOOL:PYTHON]\nbroken\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.PyCoderRepairAgent, "get_response",
        lambda self, code, error, is_final_attempt: "print('repaired')",
    )
    fake_repl = _FakeRepl(script=[("err", True)])  # every execute() call fails
    monkeypatch.setattr(AgentDispatcher, "get_pycoder_repl", lambda self, node_id, repl_id=None: fake_repl)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")
        failures = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="do something impossible", code="", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        request_id, entry = next(iter(pycoder_slots(dispatcher).items()))

        # Approve the FIRST gate (the original "broken" code) only.
        for _ in range(200):
            if node.state.pycoder_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert node.state.pycoder_code == "broken"
        assert dispatcher.approve_code_execution(request_id) is True

        # Wait for the SECOND gate (the repaired code) to open.
        for _ in range(200):
            if len(fake_repl.calls) >= 1 and node.state.pycoder_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert node.state.pycoder_awaiting_approval is True, "the repaired variant must open its own gate"
        assert node.state.pycoder_code == "print('repaired')", (
            "the repair gate's disclosure must show the REPAIRED code, not the "
            "original (already-seen) code from the first gate"
        )

        assert dispatcher.deny_code_execution(request_id) is True
        await entry["task"]

    asyncio.run(run())


def test_pycoder_execution_blocked_when_approved_fingerprint_does_not_match(monkeypatch):
    """ADR-002 P0 defense-in-depth: if node.state.pycoder_approved_fingerprint
    ever disagrees with the code about to execute (simulating a future bug
    that mutates current_code without opening a fresh gate), the run must
    fail loudly instead of silently executing unapproved content."""
    monkeypatch.setattr(
        agents_module.PyCoderExecutionAgent, "get_response",
        lambda self, history, prompt: "[TOOL:PYTHON]\nprint(1)\n[/TOOL]",
    )
    fake_repl = _FakeRepl(script=[("1", False)])
    monkeypatch.setattr(AgentDispatcher, "get_pycoder_repl", lambda self, node_id, repl_id=None: fake_repl)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="ai_driven")
        failures = []

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="add 1", code="", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        request_id, entry = next(iter(pycoder_slots(dispatcher).items()))
        for _ in range(200):
            if node.state.pycoder_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        # Tamper with the fingerprint the way a hypothetical future bug that
        # mutated the code without re-gating would leave it mismatched.
        node.state.pycoder_approved_fingerprint = "not-the-real-fingerprint"
        assert dispatcher.approve_code_execution(request_id) is True
        await entry["task"]

        assert failures == [
            "Py-Coder execution blocked: the approved code no longer matches what is about to run."
        ]
        assert fake_repl.calls == [], "execution must never happen once the fingerprint check fails"

    asyncio.run(run())


def test_pycoder_busy_node_refuses_immediately_without_creating_a_request_entry():
    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pending_request_id="already-busy")

        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="ai_driven", prompt="x", code="", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )

        assert pycoder_slots(dispatcher) == {}
        assert notifications.visible is True
        assert notifications.msg_type == "info"

    asyncio.run(run())


# -- Execution Sandbox ----------------------------------------------------------


def test_code_sandbox_both_blank_calls_on_failure():
    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()
        failures = []

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-1", prompt="   ", existing_code="   ",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        entry = next(iter(code_sandbox_slots(dispatcher).values()))
        await entry["task"]

        assert failures == ["Provide a task prompt or Python code before running the sandbox."]
        assert code_sandbox_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_code_sandbox_blank_prompt_with_existing_code_reuses_it_without_calling_generation_agent(monkeypatch):
    generation_calls = []
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: generation_calls.append(prompt) or "unused",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-1", prompt="  ", existing_code="print('reuse me')",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))

        # Give the pipeline a moment to reach the approval gate, then deny -
        # this test only cares that generation was skipped, not about a full
        # execute cycle.
        for _ in range(200):
            if node.state.code_sandbox_awaiting_approval or entry["task"].done():
                break
            await asyncio.sleep(0.005)
        assert node.state.code_sandbox_code == "print('reuse me')", (
            "the EXISTING code must be what is shown for approval, unmodified"
        )
        dispatcher.deny_code_execution(request_id)
        await entry["task"]

        assert generation_calls == [], "a blank prompt with existing code must never call the generation agent"

    asyncio.run(run())


def test_code_sandbox_nonblank_prompt_always_regenerates_even_with_existing_code(monkeypatch):
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint('regenerated')\n[/TOOL]",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-1", prompt="write something new", existing_code="print('stale code')",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        dispatcher.deny_code_execution(request_id)
        await entry["task"]

        assert node.state.code_sandbox_code == "print('regenerated')", (
            "a non-blank prompt must always regenerate, ignoring any existing code"
        )

    asyncio.run(run())


def test_code_sandbox_no_code_extracted_calls_on_success_with_placeholder_and_skips_approval(monkeypatch):
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "A direct answer, no code tool used.",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()
        successes = []

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-1", prompt="what is 1+1", existing_code="",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: successes.append(a), on_failure=lambda m: None,
        )
        entry = next(iter(code_sandbox_slots(dispatcher).values()))
        await entry["task"]

        assert successes == [(
            "# No Python code was generated for this request.",
            "[Sandbox was not executed]",
            "A direct answer, no code tool used.",
        )]
        assert node.state.code_sandbox_awaiting_approval is False

    asyncio.run(run())


def test_code_sandbox_denied_approval_calls_on_failure_with_the_exact_legacy_message(monkeypatch):
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint(1)\n[/TOOL]",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()
        failures = []

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-1", prompt="do x", existing_code="",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        assert dispatcher.deny_code_execution(request_id) is True
        await entry["task"]

        assert failures == ["Sandbox run cancelled: execution was not approved."]
        assert node.state.code_sandbox_awaiting_approval is False
        assert code_sandbox_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_code_sandbox_approved_full_success_flow_runs_venv_and_analyzes(monkeypatch):
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint('ok')\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "sandbox ran fine",
    )

    class _FakeSandbox:
        def __init__(self, sandbox_id):
            self.sandbox_id = sandbox_id
            self.prep_calls = []

        def ensure_base_environment(self, should_continue, emit_line=None):
            self.prep_calls.append("ensure_base_environment")

        def sync_requirements(self, manifest, should_continue, emit_line=None, allow_source_builds=False):
            self.prep_calls.append(("sync_requirements", manifest))

        def execute_code(self, code, should_continue, emit_line=None):
            return f"ran: {code}", 0

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _FakeSandbox)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()
        successes = []

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="print ok", existing_code="",
            requirements_manifest="numpy", conversation_history=[],
            on_success=lambda *a: successes.append(a), on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        dispatcher.approve_code_execution(request_id)
        await entry["task"]

        assert len(successes) == 1
        code, output, analysis = successes[0]
        assert code == "print('ok')"
        assert output == "ran: print('ok')"
        assert analysis == "sandbox ran fine"
        assert node.state.code_sandbox_awaiting_approval is False
        assert code_sandbox_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def _make_recording_sandbox_class():
    """ADR-005 stage 5.5: a _FakeSandbox twin that records exactly what
    allow_source_builds value sync_requirements was called with, for the
    source-build escalation tests below."""

    class _RecordingSandbox:
        def __init__(self, sandbox_id):
            self.sandbox_id = sandbox_id
            self.sync_requirements_calls = []
            self.on_ensure_base_environment = None

        def ensure_base_environment(self, should_continue, emit_line=None):
            if self.on_ensure_base_environment is not None:
                self.on_ensure_base_environment()

        def sync_requirements(self, manifest, should_continue, emit_line=None, allow_source_builds=False):
            self.sync_requirements_calls.append(allow_source_builds)

        def execute_code(self, code, should_continue, emit_line=None):
            return "ran", 0

    return _RecordingSandbox


def test_code_sandbox_source_builds_defaults_to_false_when_never_toggled(monkeypatch):
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint('ok')\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "ok",
    )
    sandbox_holder = {}
    recording_class = _make_recording_sandbox_class()

    def _make_sandbox(sandbox_id):
        sandbox = recording_class(sandbox_id)
        sandbox_holder["sandbox"] = sandbox
        return sandbox

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _make_sandbox)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="print ok", existing_code="",
            requirements_manifest="numpy", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        dispatcher.approve_code_execution(request_id)
        await entry["task"]

        assert sandbox_holder["sandbox"].sync_requirements_calls == [False]

    asyncio.run(run())


def test_code_sandbox_source_builds_true_is_threaded_through_to_sync_requirements_when_approved(monkeypatch):
    # ADR-005 stage 5.5: the checkbox is set WHILE the panel is open, i.e.
    # any time before Approve is clicked - simulated here by setting the
    # state field directly before calling approve_code_execution, exactly
    # like a real setCodeSandboxAllowSourceBuilds intent arriving during
    # that window would.
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint('ok')\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "ok",
    )
    sandbox_holder = {}
    recording_class = _make_recording_sandbox_class()

    def _make_sandbox(sandbox_id):
        sandbox = recording_class(sandbox_id)
        sandbox_holder["sandbox"] = sandbox
        return sandbox

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _make_sandbox)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="print ok", existing_code="",
            requirements_manifest="some-source-only-pkg", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        # Wait for the gate to actually be open before toggling - agents.py
        # resets the field to False the instant the gate opens (see the
        # reset test above), so setting it before that reset has run would
        # just get clobbered, same as a real checkbox click before the
        # panel has even rendered could never happen.
        for _ in range(200):
            if node.state.code_sandbox_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert node.state.code_sandbox_awaiting_approval is True
        node.state.code_sandbox_approval_allow_source_builds = True
        dispatcher.approve_code_execution(request_id)
        await entry["task"]

        assert sandbox_holder["sandbox"].sync_requirements_calls == [True]

    asyncio.run(run())


def test_code_sandbox_source_builds_flag_is_reset_the_instant_a_new_gate_opens(monkeypatch):
    # A stale True left over from some earlier interaction must never
    # silently carry into a run the user has not actually reviewed yet.
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint('ok')\n[/TOOL]",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node(code_sandbox_approval_allow_source_builds=True)

        # start_code_sandbox_run itself returns quickly (it creates and
        # attaches _run() as a background task rather than awaiting it) -
        # same pattern every other test in this module relies on.
        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="print ok", existing_code="",
            requirements_manifest="numpy", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        # Yield until the gate has actually opened (awaiting_approval flips
        # True) before asserting the reset - the background task needs a
        # tick to run past the generation-agent await.
        for _ in range(200):
            if node.state.code_sandbox_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert node.state.code_sandbox_awaiting_approval is True
        assert node.state.code_sandbox_approval_allow_source_builds is False

        dispatcher.deny_code_execution(request_id)
        await entry["task"]

    asyncio.run(run())


def test_code_sandbox_source_builds_checked_then_denied_does_not_leak_into_the_next_run(monkeypatch):
    # ADR-005 stage 5.5 test-coverage-gap fix: the reset test above only
    # proves the GATE-OPEN reset (agents.py's own unconditional reset at
    # the top of every gate) - it never exercises the POST-APPROVAL-RESOLVE
    # clear (the line right after `approved = await approval_future`,
    # which runs whether approved is True or False) via a real check-then-
    # deny sequence. A future refactor that moved that clear inside an
    # `if approved:` branch would not be caught by the gate-open-reset test
    # alone, since that reset happens to independently cover the same
    # user-visible symptom on the NEXT run - this test exercises the
    # actual deny path directly.
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint('ok')\n[/TOOL]",
    )

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="print ok", existing_code="",
            requirements_manifest="numpy", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        for _ in range(200):
            if node.state.code_sandbox_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert node.state.code_sandbox_awaiting_approval is True

        # User checks the box, then denies (changes their mind).
        node.state.code_sandbox_approval_allow_source_builds = True
        dispatcher.deny_code_execution(request_id)
        await entry["task"]

        assert node.state.code_sandbox_approval_allow_source_builds is False, (
            "denying a checked approval must clear the field - a checked-"
            "then-denied run must never leave a stale True for whatever "
            "runs on this node next"
        )

    asyncio.run(run())


def test_code_sandbox_source_builds_toggle_after_approval_resolves_does_not_affect_the_in_flight_run(monkeypatch):
    # ADR-005 stage 5.5 race-safety proof: agents.py captures allow_source_
    # builds into a local variable the instant the approval future
    # resolves, BEFORE the ensure_base_environment/sync_requirements awaits
    # that follow - a setCodeSandboxAllowSourceBuilds intent arriving in
    # that window (simulated here via ensure_base_environment's own hook,
    # the one await between approval and the read this test targets) must
    # not change what an already-decided run does.
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint('ok')\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "ok",
    )
    sandbox_holder = {}
    recording_class = _make_recording_sandbox_class()
    node_holder = {}

    def _make_sandbox(sandbox_id):
        sandbox = recording_class(sandbox_id)
        sandbox.on_ensure_base_environment = (
            lambda: setattr(node_holder["node"].state, "code_sandbox_approval_allow_source_builds", True)
        )
        sandbox_holder["sandbox"] = sandbox
        return sandbox

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _make_sandbox)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()
        node_holder["node"] = node

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="print ok", existing_code="",
            requirements_manifest="numpy", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        # Approved with the checkbox still unchecked (default False).
        dispatcher.approve_code_execution(request_id)
        await entry["task"]

        # The late toggle (fired from inside ensure_base_environment, after
        # approval resolved) must NOT have reached sync_requirements.
        assert sandbox_holder["sandbox"].sync_requirements_calls == [False]
        # The state field itself did change (proving the toggle really
        # fired) - what's under test is that the ALREADY-RUNNING call used
        # the frozen local, not this now-True live value.
        assert node.state.code_sandbox_approval_allow_source_builds is True

    asyncio.run(run())


def test_code_sandbox_source_builds_snapshotted_atomically_at_approve_time_not_at_task_resume_time(monkeypatch):
    # ADR-005 stage 5.5 review-fix: a 4-lens adversarial review found that
    # capturing allow_source_builds AFTER `await approval_future` resolves
    # (the previous version of this mechanism) is not actually race-free -
    # future.set_result() only SCHEDULES the _run() task's resumption
    # rather than running it inline, so a second WS connection could fully
    # process a setCodeSandboxAllowSourceBuilds intent in that scheduling
    # gap. The fix: _resolve_approval snapshots the field into
    # RunHandle.approval_snapshot SYNCHRONOUSLY, in the same
    # uninterruptible stretch as future.set_result() itself - see that
    # field's own doc (backend/run_lifecycle.py).
    #
    # This test proves the fix precisely: it mutates the field back to a
    # DIFFERENT value immediately after calling approve_code_execution,
    # still on the same synchronous stretch of test code (no `await` in
    # between) - i.e. strictly BEFORE the _run() task could possibly get a
    # turn on the event loop to resume and read anything. If the
    # implementation still re-read node.state after resuming (the old,
    # race-prone behavior), it would see this post-approve mutation and
    # install with the WRONG value; reading the snapshotted value instead
    # proves the capture genuinely happened at approve-time.
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint('ok')\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "ok",
    )
    sandbox_holder = {}
    recording_class = _make_recording_sandbox_class()

    def _make_sandbox(sandbox_id):
        sandbox = recording_class(sandbox_id)
        sandbox_holder["sandbox"] = sandbox
        return sandbox

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _make_sandbox)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="print ok", existing_code="",
            requirements_manifest="some-source-only-pkg", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        for _ in range(200):
            if node.state.code_sandbox_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert node.state.code_sandbox_awaiting_approval is True

        # The checkbox is checked when Approve fires...
        node.state.code_sandbox_approval_allow_source_builds = True
        dispatcher.approve_code_execution(request_id)
        # ...then immediately, synchronously, "unchecked" again - simulating
        # a competing setCodeSandboxAllowSourceBuilds(False) landing in the
        # scheduling gap before _run() has resumed. No `await` happens
        # between the approve call and this line.
        node.state.code_sandbox_approval_allow_source_builds = False

        await entry["task"]

        assert sandbox_holder["sandbox"].sync_requirements_calls == [True], (
            "sync_requirements must install with the value snapshotted "
            "AT APPROVE TIME (True), not whatever node.state held once the "
            "_run() task actually got a turn to resume"
        )

    asyncio.run(run())


def test_code_sandbox_repair_gate_resets_allow_source_builds_and_marks_is_repair(monkeypatch):
    # ADR-005 stage 5.5 review-fix: an earlier version of the repair-loop
    # re-gate left code_sandbox_approval_allow_source_builds untouched,
    # reasoning it was "still False from the initial gate's own clear" -
    # that assumption was never enforced (setCodeSandboxAllowSourceBuilds
    # is ungated and could set it True during the execute/repair window,
    # which is never awaiting_approval), so a repair round's checkbox could
    # render CHECKED without the user having touched it that round. The fix
    # resets it explicitly on every repair re-gate too, and adds
    # code_sandbox_approval_is_repair so the frontend can hide the
    # (functionally inert on repair rounds) checkbox entirely.
    #
    # Uses a threading.Event-blocked repair-agent call (mirroring test_
    # canvas.py's own _blocking_get_response race tests) rather than
    # wall-clock polling for the intermediate state - every mocked agent
    # call here resolves fast enough that a plain asyncio.sleep(0.005) poll
    # loop cannot reliably land inside the narrow execute/repair window
    # before the repair gate has already reopened, as this test's own
    # first draft (proven by watching it race past the intermediate state
    # in both directions) discovered empirically.
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nbroken\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "explains the failure",
    )

    entered_repair = threading.Event()
    release_repair = threading.Event()

    def _blocking_repair(self, code, error, manifest, original_prompt=None):
        entered_repair.set()
        release_repair.wait(timeout=5)
        return "still broken"

    monkeypatch.setattr(agents_module.SandboxRepairAgent, "get_response", _blocking_repair)

    node_holder = {}

    class _FailingSandbox:
        def __init__(self, sandbox_id):
            self.sandbox_id = sandbox_id

        def ensure_base_environment(self, should_continue, emit_line=None):
            pass

        def sync_requirements(self, manifest, should_continue, emit_line=None, allow_source_builds=False):
            # sync_requirements only ever runs ONCE, before the repair loop
            # starts - simulates a setCodeSandboxAllowSourceBuilds(True)
            # intent landing during the execute/repair window, which is
            # never awaiting_approval so nothing rejects it (ungated,
            # matching the sibling setCodeSandboxRequirements intent's own
            # posture).
            node_holder["node"].state.code_sandbox_approval_allow_source_builds = True

        def execute_code(self, code, should_continue, emit_line=None):
            return "Traceback (most recent call last):\nboom", 1

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _FailingSandbox)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()
        node_holder["node"] = node

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="do something impossible", existing_code="",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))

        # Initial gate: verify is_repair is False, then approve.
        for _ in range(200):
            if node.state.code_sandbox_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert node.state.code_sandbox_awaiting_approval is True
        assert node.state.code_sandbox_approval_is_repair is False
        dispatcher.approve_code_execution(request_id)

        # Wait until the repair agent is genuinely blocked mid-call - proves
        # sync_requirements already ran (the stray True landed) and
        # execute_code already failed, but the repair gate has not yet
        # reopened.
        for _ in range(200):
            if entered_repair.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered_repair.is_set(), "the repair agent call never actually started"
        assert node.state.code_sandbox_approval_allow_source_builds is True, (
            "sanity check: the stray toggle landed during the execute/repair window"
        )
        assert node.state.code_sandbox_awaiting_approval is False

        release_repair.set()

        for _ in range(200):
            if node.state.code_sandbox_awaiting_approval or entry["task"].done():
                break
            await asyncio.sleep(0.005)
        assert node.state.code_sandbox_awaiting_approval is True, "must be parked on the repair gate"
        assert node.state.code_sandbox_approval_is_repair is True
        assert node.state.code_sandbox_approval_allow_source_builds is False, (
            "the repair re-gate must reset the stray True, not render the "
            "repair panel's checkbox as checked without user action this round"
        )

        dispatcher.deny_code_execution(request_id)
        await entry["task"]

    asyncio.run(run())


def test_code_sandbox_repair_loop_requires_a_fresh_approval_for_each_repaired_attempt(monkeypatch):
    """ADR-002 P0: the code_sandbox twin of the pycoder repair-gate test
    above - same confirmed gap, same fix, same shape."""
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nbroken\n[/TOOL]",
    )
    repair_calls = []
    monkeypatch.setattr(
        agents_module.SandboxRepairAgent, "get_response",
        lambda self, code, error, manifest, original_prompt=None: repair_calls.append(1) or "still broken",
    )
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "explains the persistent failure",
    )

    class _FailingSandbox:
        def __init__(self, sandbox_id):
            self.sandbox_id = sandbox_id
            self.execute_calls = []

        def ensure_base_environment(self, should_continue, emit_line=None):
            pass

        def sync_requirements(self, manifest, should_continue, emit_line=None, allow_source_builds=False):
            pass

        def execute_code(self, code, should_continue, emit_line=None):
            self.execute_calls.append(code)
            return "Traceback (most recent call last):\nboom", 1

    fake_sandbox_holder = {}

    def _make_sandbox(sandbox_id):
        sandbox = _FailingSandbox(sandbox_id)
        fake_sandbox_holder["sandbox"] = sandbox
        return sandbox

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _make_sandbox)

    # ADR-002 stage 2.5 PR10b: code_sandbox_awaiting_approval now lives on
    # node.state (see _make_code_sandbox_node's own docstring), so this
    # wraps the STATE object, not the node itself - mirroring the identical
    # rewiring already done for pycoder's own gate counter above.
    class _CountingState(SimpleNamespace):
        def __setattr__(self, name, value):
            if name == "code_sandbox_awaiting_approval" and value is True:
                self.__dict__["gate_open_count"] = self.__dict__.get("gate_open_count", 0) + 1
            super().__setattr__(name, value)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()
        node.state = _CountingState(**vars(node.state), gate_open_count=0)
        successes = []

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="do something impossible", existing_code="",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: successes.append(a), on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        await _approve_every_gate_until_done(dispatcher, request_id, entry["task"])
        await entry["task"]

        assert len(successes) == 1, "an exhausted repair loop is still a completed run, never on_failure"
        sandbox = fake_sandbox_holder["sandbox"]
        assert len(sandbox.execute_calls) == 3, "max_attempts=3, matching the existing sandbox behavior"
        assert repair_calls == [1, 1], "2 repairs between the 3 execute attempts"
        assert node.state.gate_open_count == 3, (
            "ADR-002 P0: one gate for the original code, plus one fresh gate per repaired "
            "variant (2) - repaired code must never run under the original approval"
        )

    asyncio.run(run())


def test_code_sandbox_repair_gate_denied_stops_immediately_without_further_repairs(monkeypatch):
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nbroken\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.SandboxRepairAgent, "get_response",
        lambda self, code, error, manifest, original_prompt=None: "still broken",
    )

    class _FailingSandbox:
        def __init__(self, sandbox_id):
            self.execute_calls = []

        def ensure_base_environment(self, should_continue, emit_line=None):
            pass

        def sync_requirements(self, manifest, should_continue, emit_line=None, allow_source_builds=False):
            pass

        def execute_code(self, code, should_continue, emit_line=None):
            self.execute_calls.append(code)
            return "Traceback (most recent call last):\nboom", 1

    fake_sandbox_holder = {}

    def _make_sandbox(sandbox_id):
        sandbox = _FailingSandbox(sandbox_id)
        fake_sandbox_holder["sandbox"] = sandbox
        return sandbox

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _make_sandbox)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()
        failures = []

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="do something impossible", existing_code="",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))

        for _ in range(200):
            if node.state.code_sandbox_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert dispatcher.approve_code_execution(request_id) is True

        for _ in range(200):
            if "sandbox" in fake_sandbox_holder:
                break
            await asyncio.sleep(0.005)
        sandbox = fake_sandbox_holder["sandbox"]
        for _ in range(200):
            if len(sandbox.execute_calls) >= 1 and node.state.code_sandbox_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        assert node.state.code_sandbox_awaiting_approval is True, "the repaired variant must open its own gate"
        assert dispatcher.deny_code_execution(request_id) is True
        await entry["task"]

        assert failures == ["Sandbox run cancelled: repaired code was not approved."]
        assert len(sandbox.execute_calls) == 1, (
            "denying the repair gate must prevent the repaired code from ever running"
        )
        assert node.state.code_sandbox_awaiting_approval is False
        assert code_sandbox_slots(dispatcher) == {}
        assert node.pending_request_id is None

    asyncio.run(run())


def test_code_sandbox_execution_blocked_when_approved_fingerprint_does_not_match(monkeypatch):
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint(1)\n[/TOOL]",
    )

    class _FakeSandbox:
        def __init__(self, sandbox_id):
            self.execute_calls = []

        def ensure_base_environment(self, should_continue, emit_line=None):
            pass

        def sync_requirements(self, manifest, should_continue, emit_line=None, allow_source_builds=False):
            pass

        def execute_code(self, code, should_continue, emit_line=None):
            self.execute_calls.append(code)
            return "1", 0

    fake_sandbox_holder = {}

    def _make_sandbox(sandbox_id):
        sandbox = _FakeSandbox(sandbox_id)
        fake_sandbox_holder["sandbox"] = sandbox
        return sandbox

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _make_sandbox)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node()
        failures = []

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="print 1", existing_code="",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        for _ in range(200):
            if node.state.code_sandbox_awaiting_approval:
                break
            await asyncio.sleep(0.005)
        node.state.code_sandbox_approved_fingerprint = "not-the-real-fingerprint"
        assert dispatcher.approve_code_execution(request_id) is True
        await entry["task"]

        assert failures == [
            "Sandbox execution blocked: the approved code no longer matches what is about to run."
        ]
        assert fake_sandbox_holder["sandbox"].execute_calls == [], (
            "execution must never happen once the fingerprint check fails"
        )

    asyncio.run(run())


def test_code_sandbox_run_streams_live_output_lines_in_order_with_a_final_done_frame(monkeypatch):
    """R5.4 post-review FIX 1: execute_code's emit_line callback must reach
    the WS layer via bus.publish_stream, in order, addressed to this run's
    own request_id, ending with an unconditional final done=True frame -
    same recorder/assertion shape test_streaming_happy_path_... uses for the
    chat token-streaming pump above (_StreamRecorderConnection is a plain,
    reusable Connection double, not chat-specific)."""
    monkeypatch.setattr(
        agents_module.SandboxGenerationAgent, "get_response",
        lambda self, history, prompt, manifest: "[TOOL:PYTHON]\nprint('ok')\n[/TOOL]",
    )
    monkeypatch.setattr(
        agents_module.PyCoderAnalysisAgent, "get_response",
        lambda self, original_prompt, code, code_output: "sandbox ran fine",
    )

    class _FakeSandbox:
        def __init__(self, sandbox_id):
            self.sandbox_id = sandbox_id

        def ensure_base_environment(self, should_continue, emit_line=None):
            if emit_line:
                emit_line("[Sandbox] Creating virtual environment...\n")

        def sync_requirements(self, manifest, should_continue, emit_line=None, allow_source_builds=False):
            if emit_line:
                emit_line("[Sandbox] No extra dependencies requested.\n")

        def execute_code(self, code, should_continue, emit_line=None):
            if emit_line:
                emit_line("line one\n")
                emit_line("line two\n")
            return "line one\nline two", 0

    monkeypatch.setattr(agents_module, "VirtualEnvSandbox", _FakeSandbox)

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        recorder = _StreamRecorderConnection()
        bus.attach(recorder)
        node = _make_code_sandbox_node()
        successes = []

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-42", prompt="print ok", existing_code="",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: successes.append(a), on_failure=lambda m: None,
        )
        request_id, entry = next(iter(code_sandbox_slots(dispatcher).items()))
        dispatcher.approve_code_execution(request_id)
        await entry["task"]

        assert len(successes) == 1, "the run must still complete normally alongside the new streaming side-channel"

        assert recorder.frames, "must have received at least one stream frame"
        assert recorder.frames[-1]["done"] is True
        assert recorder.frames[-1]["delta"] == ""
        non_final = [f for f in recorder.frames if not f["done"]]
        assert [f["delta"] for f in non_final] == [
            "[Sandbox] Creating virtual environment...\n",
            "[Sandbox] No extra dependencies requested.\n",
            "line one\n",
            "line two\n",
        ], "one publish_stream call per emit_line call, in order - no batching"
        assert all(f["topic"] == "scene" for f in recorder.frames)
        assert all(f["requestId"] == request_id for f in recorder.frames), (
            "every frame must be addressed to THIS run's own request_id"
        )
        seqs = [f["seq"] for f in recorder.frames]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "seq must be strictly increasing"

    asyncio.run(run())


def test_code_sandbox_busy_node_refuses_immediately_without_creating_a_request_entry():
    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_code_sandbox_node(pending_request_id="already-busy")

        await dispatcher.start_code_sandbox_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            sandbox_id="sandbox-1", prompt="x", existing_code="",
            requirements_manifest="", conversation_history=[],
            on_success=lambda *a: None, on_failure=lambda m: None,
        )

        assert code_sandbox_slots(dispatcher) == {}
        assert notifications.visible is True
        assert notifications.msg_type == "info"

    asyncio.run(run())


def test_is_sandbox_error_output_detects_nonzero_return_code_and_keywords():
    assert agents_module._is_sandbox_error_output("all good", 1) is True
    assert agents_module._is_sandbox_error_output("Traceback (most recent call last):", 0) is True
    assert agents_module._is_sandbox_error_output("ModuleNotFoundError: no module", 0) is True
    assert agents_module._is_sandbox_error_output("all good, no errors here", 0) is False


# -- shared approve/deny + cancel + disconnect auto-deny mechanics ------------


def test_cancel_pycoder_sets_cancel_event_and_resolves_the_approval_future_false():
    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        cancel_event = threading.Event()
        future = asyncio.get_running_loop().create_future()
        handle = dispatcher._runs.claim("pycoder", cancel_event=cancel_event, approval_future=future)

        assert dispatcher.cancel_pycoder(handle.request_id) is True
        assert cancel_event.is_set() is True
        assert future.done() is True
        assert future.result() is False

    asyncio.run(run())


def test_cancel_pycoder_returns_false_for_an_unknown_request_id():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    assert dispatcher.cancel_pycoder("no-such-request") is False


def test_cancel_pycoder_and_cancel_code_sandbox_cannot_trip_each_others_or_a_foreign_kinds_request_id():
    """ADR-002 stage 2.4g: cancel_pycoder/cancel_code_sandbox both need an
    explicit kind check (unlike _resolve_approval, whose approval_future
    is None discriminator already handles this) because they unconditionally
    call handle.cancel_event.set() - a foreign kind's handle could have
    cancel_event=None (e.g. chart) and AttributeError, or - worse - a
    request_id belonging to a DIFFERENT cancel_event-bearing kind (chat,
    artifact, gitlink_run, or the OTHER of this pair) could silently trip
    that unrelated run's cancellation instead of being rejected."""
    async def run():
        dispatcher = AgentDispatcher(_FakeSettingsManager())
        loop = asyncio.get_running_loop()
        chat_handle = dispatcher._runs.claim("chat", cancel_event=threading.Event())
        pycoder_handle = dispatcher._runs.claim(
            "pycoder", cancel_event=threading.Event(), approval_future=loop.create_future()
        )
        sandbox_handle = dispatcher._runs.claim(
            "code_sandbox", cancel_event=threading.Event(), approval_future=loop.create_future()
        )

        assert dispatcher.cancel_pycoder(chat_handle.request_id) is False
        assert not chat_handle.cancel_event.is_set()

        assert dispatcher.cancel_pycoder(sandbox_handle.request_id) is False
        assert not sandbox_handle.cancel_event.is_set()

        assert dispatcher.cancel_code_sandbox(pycoder_handle.request_id) is False
        assert not pycoder_handle.cancel_event.is_set()

        # Sanity: each method DOES accept its own matching kind.
        assert dispatcher.cancel_pycoder(pycoder_handle.request_id) is True
        assert pycoder_handle.cancel_event.is_set()
        assert dispatcher.cancel_code_sandbox(sandbox_handle.request_id) is True
        assert sandbox_handle.cancel_event.is_set()

    asyncio.run(run())


def test_cancel_all_trips_a_pycoder_run_that_is_mid_execution_past_the_approval_gate(monkeypatch):
    """ADR-002 stage 2.4g: proves the NEW capability this migration slice
    unlocks - previously pycoder's cancel_event lived in its own private
    dict that cancel_all() never walked at all, so a session disconnect
    mid-EXECUTION (already past any approval gate, blocked in the REPL)
    left the run running server-side, untethered, until it finished on
    its own. Now that pycoder shares self._runs with every other
    cancellable kind, cancel_all() - the same method backend/app.py's
    disconnect handler calls first, before cancel_all_pending_approvals()
    - genuinely reaches it too. Uses manual mode deliberately: it has no
    approval gate at all, so this isolates the EXECUTE-stage cancellation
    specifically from the (already separately tested) approval-pause
    auto-deny path."""
    started = threading.Event()
    release = threading.Event()

    class _BlockingRepl:
        last_run_failed = False

        def execute(self, code):
            started.set()
            release.wait(5)
            return "output"

    monkeypatch.setattr(AgentDispatcher, "get_pycoder_repl", lambda self, node_id, repl_id=None: _BlockingRepl())

    async def run():
        bus, notifications, dispatcher = _make_code_exec_env()
        node = _make_pycoder_node(pycoder_mode="manual")
        failures = []

        # start_pycoder_run is itself fire-and-forget - it schedules _run()
        # internally and returns almost immediately, well before execution
        # reaches the REPL. Await it directly (not wrapped in create_task);
        # the REAL background work is entry["task"] below, grabbed only
        # after this returns and the registry claim has landed.
        await dispatcher.start_pycoder_run(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            mode="manual", prompt="", code="print(1)", conversation_history=[],
            on_success=lambda *a: None, on_failure=failures.append,
        )
        entry = next(iter(pycoder_slots(dispatcher).values()))
        await asyncio.to_thread(started.wait, 5)

        # THE key action: cancel_all() - exactly what backend/app.py's
        # disconnect handler calls - must trip this run's cancel_event even
        # though it is mid-execution, with no approval gate involved at all.
        dispatcher.cancel_all()

        release.set()
        await entry["task"]

        assert notifications.visible is True
        assert notifications.message == "Py-Coder execution cancelled."

    asyncio.run(run())


def test_cancel_code_sandbox_returns_false_for_an_unknown_request_id():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    assert dispatcher.cancel_code_sandbox("no-such-request") is False


def test_approve_code_execution_resolves_a_pending_pycoder_future():
    async def run():
        dispatcher = AgentDispatcher(_FakeSettingsManager())
        future = asyncio.get_running_loop().create_future()
        handle = dispatcher._runs.claim("pycoder", approval_future=future)

        assert dispatcher.approve_code_execution(handle.request_id) is True
        assert future.result() is True

    asyncio.run(run())


def test_deny_code_execution_resolves_a_pending_code_sandbox_future():
    async def run():
        dispatcher = AgentDispatcher(_FakeSettingsManager())
        future = asyncio.get_running_loop().create_future()
        handle = dispatcher._runs.claim("code_sandbox", approval_future=future)

        assert dispatcher.deny_code_execution(handle.request_id) is True
        assert future.result() is False

    asyncio.run(run())


def test_approve_code_execution_returns_false_for_an_unknown_request_id():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    assert dispatcher.approve_code_execution("no-such-request") is False


def test_resolve_approval_is_idempotent_and_never_raises_on_a_stale_duplicate_call():
    """LOAD-BEARING guard: a duplicate/stale approve-or-deny (e.g. a
    double-click, or a message arriving after cancel already resolved this
    same future) must never raise asyncio.InvalidStateError."""
    async def run():
        dispatcher = AgentDispatcher(_FakeSettingsManager())
        future = asyncio.get_running_loop().create_future()
        handle = dispatcher._runs.claim("pycoder", approval_future=future)

        assert dispatcher.approve_code_execution(handle.request_id) is True
        assert future.result() is True
        # A second, stale call for the SAME request must not raise, and must
        # not flip the already-resolved value.
        assert dispatcher.deny_code_execution(handle.request_id) is True
        assert future.result() is True, "the first resolution wins - a stale deny must not clobber it"

    asyncio.run(run())


def test_cancel_all_pending_approvals_auto_denies_every_undone_future_in_both_dicts():
    async def run():
        dispatcher = AgentDispatcher(_FakeSettingsManager())
        pycoder_future = asyncio.get_running_loop().create_future()
        sandbox_future = asyncio.get_running_loop().create_future()
        already_resolved_future = asyncio.get_running_loop().create_future()
        already_resolved_future.set_result(True)

        dispatcher._runs.claim("pycoder", cancel_event=threading.Event(), approval_future=pycoder_future)
        dispatcher._runs.claim("code_sandbox", cancel_event=threading.Event(), approval_future=sandbox_future)
        dispatcher._runs.claim(
            "code_sandbox", cancel_event=threading.Event(), approval_future=already_resolved_future
        )

        dispatcher.cancel_all_pending_approvals()

        assert pycoder_future.result() is False
        assert sandbox_future.result() is False
        assert already_resolved_future.result() is True, (
            "an already-resolved future must never be clobbered by the auto-deny sweep"
        )

    asyncio.run(run())


def test_get_pycoder_repl_returns_the_same_instance_for_the_same_node_id():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    first = dispatcher.get_pycoder_repl("n1", "repl-1")
    second = dispatcher.get_pycoder_repl("n1", "repl-1")
    assert first is second
    assert dispatcher.get_pycoder_repl("n2", "repl-2") is not first


def test_get_pycoder_repl_is_keyed_by_node_id_not_repl_id():
    # ADR-005 stage 5.3 (review-fix): the in-memory dict lookup is
    # node_id-keyed (session-scoped, safe to reuse - see get_pycoder_repl's
    # own docstring); repl_id only affects the constructed PythonREPL's
    # on-disk cwd, not which dict entry is returned. A lookup on the same
    # node_id must reuse the SAME live REPL even if a caller (incorrectly,
    # or via some future bug) passed a different repl_id the second time -
    # the repl_id argument on that second call must simply be ignored, not
    # cause a second REPL to be created.
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    first = dispatcher.get_pycoder_repl("n1", "repl-1")
    second = dispatcher.get_pycoder_repl("n1", "some-other-repl-id")
    assert first is second
    assert first.cwd.name == "repl-1"


def test_dispose_pycoder_repl_tolerates_a_missing_node_id_silently():
    async def run():
        dispatcher = AgentDispatcher(_FakeSettingsManager())
        await dispatcher.dispose_pycoder_repl("never-created")  # must not raise

    asyncio.run(run())


def test_dispose_pycoder_repl_stops_and_removes_the_repl():
    class _StoppableRepl:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    async def run():
        dispatcher = AgentDispatcher(_FakeSettingsManager())
        fake_repl = _StoppableRepl()
        dispatcher._pycoder_repls["n1"] = fake_repl

        await dispatcher.dispose_pycoder_repl("n1")

        assert fake_repl.stopped is True
        assert "n1" not in dispatcher._pycoder_repls

    asyncio.run(run())


def test_dispose_all_pycoder_repls_stops_every_tracked_repl_and_clears_the_dict():
    class _StoppableRepl:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    dispatcher = AgentDispatcher(_FakeSettingsManager())
    repl_a = _StoppableRepl()
    repl_b = _StoppableRepl()
    dispatcher._pycoder_repls["n1"] = repl_a
    dispatcher._pycoder_repls["n2"] = repl_b

    dispatcher.dispose_all_pycoder_repls()

    assert dispatcher._pycoder_repls == {}
    # stop() runs on a background thread (see the method's own docstring
    # for why) - give it a moment to actually land before asserting.
    for _ in range(50):
        if repl_a.stopped and repl_b.stopped:
            break
        time.sleep(0.02)
    assert repl_a.stopped is True
    assert repl_b.stopped is True


def test_dispose_all_pycoder_repls_does_not_block_the_caller_on_a_slow_stop():
    # ADR-005 stage 5.3 (review-fix): its one real caller
    # (_evict_idle_session) runs on the live asyncio event loop - a
    # blocking inline repl.stop() would stall every other connected
    # client's WS/HTTP handling for however long the OS takes to kill and
    # reap the process. Proven here with a repl.stop() that would take
    # noticeably longer than "instant" if called inline.
    class _SlowRepl:
        def __init__(self):
            self.stopped = threading.Event()

        def stop(self):
            time.sleep(0.5)
            self.stopped.set()

    dispatcher = AgentDispatcher(_FakeSettingsManager())
    slow_repl = _SlowRepl()
    dispatcher._pycoder_repls["n1"] = slow_repl

    start = time.monotonic()
    dispatcher.dispose_all_pycoder_repls()
    elapsed = time.monotonic() - start

    assert elapsed < 0.2, "must return almost immediately, not block on the slow stop()"
    assert slow_repl.stopped.wait(timeout=2.0), "the background thread must still actually call stop()"


def test_remove_code_sandbox_scratch_dir_refuses_a_blank_sandbox_id():
    # ADR-005 stage 5.3 (review-fix): a blank sandbox_id resolves to the
    # shared "default" bucket - rmtree-ing it because one blank-id node
    # was deleted would take a DIFFERENT still-live blank-id node's
    # directory down with it. See graphlink_scratch_dirs.
    # remove_scratch_dir_for_id's own docstring.
    async def run():
        dispatcher = AgentDispatcher(_FakeSettingsManager())
        shared_default = EXECUTION_SANDBOX_ROOT / "default"
        shared_default.mkdir(parents=True, exist_ok=True)
        (shared_default / "someone_elses_venv_marker.txt").write_text("data", encoding="utf-8")
        try:
            await dispatcher.remove_code_sandbox_scratch_dir("")
            assert shared_default.exists(), "a blank sandbox_id must never remove the shared bucket"
        finally:
            import shutil
            shutil.rmtree(shared_default, ignore_errors=True)

    asyncio.run(run())


# -- R6.2: start_chart_generation/_call_chart_agent - the independent chart
# generation slot -------------------------------------------------------------
#
# Mirrors the R5.2 artifact section's own structure: these tests never touch
# Ollama/api_provider.chat plumbing - start_chart_generation's only real
# dependency is ChartDataAgent.get_response, monkeypatched directly on the
# class (agents.py constructs a fresh ChartDataAgent() instance per call, so
# patching the class method is the seam, mirroring how ArtifactAgent.
# get_response and WebResearchService.run are patched as class-level seams
# for their own dispatch paths).


def _make_chart_env():
    bus = SessionBus("agents-chart-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", lambda: {})
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    return bus, notifications, dispatcher


def test_call_chart_agent_calls_get_response_and_returns_the_parsed_dict(monkeypatch):
    captured = []

    def fake_get_response(self, text, chart_type):
        captured.append((text, chart_type))
        return '{"type": "bar", "title": "T", "labels": ["A"], "values": [1]}'

    monkeypatch.setattr(agents_module.ChartDataAgent, "get_response", fake_get_response)

    result = agents_module._call_chart_agent("some source text", "bar")

    assert result == {"type": "bar", "title": "T", "labels": ["A"], "values": [1]}
    assert captured == [("some source text", "bar")]


def test_start_chart_generation_calls_on_success_with_the_parsed_result_then_clears_the_slot(monkeypatch):
    def fake_get_response(self, text, chart_type):
        return '{"type": "bar", "title": "T", "labels": ["A", "B"], "values": [1, 2]}'

    monkeypatch.setattr(agents_module.ChartDataAgent, "get_response", fake_get_response)

    async def run():
        bus, notifications, dispatcher = _make_chart_env()
        successes = []
        failures = []

        await dispatcher.start_chart_generation(
            bus=bus,
            notifications_state=notifications,
            node_id="n-parent",
            chart_type="bar",
            source_text="the parent branch's text",
            on_success=lambda result: successes.append(result),
            on_failure=lambda message: failures.append(message),
        )
        # ADR-006 stage 6.2 fire-and-forget: the awaited call above only
        # claims the slot and schedules the generation - drain the scheduled
        # task so on_success and the release have landed before asserting.
        await drain_runs(dispatcher, "chart")

        assert successes == [{"type": "bar", "title": "T", "labels": ["A", "B"], "values": [1, 2]}]
        assert failures == []
        assert busy_count(dispatcher, "chart") == 0
        assert notifications.visible is False

    asyncio.run(run())


def test_start_chart_generation_second_call_while_in_flight_is_rejected(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocking_get_response(self, text, chart_type):
        started.set()
        release.wait(5)
        return '{"type": "bar", "title": "T", "labels": ["A"], "values": [1]}'

    monkeypatch.setattr(agents_module.ChartDataAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, dispatcher = _make_chart_env()
        successes = []

        # ADR-006 stage 6.2 fire-and-forget: start_chart_generation now
        # claims the slot synchronously and schedules the generation itself,
        # returning before it runs - the first call stays in flight on its
        # own, no wrapper task needed to race a second call against it.
        await dispatcher.start_chart_generation(
            bus=bus,
            notifications_state=notifications,
            node_id="n1",
            chart_type="bar",
            source_text="text one",
            on_success=lambda result: successes.append(result),
            on_failure=lambda message: None,
        )
        # Grab the scheduled worker task now, while the slot still holds it,
        # so it can be drained at the end.
        first_call_task = next(
            handle.task for handle in dispatcher._runs.values() if handle.kind == "chart"
        )
        await asyncio.to_thread(started.wait, 5)

        # Second call while the first is still in flight must be rejected
        # and must not disturb the first request.
        await dispatcher.start_chart_generation(
            bus=bus,
            notifications_state=notifications,
            node_id="n2",
            chart_type="pie",
            source_text="text two",
            on_success=lambda result: successes.append(result),
            on_failure=lambda message: None,
        )
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "A chart is already being generated."
        assert busy_count(dispatcher, "chart") == 1

        release.set()
        await first_call_task

        assert successes == [{"type": "bar", "title": "T", "labels": ["A"], "values": [1]}]
        assert busy_count(dispatcher, "chart") == 0

    asyncio.run(run())


def test_start_chart_generation_top_level_error_key_calls_on_failure_and_shows_notification_never_calls_on_success(monkeypatch):
    # Mirrors ChartWorkerThread.run()'s own `if 'error' in parsed: raise
    # ValueError(...)` check at the one legacy call site - a fully-degraded
    # ChartDataAgent response (even its own heuristic fallback found
    # nothing) must never reach on_success.
    def fake_get_response(self, text, chart_type):
        return '{"error": "Could not find sufficient data to generate a bar chart."}'

    monkeypatch.setattr(agents_module.ChartDataAgent, "get_response", fake_get_response)

    async def run():
        bus, notifications, dispatcher = _make_chart_env()
        successes = []
        failures = []

        await dispatcher.start_chart_generation(
            bus=bus,
            notifications_state=notifications,
            node_id="n1",
            chart_type="bar",
            source_text="text with no chartable data",
            on_success=lambda result: successes.append(result),
            on_failure=lambda message: failures.append(message),
        )
        # ADR-006 stage 6.2 fire-and-forget: drain the scheduled task so the
        # failure path has run before asserting.
        await drain_runs(dispatcher, "chart")

        assert successes == [], "on_success must never be called on a top-level error-key response"
        assert failures == ["Could not find sufficient data to generate a bar chart."]
        assert busy_count(dispatcher, "chart") == 0
        assert notifications.visible is True
        assert notifications.msg_type == "error"
        assert notifications.message == (
            "Chart generation failed: Could not find sufficient data to generate a bar chart."
        )

    asyncio.run(run())


def test_start_chart_generation_timeout_fires_the_exact_message_and_clears_the_slot(monkeypatch):
    monkeypatch.setattr(agents_module, "WATCHDOG_TIMEOUT_SECONDS", 0.05)

    def slow_get_response(self, text, chart_type):
        time.sleep(0.3)
        return '{"type": "bar", "title": "T", "labels": ["A"], "values": [1]}'

    monkeypatch.setattr(agents_module.ChartDataAgent, "get_response", slow_get_response)

    async def run():
        bus, notifications, dispatcher = _make_chart_env()
        successes = []
        failures = []

        await dispatcher.start_chart_generation(
            bus=bus,
            notifications_state=notifications,
            node_id="n1",
            chart_type="bar",
            source_text="text",
            on_success=lambda result: successes.append(result),
            on_failure=lambda message: failures.append(message),
        )
        # ADR-006 stage 6.2 fire-and-forget: drain the scheduled task so the
        # timeout path has run before asserting.
        await drain_runs(dispatcher, "chart")

        assert successes == []
        assert len(failures) == 1 and "stopped responding" in failures[0]
        assert busy_count(dispatcher, "chart") == 0, "the slot must not leak/deadlock future requests"
        assert notifications.visible is True
        assert notifications.msg_type == "error"

    asyncio.run(run())


def test_chart_request_and_chat_request_run_concurrently_both_busy(monkeypatch):
    """ADR-002 stage 2.3 regression guard: chat and chart now claim into
    the SAME self._runs registry (previously two fully disjoint dicts),
    so this isolation is no longer structural by construction - it
    depends entirely on RunRegistry.is_busy()'s kind filter being correct.
    Mirrors every OTHER cross-kind concurrency test in this file (e.g.
    test_image_request_and_chat_request_run_concurrently_both_dicts_non_empty
    above): both must be simultaneously in flight, neither blocking nor
    blocked by the other, and neither bounced by a busy notification."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    chart_started = threading.Event()
    chart_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_get_response(self, text, chart_type):
        chart_started.set()
        chart_release.wait(5)
        return '{"type": "bar", "title": "T", "labels": ["A"], "values": [1]}'

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(agents_module.ChartDataAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        chat_replies = []
        chart_successes = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=chat_replies.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: start_chart_generation now
        # schedules its own background task and returns immediately, the
        # same shape as chat - no wrapper task needed to race the two.
        await dispatcher.start_chart_generation(
            bus=bus, notifications_state=notifications, node_id="n1",
            chart_type="bar", source_text="text", on_success=chart_successes.append,
            on_failure=lambda message: None,
        )

        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(chart_started.wait, 5)

        # THE key assertion: both are genuinely in flight at the same time -
        # neither bounced the other with a busy notification.
        assert len(chat_slots(dispatcher)) == 1
        assert busy_count(dispatcher, "chart") == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(chat_slots(dispatcher).values()))
        await chat_entry["task"]
        chart_release.set()
        # ADR-006 stage 6.2 fire-and-forget: drain chart's scheduled task
        # before asserting its side effects.
        await drain_runs(dispatcher, "chart")

        assert chat_replies == ["chat reply"]
        assert chart_successes == [{"type": "bar", "title": "T", "labels": ["A"], "values": [1]}]

    asyncio.run(run())


# -- R8a: note agents (Key Takeaway / Explainer Note) -------------------------
#
# These two were implemented in the deleted Qt app and never ported, leaving
# their menu items as disabled stubs. Mocking follows the chart/artifact seam
# (patch the agent CLASS's get_response), not the api_provider.chat seam,
# because agents.py constructs a fresh agent instance per call.


def _make_note_env():
    bus = SessionBus("agents-note-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", lambda: {})
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    return bus, notifications, dispatcher


def test_start_note_generation_takeaway_calls_on_success_then_clears_the_slot(monkeypatch):
    monkeypatch.setattr(
        agents_module.KeyTakeawayAgent, "get_response",
        lambda self, text: f"Key Takeaway\n\nMain Points:\n• from {text}",
    )

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        successes, failures = [], []
        await dispatcher.start_note_generation(
            bus=bus, notifications_state=notifications, node_id="n1",
            note_kind="takeaway", source_text="the source node's text",
            on_success=successes.append, on_failure=failures.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain the scheduled task so
        # on_success and the release have landed before asserting.
        await drain_runs(dispatcher, "note")
        assert successes == ["Key Takeaway\n\nMain Points:\n• from the source node's text"]
        assert failures == []
        assert busy_count(dispatcher, "note") == 0
        assert notifications.visible is False

    asyncio.run(run())


def test_start_note_generation_explainer_uses_the_explainer_agent(monkeypatch):
    # note_kind must actually select the agent - a regression here would
    # silently produce takeaways for both menu items.
    monkeypatch.setattr(agents_module.KeyTakeawayAgent, "get_response", lambda self, text: "TAKEAWAY")
    monkeypatch.setattr(agents_module.ExplainerAgent, "get_response", lambda self, text: "EXPLAINER")

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        got = []
        await dispatcher.start_note_generation(
            bus=bus, notifications_state=notifications, node_id="n1",
            note_kind="explainer", source_text="x",
            on_success=got.append, on_failure=lambda m: None,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain before asserting.
        await drain_runs(dispatcher, "note")
        assert got == ["EXPLAINER"]

    asyncio.run(run())


def test_start_note_generation_rejects_a_second_concurrent_run(monkeypatch):
    monkeypatch.setattr(agents_module.KeyTakeawayAgent, "get_response", lambda self, text: "ok")

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        seed = dispatcher._runs.claim("note")
        successes = []
        await dispatcher.start_note_generation(
            bus=bus, notifications_state=notifications, node_id="n1",
            note_kind="takeaway", source_text="x",
            on_success=successes.append, on_failure=lambda m: None,
        )
        assert successes == [], "the busy guard must not run a second agent"
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        # The pre-existing claim must survive - the guard rejects, it
        # must never clear someone else's in-flight slot.
        assert dispatcher._runs.get(seed.request_id) is seed
        assert busy_count(dispatcher, "note") == 1

    asyncio.run(run())


def test_start_note_generation_empty_response_fails_instead_of_creating_a_blank_note(monkeypatch):
    monkeypatch.setattr(agents_module.KeyTakeawayAgent, "get_response", lambda self, text: "   ")

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        successes, failures = [], []
        await dispatcher.start_note_generation(
            bus=bus, notifications_state=notifications, node_id="n1",
            note_kind="takeaway", source_text="x",
            on_success=successes.append, on_failure=failures.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain so the validate-failure
        # path has run before asserting.
        await drain_runs(dispatcher, "note")
        assert successes == [], "an empty agent response must not become a note"
        assert len(failures) == 1
        assert notifications.msg_type == "error"
        assert busy_count(dispatcher, "note") == 0

    asyncio.run(run())


def test_start_note_generation_agent_exception_surfaces_and_clears_the_slot(monkeypatch):
    def _boom(self, text):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(agents_module.KeyTakeawayAgent, "get_response", _boom)

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        successes, failures = [], []
        await dispatcher.start_note_generation(
            bus=bus, notifications_state=notifications, node_id="n1",
            note_kind="takeaway", source_text="x",
            on_success=successes.append, on_failure=failures.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain so the exception path has
        # run before asserting.
        await drain_runs(dispatcher, "note")
        assert successes == []
        assert "model exploded" in failures[0]
        assert notifications.msg_type == "error"
        assert busy_count(dispatcher, "note") == 0, "the slot must not leak after a failure"

    asyncio.run(run())


def test_note_request_and_chat_request_run_concurrently_both_busy(monkeypatch):
    """ADR-002 stage 2.3 regression guard, note's counterpart to
    test_chart_request_and_chat_request_run_concurrently_both_busy above -
    same reasoning: chat and note now claim into the SAME self._runs
    registry, so this isolation depends entirely on RunRegistry.is_busy()'s
    kind filter rather than being structural by construction."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    note_started = threading.Event()
    note_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_get_response(self, text):
        note_started.set()
        note_release.wait(5)
        return "Key Takeaway\n\nMain Points:\n• done"

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(agents_module.KeyTakeawayAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        chat_replies = []
        note_successes = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=chat_replies.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: start_note_generation now
        # schedules its own background task and returns immediately, the
        # same shape as chat - no wrapper task needed to race the two.
        await dispatcher.start_note_generation(
            bus=bus, notifications_state=notifications, node_id="n1",
            note_kind="takeaway", source_text="x", on_success=note_successes.append,
            on_failure=lambda message: None,
        )

        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(note_started.wait, 5)

        # THE key assertion: both are genuinely in flight at the same time -
        # neither bounced the other with a busy notification.
        assert len(chat_slots(dispatcher)) == 1
        assert busy_count(dispatcher, "note") == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(chat_slots(dispatcher).values()))
        await chat_entry["task"]
        note_release.set()
        # ADR-006 stage 6.2 fire-and-forget: drain note's scheduled task
        # before asserting its side effects.
        await drain_runs(dispatcher, "note")

        assert chat_replies == ["chat reply"]
        assert note_successes == ["Key Takeaway\n\nMain Points:\n• done"]

    asyncio.run(run())


# -- ADR-002 Workstream 1: start_branch_comparison ("Compare Branches") ------
#
# Mirrors start_note_generation's own test shape exactly - same busy-guard/
# timeout/empty-response/exception coverage - but against the SEPARATE
# "branch_comparison" kind in self._runs, proving the two features' busy
# states are genuinely independent (see that field's own comment for why).


def test_start_branch_comparison_calls_on_success_then_clears_the_slot(monkeypatch):
    monkeypatch.setattr(
        agents_module.BranchComparisonAgent, "get_response",
        lambda self, text: f"Branch Comparison\n\nAgreements:\n• from {text}",
    )

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        successes, failures = [], []
        await dispatcher.start_branch_comparison(
            bus=bus, notifications_state=notifications,
            source_text="=== Branch 1 ===\n...",
            on_success=successes.append, on_failure=failures.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain the scheduled task so
        # on_success and the release have landed before asserting.
        await drain_runs(dispatcher, "branch_comparison")
        assert successes == ["Branch Comparison\n\nAgreements:\n• from === Branch 1 ===\n..."]
        assert failures == []
        assert busy_count(dispatcher, "branch_comparison") == 0
        assert notifications.visible is False

    asyncio.run(run())


def test_start_branch_comparison_rejects_a_second_concurrent_run(monkeypatch):
    monkeypatch.setattr(agents_module.BranchComparisonAgent, "get_response", lambda self, text: "ok")

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        seed = dispatcher._runs.claim("branch_comparison")
        successes = []
        await dispatcher.start_branch_comparison(
            bus=bus, notifications_state=notifications, source_text="x",
            on_success=successes.append, on_failure=lambda m: None,
        )
        assert successes == [], "the busy guard must not run a second agent"
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        # The pre-existing claim must survive - the guard rejects, it
        # must never clear someone else's in-flight slot.
        assert dispatcher._runs.get(seed.request_id) is seed
        assert busy_count(dispatcher, "branch_comparison") == 1

    asyncio.run(run())


def test_start_branch_comparison_does_not_share_a_busy_slot_with_note_generation(monkeypatch):
    # The whole reason for a SEPARATE dict: an in-flight Key Takeaway must
    # never block an unrelated Compare Branches call, and vice versa.
    monkeypatch.setattr(agents_module.BranchComparisonAgent, "get_response", lambda self, text: "Branch Comparison")

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        dispatcher._runs.claim("note")
        successes = []
        await dispatcher.start_branch_comparison(
            bus=bus, notifications_state=notifications, source_text="x",
            on_success=successes.append, on_failure=lambda m: None,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain before asserting (the
        # seeded "note" claim has no task, so only the comparison drains).
        await drain_runs(dispatcher, "branch_comparison")
        assert successes == ["Branch Comparison"], "an in-flight note generation must not block this"

    asyncio.run(run())


def test_start_branch_comparison_empty_response_fails_instead_of_creating_a_blank_note(monkeypatch):
    monkeypatch.setattr(agents_module.BranchComparisonAgent, "get_response", lambda self, text: "   ")

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        successes, failures = [], []
        await dispatcher.start_branch_comparison(
            bus=bus, notifications_state=notifications, source_text="x",
            on_success=successes.append, on_failure=failures.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain so the validate-failure
        # path has run before asserting.
        await drain_runs(dispatcher, "branch_comparison")
        assert successes == [], "an empty agent response must not become a note"
        assert len(failures) == 1
        assert notifications.msg_type == "error"
        assert busy_count(dispatcher, "branch_comparison") == 0

    asyncio.run(run())


def test_start_branch_comparison_agent_exception_surfaces_and_clears_the_slot(monkeypatch):
    def _boom(self, text):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(agents_module.BranchComparisonAgent, "get_response", _boom)

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        successes, failures = [], []
        await dispatcher.start_branch_comparison(
            bus=bus, notifications_state=notifications, source_text="x",
            on_success=successes.append, on_failure=failures.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain so the exception path has
        # run before asserting.
        await drain_runs(dispatcher, "branch_comparison")
        assert successes == []
        assert "model exploded" in failures[0]
        assert notifications.msg_type == "error"
        assert busy_count(dispatcher, "branch_comparison") == 0, "the slot must not leak after a failure"

    asyncio.run(run())


# -- ADR-002 Workstream 1: start_branch_synthesis ("Synthesize Branches") ----
#
# Mirrors start_branch_comparison's own test shape exactly - same busy-guard/
# timeout/empty-response/exception coverage - but against the SEPARATE
# "branch_synthesis" kind in self._runs, proving Compare and Synthesize's
# busy states are genuinely independent (see that field's own comment for why).


def test_start_branch_synthesis_calls_on_success_then_clears_the_slot(monkeypatch):
    monkeypatch.setattr(
        agents_module.BranchSynthesisAgent, "get_response",
        lambda self, text, instructions: f"Combined from {text} per '{instructions}'",
    )

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        successes, failures = [], []
        await dispatcher.start_branch_synthesis(
            bus=bus, notifications_state=notifications,
            source_text="=== Branch 1 ===\n...", instructions="merge them",
            on_success=successes.append, on_failure=failures.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain the scheduled task so
        # on_success and the release have landed before asserting.
        await drain_runs(dispatcher, "branch_synthesis")
        assert successes == ["Combined from === Branch 1 ===\n... per 'merge them'"]
        assert failures == []
        assert busy_count(dispatcher, "branch_synthesis") == 0
        assert notifications.visible is False

    asyncio.run(run())


def test_start_branch_synthesis_rejects_a_second_concurrent_run(monkeypatch):
    monkeypatch.setattr(agents_module.BranchSynthesisAgent, "get_response", lambda self, text, instructions: "ok")

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        seed = dispatcher._runs.claim("branch_synthesis")
        successes = []
        await dispatcher.start_branch_synthesis(
            bus=bus, notifications_state=notifications, source_text="x", instructions="y",
            on_success=successes.append, on_failure=lambda m: None,
        )
        assert successes == [], "the busy guard must not run a second agent"
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert dispatcher._runs.get(seed.request_id) is seed
        assert busy_count(dispatcher, "branch_synthesis") == 1

    asyncio.run(run())


def test_start_branch_synthesis_does_not_share_a_busy_slot_with_branch_comparison(monkeypatch):
    # The whole reason for a SEPARATE dict: an in-flight Compare Branches
    # call must never block an unrelated Synthesize Branches call, and vice
    # versa - they are unrelated user gestures over the same kind of
    # selection.
    monkeypatch.setattr(
        agents_module.BranchSynthesisAgent, "get_response",
        lambda self, text, instructions: "Combined answer.",
    )

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        dispatcher._runs.claim("branch_comparison")
        successes = []
        await dispatcher.start_branch_synthesis(
            bus=bus, notifications_state=notifications, source_text="x", instructions="y",
            on_success=successes.append, on_failure=lambda m: None,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain before asserting (the
        # seeded "branch_comparison" claim has no task, so only the
        # synthesis drains).
        await drain_runs(dispatcher, "branch_synthesis")
        assert successes == ["Combined answer."], "an in-flight branch comparison must not block this"

    asyncio.run(run())


def test_start_branch_synthesis_empty_response_fails_instead_of_creating_a_blank_node(monkeypatch):
    monkeypatch.setattr(agents_module.BranchSynthesisAgent, "get_response", lambda self, text, instructions: "   ")

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        successes, failures = [], []
        await dispatcher.start_branch_synthesis(
            bus=bus, notifications_state=notifications, source_text="x", instructions="y",
            on_success=successes.append, on_failure=failures.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain so the validate-failure
        # path has run before asserting.
        await drain_runs(dispatcher, "branch_synthesis")
        assert successes == [], "an empty agent response must not become a node"
        assert len(failures) == 1
        assert notifications.msg_type == "error"
        assert busy_count(dispatcher, "branch_synthesis") == 0

    asyncio.run(run())


def test_start_branch_synthesis_agent_exception_surfaces_and_clears_the_slot(monkeypatch):
    def _boom(self, text, instructions):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(agents_module.BranchSynthesisAgent, "get_response", _boom)

    async def run():
        bus, notifications, dispatcher = _make_note_env()
        successes, failures = [], []
        await dispatcher.start_branch_synthesis(
            bus=bus, notifications_state=notifications, source_text="x", instructions="y",
            on_success=successes.append, on_failure=failures.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: drain so the exception path has
        # run before asserting.
        await drain_runs(dispatcher, "branch_synthesis")
        assert successes == []
        assert "model exploded" in failures[0]
        assert notifications.msg_type == "error"
        assert busy_count(dispatcher, "branch_synthesis") == 0, "the slot must not leak after a failure"

    asyncio.run(run())


def test_branch_comparison_request_and_chat_request_run_concurrently_both_busy(monkeypatch):
    """ADR-002 stage 2.4 regression guard, branch_comparison's counterpart
    to test_chart_request_and_chat_request_run_concurrently_both_busy
    (stage 2.3) - same reasoning: chat and branch_comparison now claim
    into the SAME self._runs registry, so this isolation depends entirely
    on RunRegistry.is_busy()'s kind filter rather than being structural by
    construction."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    comparison_started = threading.Event()
    comparison_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_get_response(self, text):
        comparison_started.set()
        comparison_release.wait(5)
        return "Branch Comparison\n\nAgreements:\n• done"

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(agents_module.BranchComparisonAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        chat_replies = []
        comparison_successes = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=chat_replies.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: start_branch_comparison now
        # schedules its own background task and returns immediately, the
        # same shape as chat - no wrapper task needed to race the two.
        await dispatcher.start_branch_comparison(
            bus=bus, notifications_state=notifications, source_text="x",
            on_success=comparison_successes.append, on_failure=lambda message: None,
        )

        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(comparison_started.wait, 5)

        # THE key assertion: both are genuinely in flight at the same time -
        # neither bounced the other with a busy notification.
        assert len(chat_slots(dispatcher)) == 1
        assert busy_count(dispatcher, "branch_comparison") == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(chat_slots(dispatcher).values()))
        await chat_entry["task"]
        comparison_release.set()
        # ADR-006 stage 6.2 fire-and-forget: drain the comparison's
        # scheduled task before asserting its side effects.
        await drain_runs(dispatcher, "branch_comparison")

        assert chat_replies == ["chat reply"]
        assert comparison_successes == ["Branch Comparison\n\nAgreements:\n• done"]

    asyncio.run(run())


def test_branch_synthesis_request_and_chat_request_run_concurrently_both_busy(monkeypatch):
    """ADR-002 stage 2.4 regression guard, branch_synthesis's counterpart
    to test_branch_comparison_request_and_chat_request_run_concurrently_
    both_busy above - same reasoning."""
    chat_started = threading.Event()
    chat_release = threading.Event()
    synthesis_started = threading.Event()
    synthesis_release = threading.Event()

    def blocking_chat(task, messages, **kwargs):
        chat_started.set()
        chat_release.wait(5)
        return {"message": {"content": "chat reply"}}

    def blocking_get_response(self, text, instructions):
        synthesis_started.set()
        synthesis_release.wait(5)
        return "Combined answer."

    _configure_fake_ollama(monkeypatch, blocking_chat)
    monkeypatch.setattr(agents_module.BranchSynthesisAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        chat_replies = []
        synthesis_successes = []

        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=chat_replies.append,
        )
        # ADR-006 stage 6.2 fire-and-forget: start_branch_synthesis now
        # schedules its own background task and returns immediately, the
        # same shape as chat - no wrapper task needed to race the two.
        await dispatcher.start_branch_synthesis(
            bus=bus, notifications_state=notifications, source_text="x", instructions="y",
            on_success=synthesis_successes.append, on_failure=lambda message: None,
        )

        await asyncio.to_thread(chat_started.wait, 5)
        await asyncio.to_thread(synthesis_started.wait, 5)

        assert len(chat_slots(dispatcher)) == 1
        assert busy_count(dispatcher, "branch_synthesis") == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(chat_slots(dispatcher).values()))
        await chat_entry["task"]
        synthesis_release.set()
        # ADR-006 stage 6.2 fire-and-forget: drain the synthesis's scheduled
        # task before asserting its side effects.
        await drain_runs(dispatcher, "branch_synthesis")

        assert chat_replies == ["chat reply"]
        assert synthesis_successes == ["Combined answer."]

    asyncio.run(run())


# -- ADR-006 stage 6.2 review fixes: release-on-cancel at the dispatcher level -


def test_cancelled_runs_late_teardown_never_clobbers_a_new_runs_claimed_slot(monkeypatch):
    """ADR-006 stage 6.2 review fix (NEW-RUN-CLOBBER): release-on-cancel
    frees the chat slot the instant a cancel lands, so a NEW chat run can
    claim it while the cancelled worker is still unwinding. That stale
    worker's late finally must neither pop the new run's handle nor re-run
    run 1's end transition (cancel() already ran it via finalize) - the
    gated `if release(): on_end()` tail is exactly what this pins, through
    _dispatch's own on_begin/on_end callbacks."""
    started1, release1 = threading.Event(), threading.Event()
    started2, release2 = threading.Event(), threading.Event()
    calls = []

    def sequential_blocking_chat(task, messages, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            started1.set()
            release1.wait(5)
            # Run 1 was cancelled mid-flight - the real driver observes the
            # cancel event and raises, same shape as the mid-flight-cancel
            # test above.
            raise api_provider.RequestCancelledError("Request cancelled.")
        started2.set()
        release2.wait(5)
        return {"message": {"content": "second reply"}}

    _configure_fake_ollama(monkeypatch, sequential_blocking_chat)

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        begins1, ends1 = [], []
        begins2, ends2 = [], []

        await dispatcher._dispatch(
            bus=bus,
            notifications_state=notifications,
            conversation_history=[{"role": "user", "content": "first"}],
            on_reply=lambda text: None,
            on_begin=begins1.append,
            on_end=lambda: ends1.append(1),
            state_topic="app-composer",
        )
        await asyncio.to_thread(started1.wait, 5)
        request_id1, entry1 = next(iter(chat_slots(dispatcher).items()))

        # Cancel run 1 - release-on-cancel: the slot frees IMMEDIATELY,
        # while the worker is still held open.
        assert dispatcher.cancel(request_id1) is True
        assert chat_slots(dispatcher) == {}

        # cancel() schedules run 1's finalize (its on_end + publish) - it
        # must land exactly once, promptly, without waiting for the worker.
        deadline = asyncio.get_running_loop().time() + 2
        while len(ends1) < 1 and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert ends1 == [1], "cancel must run run 1's end transition exactly once"

        # Run 2 claims the freed slot while run 1's worker is STILL running.
        await dispatcher._dispatch(
            bus=bus,
            notifications_state=notifications,
            conversation_history=[{"role": "user", "content": "second"}],
            on_reply=lambda text: None,
            on_begin=begins2.append,
            on_end=lambda: ends2.append(1),
            state_topic="app-composer",
        )
        await asyncio.to_thread(started2.wait, 5)
        request_id2 = next(iter(chat_slots(dispatcher).keys()))
        assert request_id2 != request_id1

        # NOW let run 1's stale worker unwind fully...
        release1.set()
        await entry1["task"]

        # ...and run 2's claim must be completely undisturbed by it.
        assert len(chat_slots(dispatcher)) == 1, "run 1's late teardown must not free run 2's slot"
        assert next(iter(chat_slots(dispatcher).keys())) == request_id2, (
            "run 2's handle must still be the claimed one after run 1's stale finally"
        )
        assert ends1 == [1], "run 1's end transition must NOT fire a second time from the stale finally"
        assert ends2 == [], "run 2 is still in flight - its end transition must not have fired"
        assert begins1 == [request_id1] and begins2 == [request_id2]

        release2.set()
        entry2 = next(iter(chat_slots(dispatcher).values()))
        await entry2["task"]
        assert chat_slots(dispatcher) == {}
        assert ends2 == [1]

    asyncio.run(run())


def test_cancel_all_now_cancels_a_chart_generation_and_frees_its_slot_immediately(monkeypatch):
    """ADR-006 stage 6.2 review fix: runtime proof that a formerly-
    uncancellable kind (chart - pre-6.2 cancel_all silently skipped it)
    really cancels now: the slot frees the instant cancel_all() fires, the
    late result is suppressed (on_success never called), and no error
    notification is shown for work the user abandoned."""
    started = threading.Event()
    release = threading.Event()

    def blocking_get_response(self, text, chart_type):
        started.set()
        release.wait(5)
        return '{"type": "bar", "title": "T", "labels": ["A"], "values": [1]}'

    monkeypatch.setattr(agents_module.ChartDataAgent, "get_response", blocking_get_response)

    async def run():
        bus, notifications, dispatcher = _make_chart_env()
        successes = []
        failures = []

        await dispatcher.start_chart_generation(
            bus=bus,
            notifications_state=notifications,
            node_id="n1",
            chart_type="bar",
            source_text="text",
            on_success=lambda result: successes.append(result),
            on_failure=lambda message: failures.append(message),
        )
        # Grab the scheduled worker task now - after cancel_all it is only
        # reachable as an orphan, no longer via the slots.
        worker_task = next(
            handle.task for handle in dispatcher._runs.values() if handle.kind == "chart"
        )
        await asyncio.to_thread(started.wait, 5)
        assert busy_count(dispatcher, "chart") == 1

        dispatcher.cancel_all()

        # Release-on-cancel: the slot is free the moment cancel_all returns,
        # while the worker is still held open...
        assert busy_count(dispatcher, "chart") == 0
        assert not release.is_set()
        # ...but the unwinding worker still counts as live work (eviction veto).
        assert dispatcher.has_in_flight_runs() is True

        release.set()
        await worker_task
        deadline = asyncio.get_running_loop().time() + 2
        while dispatcher.has_in_flight_runs() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert dispatcher.has_in_flight_runs() is False

        assert successes == [], "a cancelled chart's late result must be suppressed"
        assert failures == []
        assert notifications.visible is False, (
            "no error notification for work the user already abandoned"
        )

    asyncio.run(run())


def test_stale_artifact_teardown_never_clears_a_newer_runs_pending_request_id(monkeypatch):
    """ADR-006 stage 6.2 review fix (pins start_artifact_reply's finally):
    release-on-cancel frees the artifact slot instantly, so a NEW artifact
    run can claim and stamp the SAME node before the cancelled worker
    unwinds. The old unconditional `node.pending_request_id = None` in the
    stale worker's finally wiped the new run's in-flight marker - the fix
    only clears it when it still equals the stale run's own request_id."""
    started1, release1 = threading.Event(), threading.Event()
    started2, release2 = threading.Event(), threading.Event()
    calls = []

    def sequential_blocking_get_response(self, current_artifact, history):
        calls.append(1)
        if len(calls) == 1:
            started1.set()
            release1.wait(5)
            return "first document", "first message"
        started2.set()
        release2.wait(5)
        return "second document", "second message"

    monkeypatch.setattr(
        agents_module.ArtifactAgent, "get_response", sequential_blocking_get_response
    )

    async def run():
        bus, notifications, dispatcher = _make_artifact_env()
        node = _make_node()
        replies = []

        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            current_artifact="doc",
            history=[],
            on_reply=lambda new_content, ai_message: replies.append((new_content, ai_message)),
        )
        await asyncio.to_thread(started1.wait, 5)
        request_id1, entry1 = next(iter(artifact_slots(dispatcher).items()))
        assert node.pending_request_id == request_id1

        assert dispatcher.cancel_artifact(request_id1) is True
        assert artifact_slots(dispatcher) == {}, "release-on-cancel frees the slot immediately"

        # Run 2 on the SAME node, while run 1's worker is still held open.
        await dispatcher.start_artifact_reply(
            bus=bus,
            notifications_state=notifications,
            node=node,
            current_artifact="doc",
            history=[],
            on_reply=lambda new_content, ai_message: replies.append((new_content, ai_message)),
        )
        await asyncio.to_thread(started2.wait, 5)
        request_id2, entry2 = next(iter(artifact_slots(dispatcher).items()))
        assert node.pending_request_id == request_id2

        # Run 1's stale worker unwinds fully - its finally must NOT clear
        # run 2's in-flight marker.
        release1.set()
        await entry1["task"]
        assert node.pending_request_id == request_id2, (
            "the stale run's teardown wiped the newer run's pending_request_id"
        )
        assert replies == [], "the cancelled run's result must have been dropped"

        release2.set()
        await entry2["task"]
        assert replies == [("second document", "second message")]
        assert node.pending_request_id is None
        assert artifact_slots(dispatcher) == {}

    asyncio.run(run())


# -- ADR-006 stage 6.5: per-session ProviderRuntime reaches the dispatcher ----


def test_dispatcher_gates_and_routes_through_its_injected_provider_runtime(monkeypatch):
    # The app-level half of the 6.5 exit criterion, at AgentDispatcher
    # granularity: a dispatcher built with its own ProviderRuntime (a) gates
    # is_configured() on THAT runtime, not the module globals, and (b) hands
    # the runtime to the chat driver. The module globals are deliberately
    # UNCONFIGURED here (empty chat model), so a dispatcher still consulting
    # them would refuse to dispatch at all - passing this test requires the
    # injected runtime to be the one consulted end to end.
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "")
    assert api_provider.is_configured() is False  # the default path would refuse

    runtime = api_provider.ProviderRuntime()
    runtime.set_ollama_models({config.TASK_CHAT: "session-two-model:7b"})
    assert runtime.is_configured() is True

    seen_runtimes = []

    def fake_stream(conversation_history, persona_text, cancel_event, on_chunk, runtime=None, **kwargs):
        seen_runtimes.append(runtime)
        return "per-session reply"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", fake_stream)

    async def run():
        bus = SessionBus("agents-runtime-test")
        notifications = NotificationState()
        bus.register_topic("notification", notifications.payload)
        composer_document = ComposerDocument()
        bus.register_topic("app-composer", composer_document.payload)
        bus.register_topic("scene", lambda: {})
        dispatcher = AgentDispatcher(_FakeSettingsManager(), provider_runtime=runtime)
        replies = []
        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert replies == ["per-session reply"]
        assert seen_runtimes == [runtime]

    asyncio.run(run())


def test_default_dispatcher_still_calls_the_drivers_with_the_exact_pre_65_arity(monkeypatch):
    # Compat pin: a dispatcher WITHOUT an injected runtime (the default
    # session, and every existing test in this file) must keep calling
    # _call_chat_agent_stream with the exact pre-6.5 positional arity - no
    # runtime kwarg, and (6.7) no persona_is_override kwarg on the default-
    # persona path. ADR-006 stage 6.6 widened the contract by exactly ONE
    # always-passed keyword: on_context_trimmed (the trim/summarize
    # notification closure) - pinned here as keyword-only so no further
    # kwargs creep in unnoticed.
    def strict_pre_65_fake(conversation_history, persona_text, cancel_event, on_chunk, *,
                           on_context_trimmed):
        assert callable(on_context_trimmed)
        return "default reply"

    monkeypatch.setattr(agents_module, "_call_chat_agent_stream", strict_pre_65_fake)
    _configure_fake_ollama(monkeypatch, lambda task, messages, **kwargs: {"message": {"content": "unused"}})

    async def run():
        bus, notifications, composer_document, dispatcher = _make_dispatch_env()
        replies = []
        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "hi"}],
            on_reply=replies.append,
        )
        entry = next(iter(chat_slots(dispatcher).values()))
        await entry["task"]

        assert replies == ["default reply"]

    asyncio.run(run())


def test_register_agents_forwards_the_provider_runtime_to_the_dispatcher():
    bus = SessionBus("agents-register-runtime-test")
    runtime = api_provider.ProviderRuntime()

    dispatcher = agents_module.register_agents(
        bus, ComposerDocument(), NotificationState(), _FakeSettingsManager(), provider_runtime=runtime
    )

    assert dispatcher._provider_runtime is runtime
