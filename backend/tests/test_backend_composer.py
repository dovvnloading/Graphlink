"""Composer/token-counter/notification tests (Qt-removal plan R2)."""

import asyncio

import pytest

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


# -- composer ----------------------------------------------------------------


def test_default_payload_matches_generated_validator_shape():
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
