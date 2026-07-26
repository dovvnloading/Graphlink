"""Composer/token-counter/notification tests (Qt-removal plan R2)."""

import asyncio

import pytest

import api_provider
import graphlink_task_config as config
from graphlink_licensing import SettingsManager

from backend.composer import ComposerDocument, ComposerError, register_composer
from backend.events import SessionBus
from backend.notifications import NotificationState, register_notifications
from backend.token_counter import TokenCounterState, estimate_tokens, register_token_counter


class Recorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)

    def topics_seen(self):
        return [m["topic"] for m in self.messages if m["kind"] == "state"]


def make_bus():
    bus = SessionBus("composer-test")
    counter = register_token_counter(bus)
    composer = register_composer(bus, counter)
    notifications = register_notifications(bus)
    recorder = Recorder()
    bus.attach(recorder)
    return bus, composer, counter, notifications, recorder


def _make_bus_with_settings(settings_manager):
    # R7.5d: a small, self-contained helper for the reasoning-toggle tests
    # below, which need a real SettingsManager wired all the way through
    # register_composer - kept separate from make_bus() above (which must
    # keep working exactly as it does today for the pre-existing tests).
    bus = SessionBus("composer-reasoning-test")
    counter = register_token_counter(bus)
    notifications = register_notifications(bus)
    composer = register_composer(bus, counter, settings_manager, notifications)
    recorder = Recorder()
    bus.attach(recorder)
    return bus, composer, notifications, recorder


# -- composer ----------------------------------------------------------------


def test_default_payload_matches_generated_validator_shape(monkeypatch):
    # R7.5d: reasoningSelection is now computed from live api_provider
    # module state (see test_reasoning_selection_capability_reflects_
    # active_local_provider below), not hardcoded - pin it here so this
    # shape assertion is deterministic regardless of any other test's
    # ambient global provider state.
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    payload = ComposerDocument().payload()
    assert set(payload) == {"draft", "context", "route", "request", "capabilities"}
    assert set(payload["draft"]) == {"id", "text", "contextMode", "sendMode", "restored"}
    assert payload["capabilities"]["reasoningSelection"] is True
    assert payload["capabilities"]["attachments"] is False, "attachment staging is deferred, not faked"
    assert payload["request"]["canSend"] is True, "idle state can send now that R4 agent dispatch has landed"


def test_update_draft_intent_updates_text_and_publishes_composer_and_tokens():
    async def run():
        bus, composer, counter, _, recorder = make_bus()
        await bus.dispatch_intent("app-composer", "updateDraft", ["hello there world"])
        assert composer.draft.text == "hello there world"
        assert counter.input_tokens == 3
        assert recorder.topics_seen().count("app-composer") == 1
        assert recorder.topics_seen().count("token-counter") == 1

    asyncio.run(run())


def test_set_reasoning_level_intent_updates_and_rejects_unknown():
    async def run():
        bus, composer, _, _, _ = make_bus()
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["thinking"])
        assert composer.reasoning_level == "thinking"
        payload = composer.payload()
        assert payload["route"]["reasoning"]["label"] == "Thinking Mode (Enable CoT)"
        with pytest.raises(ComposerError):
            await bus.dispatch_intent("app-composer", "setReasoningLevel", ["nonsense"])

    asyncio.run(run())


def test_reasoning_selection_capability_reflects_active_local_provider(monkeypatch):
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    assert ComposerDocument().payload()["capabilities"]["reasoningSelection"] is True

    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)
    assert ComposerDocument().payload()["capabilities"]["reasoningSelection"] is True

    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    assert ComposerDocument().payload()["capabilities"]["reasoningSelection"] is False


def test_set_reasoning_level_reapplies_live_when_ollama_is_active(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    calls = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: calls.append(a))

    async def run():
        bus, document, _, _ = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["thinking"])

    asyncio.run(run())

    assert manager.get_ollama_reasoning_mode() == "Thinking"
    assert calls == [(config.LOCAL_PROVIDER_OLLAMA, {"reasoning_mode": "Thinking"})]


def test_set_reasoning_level_reapplies_live_when_llama_cpp_is_active(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)
    calls = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: calls.append(a))

    async def run():
        bus, document, _, _ = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["quick"])

    asyncio.run(run())

    assert manager.get_llama_cpp_reasoning_mode() == "Quick"
    assert len(calls) == 1
    provider, settings = calls[0]
    assert provider == config.LOCAL_PROVIDER_LLAMACPP
    assert settings["reasoning_mode"] == "Quick"


