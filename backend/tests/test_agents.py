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
from backend.composer import ComposerDocument
from backend.events import SessionBus
from backend.notifications import NotificationState

import api_provider
import graphlink_task_config as config
from graphlink_licensing import SettingsManager


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
