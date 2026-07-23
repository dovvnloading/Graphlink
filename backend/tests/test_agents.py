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
import threading
import time
from types import SimpleNamespace

import pytest

# Importing any backend.* submodule runs backend/__init__.py first, which
# puts graphlink_app/ on sys.path - these must come before the bare
# top-level graphlink_app imports below (api_provider, graphlink_task_config,
# graphlink_licensing) for this module to import cleanly when run standalone.
import backend.agents as agents_module
from backend.agents import AgentDispatcher
from backend.canvas import register_canvas
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