def test_set_reasoning_level_llama_cpp_reapply_failure_surfaces_a_notification(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(api_provider, "initialize_local_provider", _boom)

    async def run():
        bus, document, notifications, recorder = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["thinking"])
        return notifications, recorder

    notifications, recorder = asyncio.run(run())

    payload = notifications.payload()
    assert payload["visible"] is True
    assert "boom" in payload["message"]
    assert recorder.topics_seen().count("notification") == 1
    # The notification path must not short-circuit before the handler's own
    # trailing publish - a future edit that early-returns after showing the
    # notification would silently stop the composer UI from ever reflecting
    # the persisted reasoning_level change.
    assert recorder.topics_seen().count("app-composer") == 1
    # Persisted despite the live-apply failure.
    assert manager.get_llama_cpp_reasoning_mode() == "Thinking"


def test_set_reasoning_level_skips_apply_in_cloud_mode(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    calls = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: calls.append(a))

    async def run():
        bus, document, _, recorder = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["thinking"])
        return document, recorder

    document, recorder = asyncio.run(run())

    assert document.reasoning_level == "thinking"
    assert calls == []
    assert recorder.topics_seen().count("app-composer") == 1


def test_set_reasoning_level_busy_guard_no_ops_even_with_settings_manager(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    calls = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: calls.append(a))

    async def run():
        bus, document, _, recorder = _make_bus_with_settings(manager)
        document.begin_request("req-1")
        publishes_before = recorder.topics_seen().count("app-composer")
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["thinking"])
        publishes_after = recorder.topics_seen().count("app-composer")
        return document, publishes_before, publishes_after

    document, publishes_before, publishes_after = asyncio.run(run())

    assert document.reasoning_level == "quick"
    assert calls == []
    assert publishes_after == publishes_before


# -- R4: request lifecycle (begin_request/end_request) ------------------------


def test_request_defaults_to_idle_and_can_send():
    document = ComposerDocument()
    request = document.payload()["request"]
    assert request == {
        "id": None,
        "state": "idle",
        "message": "",
        "canSend": True,
        "canCancel": False,
        "canRetry": False,
    }


def test_begin_request_flips_to_generating():
    document = ComposerDocument()
    document.begin_request("req-1")

    assert document.request_id == "req-1"
    assert document.request_state == "generating"

    request = document.payload()["request"]
    assert request["id"] == "req-1"
    assert request["state"] == "generating"
    assert request["canSend"] is False
    assert request["canCancel"] is True
    assert request["canRetry"] is False


def test_end_request_returns_to_idle():
    document = ComposerDocument()
    document.begin_request("req-1")
    document.end_request()

    assert document.request_id is None
    assert document.request_state == "idle"

    request = document.payload()["request"]
    assert request == {
        "id": None,
        "state": "idle",
        "message": "",
        "canSend": True,
        "canCancel": False,
        "canRetry": False,
    }


def test_capabilities_cancellation_is_a_genuine_permanent_capability():
    document = ComposerDocument()
    assert document.payload()["capabilities"]["cancellation"] is True


# -- token counter -------------------------------------------------------------


def test_estimate_tokens_is_whitespace_split():
    assert estimate_tokens("") == 0
    assert estimate_tokens("one two three") == 3
    assert estimate_tokens("  extra   spaces  ") == 2


def test_token_counter_payload_totals_all_three():
    state = TokenCounterState(input_tokens=5, output_tokens=2, context_tokens=1)
    payload = state.payload()
    assert payload["totalTokens"] == 8
    assert set(payload) == {"inputTokens", "outputTokens", "contextTokens", "totalTokens"}


# -- notifications -------------------------------------------------------------


def test_notification_show_and_dismiss():
    state = NotificationState()
    assert state.payload() == {"visible": False, "message": "", "msgType": "info"}
    state.show("Saved.", "success")
    assert state.payload() == {"visible": True, "message": "Saved.", "msgType": "success"}
    state.dismiss()
    assert state.payload()["visible"] is False


def test_notification_dismiss_intent_publishes():
    async def run():
        bus, _, _, notifications, recorder = make_bus()
        notifications.show("hi")
        await bus.dispatch_intent("notification", "dismiss", [])
        assert notifications.payload()["visible"] is False
        assert recorder.topics_seen().count("notification") == 1

    asyncio.run(run())
