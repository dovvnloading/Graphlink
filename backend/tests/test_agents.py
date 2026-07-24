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

# Importing any backend.* submodule runs backend/__init__.py first, which
# puts graphlink_app/ on sys.path - these must come before the bare
# top-level graphlink_app imports below (api_provider, graphlink_task_config,
# graphlink_licensing) for this module to import cleanly when run standalone.
import backend.agents as agents_module
from backend.agents import AgentDispatcher
from backend.canvas import SceneDocument, register_canvas
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState

import api_provider
import graphlink_task_config as config
from graphlink_licensing import SettingsManager
from graphlink_plugins.web_research.domain import RequestCancelled, ResearchFailure


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
    # and attempt a genuine network call. This fake mirrors
    # api_provider.chat_stream's own documented non-streaming-provider
    # fallback shape exactly (one blocking call plus one synthetic
    # full-text on_chunk), just delegating the blocking call to chat_fn
    # instead of the real chat() - so chat_fn's return value/exception/
    # cancellation behavior is preserved unchanged for both the streaming
    # and non-streaming dispatch paths. Tests that care about the streaming
    # semantics THEMSELVES (batching, reset events, ...) monkeypatch
    # api_provider.chat_stream directly instead - see the "R4.4: true token
    # streaming" section below.
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
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]

        assert replies == ["canned reply"]
        assert dispatcher._requests == {}
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

        assert dispatcher._requests == {}, "no task/thread work started"
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
        request_id, entry = next(iter(dispatcher._requests.items()))

        # Wait until the worker thread has actually entered chat() before
        # cancelling, so this is a genuine mid-flight cancel.
        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel(request_id) is True

        await entry["task"]

        assert dispatcher._requests == {}
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
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]

        assert dispatcher._requests == {}
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
        assert len(dispatcher._requests) == 1

        release.set()
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]
        assert replies == ["first reply"]
        assert dispatcher._requests == {}

        # Third call, after the first has fully completed, succeeds normally.
        monkeypatch.setattr(api_provider, "chat", lambda task, messages, **kwargs: {"message": {"content": "third reply"}})
        await dispatcher.start_chat_reply(
            bus=bus,
            notifications_state=notifications,
            composer_document=composer_document,
            conversation_history=[{"role": "user", "content": "third"}],
            on_reply=replies.append,
        )
        entry = next(iter(dispatcher._requests.values()))
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
    assert "Vertex" in persona_text  # BASE_SYSTEM_PROMPT's persona alias


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

        assert len(dispatcher_a._requests) == 1
        assert len(dispatcher_b._requests) == 1
        assert set(dispatcher_a._requests.keys()).isdisjoint(dispatcher_b._requests.keys())
        assert composer_a.request_state == "generating"
        assert composer_b.request_state == "generating"

        # Release out of start order - completion order must not matter.
        b_release.set()
        await next(iter(dispatcher_b._requests.values()))["task"]
        a_release.set()
        await next(iter(dispatcher_a._requests.values()))["task"]

        assert replies_a == ["reply for A"]
        assert replies_b == ["reply for B"]
        assert dispatcher_a._requests == {}
        assert dispatcher_b._requests == {}
        assert composer_a.request_state == "idle"
        assert composer_b.request_state == "idle"
        assert notif_a.visible is False
        assert notif_b.visible is False

    asyncio.run(run())


def test_rapid_fire_double_send_same_session_second_is_rejected(monkeypatch):
    """start_chat_reply has no `await` between the `if self._requests:`
    emptiness check and the dict insertion at the bottom - so two calls
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

        assert len(dispatcher._requests) == 1, "both calls must never be admitted concurrently"
        assert notifications.message == "A response is already being generated."

        release.set()
        await next(iter(dispatcher._requests.values()))["task"]
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
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]

        assert replies == ["canned reply"]
        assert dispatcher._requests == {}
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
        entry = next(iter(dispatcher._requests.values()))
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

        assert dispatcher._requests == {}, "no task/thread work started"
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
        request_id, entry = next(iter(dispatcher._requests.items()))

        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel(request_id) is True

        await entry["task"]

        assert dispatcher._requests == {}
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
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]

        assert dispatcher._requests == {}
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
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]

        assert dispatcher._requests == {}
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
        entry = next(iter(dispatcher._requests.values()))
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
        assert len(dispatcher._requests) == 1, "only the composer's original request stays in flight"

        release.set()
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]

        assert composer_replies == ["composer reply"]
        assert conversation_replies == [], "the bounced call never ran at all"
        assert dispatcher._requests == {}

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
        assert len(dispatcher._requests) == 1, "only the conversation node's original request stays in flight"

        release.set()
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]

        assert conversation_replies == ["conversation reply"]
        assert composer_replies == [], "the bounced call never ran at all"
        assert dispatcher._requests == {}
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
        entry = next(iter(dispatcher._image_requests.values()))
        await entry["task"]

        assert replies == [b"canned-image-bytes"]
        assert dispatcher._image_requests == {}
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
        assert len(dispatcher._image_requests) == 1

        release.set()
        entry = next(iter(dispatcher._image_requests.values()))
        await entry["task"]
        assert replies == [b"first-image-bytes"]
        assert dispatcher._image_requests == {}

    asyncio.run(run())


def test_image_request_and_chat_request_run_concurrently_both_dicts_non_empty(monkeypatch):
    """THE key concurrency-slot regression guard (R4.4a): a chat/composer
    request occupies self._requests while an image-generation request
    occupies the SEPARATE self._image_requests dict at the same time -
    neither blocks nor is blocked by the other, and both dicts are
    simultaneously non-empty at least once, proving these are two genuinely
    independent slots rather than aliases of the same guard (the whole point
    of AgentDispatcher._image_requests existing as its own field - see its
    comment in backend/agents.py)."""
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
        assert len(dispatcher._requests) == 1
        assert len(dispatcher._image_requests) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(dispatcher._requests.values()))
        await chat_entry["task"]
        image_release.set()
        image_entry = next(iter(dispatcher._image_requests.values()))
        await image_entry["task"]

        assert chat_replies == ["chat reply"]
        assert image_replies == [b"image-bytes"]
        assert dispatcher._requests == {}
        assert dispatcher._image_requests == {}
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
        entry = next(iter(dispatcher._image_requests.values()))
        await entry["task"]

        assert dispatcher._image_requests == {}
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
        entry = next(iter(dispatcher._image_requests.values()))
        await entry["task"]

        assert dispatcher._image_requests == {}, "the slot must not leak/deadlock future requests"
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
        entry = next(iter(dispatcher._image_requests.values()))
        await entry["task"]
        assert dispatcher._image_requests == {}

        monkeypatch.setattr(api_provider, "generate_image", lambda prompt, **kwargs: b"next-bytes")
        replies = []
        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="a dog", on_reply=replies.append,
        )
        entry = next(iter(dispatcher._image_requests.values()))
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
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]

        assert replies == ["Hello"]
        assert dispatcher._requests == {}
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
        request_id, entry = next(iter(dispatcher._requests.items()))

        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel(request_id) is True

        await entry["task"]

        assert replies == [], "cancel discards everything - no partial-text on_reply, matching R4.2 precedent"
        assert dispatcher._requests == {}
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
        entry = next(iter(dispatcher._requests.values()))
        await entry["task"]

        assert replies == []
        assert dispatcher._requests == {}
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
        entry = next(iter(dispatcher._requests.values()))
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
        entry = next(iter(dispatcher._requests.values()))
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
    stream. A chat stream paused mid-flight (self._requests) must not block,
    or be blocked by, a concurrent image-generation request
    (self._image_requests) - the two independent slots this dispatcher
    already guarantees (R4.4a) must keep holding under streaming too."""
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

        assert len(dispatcher._requests) == 1
        assert recorder.frames, "at least the first buffered delta should have flushed by now"

        await dispatcher.start_image_reply(
            bus=bus, notifications_state=notifications, prompt="a cat", on_reply=image_replies.append,
        )
        image_entry = next(iter(dispatcher._image_requests.values()))
        await image_entry["task"]

        # The image request completed independently, without waiting on the
        # still-paused chat stream.
        assert image_replies == [b"image-bytes"]
        assert dispatcher._image_requests == {}
        assert len(dispatcher._requests) == 1, "the chat stream is still in flight, untouched by the image request"
        assert notifications.visible is False, "neither request was rejected"

        chat_release.set()
        chat_entry = next(iter(dispatcher._requests.values()))
        await chat_entry["task"]

        assert chat_replies == ["final chat reply"]
        assert dispatcher._requests == {}
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
        entry = next(iter(dispatcher._web_research_requests.values()))
        await entry["task"]

        assert successes == [fake_result]
        assert dispatcher._web_research_requests == {}
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
        assert len(dispatcher._web_research_requests) == 1
        assert node2.pending_request_id is None, "the bounced call must never touch node2"

        release.set()
        entry = next(iter(dispatcher._web_research_requests.values()))
        await entry["task"]

        assert successes == [SimpleNamespace(answer_markdown="first result")]
        assert dispatcher._web_research_requests == {}

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
        entry = next(iter(dispatcher._web_research_requests.values()))
        await entry["task"]

        assert failures == [failure]
        assert dispatcher._web_research_requests == {}
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
        entry = next(iter(dispatcher._web_research_requests.values()))
        await entry["task"]

        assert failures == [cancelled_exc]
        assert dispatcher._web_research_requests == {}
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
        entry = next(iter(dispatcher._web_research_requests.values()))
        await entry["task"]

        assert len(failures) == 1
        assert isinstance(failures[0], RuntimeError)
        assert str(failures[0]) == "boom"
        assert dispatcher._web_research_requests == {}
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
        entry = next(iter(dispatcher._web_research_requests.values()))
        await entry["task"]

        assert len(failures) == 1
        assert isinstance(failures[0], ResearchFailure)
        expected_message = (
            "Web research stopped responding before the request completed. Please try again."
        )
        assert str(failures[0]) == expected_message
        assert failures[0].code == "watchdog_timeout"
        assert dispatcher._web_research_requests == {}, "the slot must not leak/deadlock future requests"
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
    has already popped _web_research_requests and cleared
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
        entry = next(iter(dispatcher._web_research_requests.values()))
        await entry["task"]
        assert dispatcher._web_research_requests == {}, "slot must already be cleared by the timeout branch"

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


def test_web_research_request_and_chat_request_run_concurrently_both_dicts_non_empty(monkeypatch):
    """THE key concurrency-slot regression guard (R5.1, mirrors R4.4a's own
    chat/image guard test): a chat/composer request occupies self._requests
    while a web-research request occupies the SEPARATE
    self._web_research_requests dict at the same time - neither blocks nor is
    blocked by the other, and both dicts are simultaneously non-empty at
    least once."""
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
        assert len(dispatcher._requests) == 1
        assert len(dispatcher._web_research_requests) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(dispatcher._requests.values()))
        await chat_entry["task"]
        research_release.set()
        research_entry = next(iter(dispatcher._web_research_requests.values()))
        await research_entry["task"]

        assert chat_replies == ["chat reply"]
        assert research_successes == [SimpleNamespace(answer_markdown="research result")]
        assert dispatcher._requests == {}
        assert dispatcher._web_research_requests == {}
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
        entry = next(iter(dispatcher._web_research_requests.values()))
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
        entry = next(iter(dispatcher._web_research_requests.values()))
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
        entry = next(iter(dispatcher._artifact_requests.values()))
        await entry["task"]

        assert replies == [("the new document", "an ai message")]
        assert dispatcher._artifact_requests == {}
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
        assert len(dispatcher._artifact_requests) == 1
        assert node2.pending_request_id is None, "the bounced call must never touch node2"

        release.set()
        entry = next(iter(dispatcher._artifact_requests.values()))
        await entry["task"]

        assert replies == [("first document", "first message")]
        assert dispatcher._artifact_requests == {}

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
        entry = next(iter(dispatcher._artifact_requests.values()))
        await entry["task"]

        assert on_reply_calls == [], "on_reply must never be called on a tag-parsing failure"
        assert dispatcher._artifact_requests == {}
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
        entry = next(iter(dispatcher._artifact_requests.values()))
        await entry["task"]

        assert replies == []
        assert dispatcher._artifact_requests == {}, "the slot must not leak/deadlock future requests"
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

        request_id = next(iter(dispatcher._artifact_requests.keys()))
        assert dispatcher.cancel_artifact(request_id) is True

        release.set()
        entry = next(iter(dispatcher._artifact_requests.values()))
        await entry["task"]

        assert replies == [], "on_reply must never be called once the request is cancelled"
        assert dispatcher._artifact_requests == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "Artifact generation cancelled."

    asyncio.run(run())


def test_cancel_artifact_returns_false_for_an_unknown_request_id():
    dispatcher = AgentDispatcher(_FakeSettingsManager())
    assert dispatcher.cancel_artifact("no-such-request") is False


def test_artifact_request_and_chat_request_run_concurrently_both_dicts_non_empty(monkeypatch):
    """THE key concurrency-slot regression guard (R5.2, mirrors R4.4a/R5.1's
    own chat/image and chat/web-research guards): a chat/composer request
    occupies self._requests while an artifact-generation request occupies the
    SEPARATE self._artifact_requests dict at the same time - neither blocks
    nor is blocked by the other, and both dicts are simultaneously non-empty
    at least once."""
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
        assert len(dispatcher._requests) == 1
        assert len(dispatcher._artifact_requests) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(dispatcher._requests.values()))
        await chat_entry["task"]
        artifact_release.set()
        artifact_entry = next(iter(dispatcher._artifact_requests.values()))
        await artifact_entry["task"]

        assert chat_replies == ["chat reply"]
        assert artifact_replies == [("artifact document", "artifact message")]
        assert dispatcher._requests == {}
        assert dispatcher._artifact_requests == {}
        assert composer_document.request_state == "idle"

    asyncio.run(run())


def test_artifact_request_and_web_research_request_run_concurrently(monkeypatch):
    """Mirrors test_web_research_request_and_chat_request_run_concurrently_
    both_dicts_non_empty: an artifact-generation request must also be able to
    run concurrently with a web-research request - self._artifact_requests
    and self._web_research_requests are two more genuinely independent slots,
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

        assert len(dispatcher._web_research_requests) == 1
        assert len(dispatcher._artifact_requests) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        research_release.set()
        research_entry = next(iter(dispatcher._web_research_requests.values()))
        await research_entry["task"]
        artifact_release.set()
        artifact_entry = next(iter(dispatcher._artifact_requests.values()))
        await artifact_entry["task"]

        assert research_successes == [SimpleNamespace(answer_markdown="research result")]
        assert artifact_replies == [("artifact document", "artifact message")]
        assert dispatcher._web_research_requests == {}
        assert dispatcher._artifact_requests == {}

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
    defaults = dict(
        pending_request_id=None,
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
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


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
        entry = next(iter(dispatcher._gitlink_requests.values()))
        await entry["task"]

        assert len(successes) == 1
        proposal_markdown, files, preview_text, fingerprint, local_root = successes[0]
        assert files == fake_result["files"]
        assert fingerprint == agents_module._fingerprint_changes(fake_result["files"])
        assert "octocat/hello-world" in proposal_markdown
        assert local_root == "", "the exact local_root this run used must be forwarded to on_success (FIX 2)"
        assert dispatcher._gitlink_requests == {}
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
        entry = next(iter(dispatcher._gitlink_requests.values()))
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
        entry = next(iter(dispatcher._gitlink_requests.values()))
        await entry["task"]

        assert successes == []
        assert dispatcher._gitlink_requests == {}
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
        request_id, entry = next(iter(dispatcher._gitlink_requests.items()))

        await asyncio.to_thread(started.wait, 5)
        assert dispatcher.cancel_gitlink(request_id) is True
        release.set()
        await entry["task"]

        assert successes == [], "a cancelled run must never call on_success, even on a late return"
        assert dispatcher._gitlink_requests == {}
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

        assert dispatcher._gitlink_requests == {}
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
        assert dispatcher._gitlink_apply_requests == {}, "no apply task must ever have been scheduled"

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
        # node.gitlink_change_fingerprint is left stale, still pointing at A.
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
        assert dispatcher._gitlink_apply_requests == {}

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
        # node.gitlink_pending_changes itself.
        assert pending_changes is not node.gitlink_pending_changes
        captured["frozen_content"] = pending_changes[0]["content"]
        # Mutate the LIVE node list from inside this patched function, to
        # prove the write still used the original frozen snapshot's content,
        # not whatever the live list is mutated to afterward.
        node.gitlink_pending_changes[0]["content"] = "mutated-after-freeze"
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
        entry = next(iter(dispatcher._gitlink_apply_requests.values()))
        await entry["task"]

        assert successes == [1]
        assert captured["frozen_content"] == "original", (
            "the write must use the frozen snapshot's content, unaffected by the later mutation"
        )
        assert node.gitlink_pending_changes[0]["content"] == "mutated-after-freeze"

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
        assert dispatcher._gitlink_requests == {}, "Run must refuse immediately for a busy node"

        failures = []
        await dispatcher.start_gitlink_apply(
            bus=bus, notifications_state=notifications, node=node, node_id="n1",
            client_fingerprint=fingerprint, local_root=str(tmp_path),
            on_success=lambda written_files: None, on_failure=failures.append,
        )
        assert dispatcher._gitlink_apply_requests == {}, "Apply must refuse immediately for a busy node"
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
        assert dispatcher._gitlink_apply_requests == {}

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
        assert dispatcher._gitlink_apply_requests == {}

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
        assert dispatcher._gitlink_apply_requests == {}

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
        entry = next(iter(dispatcher._gitlink_apply_requests.values()))
        await entry["task"]

        assert successes == [3]
        assert dispatcher._gitlink_apply_requests == {}
        assert node.pending_request_id is None
        assert notifications.visible is True
        assert notifications.msg_type == "info"
        assert notifications.message == "Applied 3 file changes."

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
        entry = next(iter(dispatcher._gitlink_apply_requests.values()))
        await entry["task"]

        assert failures == [f"Failed to write approved changes: {rollback_message}"]
        assert dispatcher._gitlink_apply_requests == {}
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
        entry = next(iter(dispatcher._gitlink_apply_requests.values()))
        await entry["task"]

        assert len(failures) == 1
        assert "stopped responding" in failures[0]
        assert dispatcher._gitlink_apply_requests == {}
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
        assert dispatcher._gitlink_apply_requests == {}, "no apply task must ever have been scheduled"

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
        entry = next(iter(dispatcher._gitlink_apply_requests.values()))
        await entry["task"]

        assert len(apply_calls) == 1
        assert node.gitlink_change_state == "applied"
        assert node.gitlink_pending_changes == [], "the approval must be cleared on success (FIX 1)"
        assert node.gitlink_change_fingerprint is None, "a consumed approval must never be replayable"
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
        assert dispatcher._gitlink_apply_requests == {}

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
        assert len(dispatcher._gitlink_apply_requests) == 1, (
            "only ONE Apply may ever be admitted for this node at a time"
        )
        entry = next(iter(dispatcher._gitlink_apply_requests.values()))
        await entry["task"]

        assert call_count["n"] == 1, "only ONE of the two concurrent calls may ever reach the write path"
        assert successes == [1], "the admitted call's on_success must fire exactly once"
        assert failures == [], "the rejected call is refused via the busy notification, not on_failure"
        assert dispatcher._gitlink_apply_requests == {}
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

    handler = bus._intents[("scene", "applyGitlinkChanges")]
    signature = inspect.signature(handler)
    assert list(signature.parameters) == ["node_id", "fingerprint"], (
        "applyGitlinkChanges must take ONLY (node_id, fingerprint) - no changes/pending_changes param"
    )


def test_gitlink_request_and_other_kind_request_run_concurrently(monkeypatch):
    """Mirrors the other cross-kind concurrency tests: a Gitlink Run request
    must run concurrently with (neither blocking nor blocked by) a chat/
    composer request - self._gitlink_requests and self._requests are two
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

        assert len(dispatcher._requests) == 1
        assert len(dispatcher._gitlink_requests) == 1
        assert notifications.visible is False, "neither call should have been rejected"

        chat_release.set()
        chat_entry = next(iter(dispatcher._requests.values()))
        await chat_entry["task"]
        gitlink_release.set()
        gitlink_entry = next(iter(dispatcher._gitlink_requests.values()))
        await gitlink_entry["task"]

        assert chat_replies == ["chat reply"]
        assert len(gitlink_successes) == 1
        assert dispatcher._requests == {}
        assert dispatcher._gitlink_requests == {}

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
