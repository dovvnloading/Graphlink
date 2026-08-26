"""Composer/token-counter/notification tests (Qt-removal plan R2)."""

import asyncio

import pytest

import api_provider
import graphlink_task_config as config
from graphlink_settings_store import SettingsManager

from backend.composer import ComposerDocument, ComposerError, register_composer
from backend.events import SessionBus
from backend.notifications import NotificationState, register_notifications
from backend.settings import register_settings
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
    # R8a: reasoningSelection is now computed from the RESOLVED MODEL's real
    # capability (_route_supports_reasoning), not just "is this provider
    # active" - pin ollama_supports_reasoning directly so this shape
    # assertion is deterministic regardless of any other test's ambient
    # global provider state or the bare document's empty modelId.
    monkeypatch.setattr(api_provider, "ollama_supports_reasoning", lambda model: True)
    payload = ComposerDocument().payload()
    assert set(payload) == {"draft", "context", "route", "request", "capabilities"}
    assert set(payload["draft"]) == {"id", "text", "contextMode", "sendMode"}
    assert payload["capabilities"]["reasoningSelection"] is True
    # R8a: attachment staging is real now (backend/attachments.py + the
    # attachFile/removeAttachment intents), so this asserts the OPPOSITE of
    # what it used to - the old assertion was itself the encoded-as-correct
    # deferral, which is exactly the shape of bug this feature exists to fix.
    assert payload["capabilities"]["attachments"] is True
    assert payload["context"]["items"] == [], "no attachment staged yet on a fresh document"
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
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["high"])
        assert composer.reasoning_level == "high"
        payload = composer.payload()
        assert payload["route"]["reasoning"]["label"] == "High"
        with pytest.raises(ComposerError):
            await bus.dispatch_intent("app-composer", "setReasoningLevel", ["nonsense"])

    asyncio.run(run())


def test_reasoning_selection_capability_reflects_active_local_provider(monkeypatch):
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    monkeypatch.setattr(api_provider, "ollama_supports_reasoning", lambda model: True)
    assert ComposerDocument().payload()["capabilities"]["reasoningSelection"] is True

    # Same model, but Ollama says it has no reasoning mechanism for it -
    # the capability must follow the MODEL, not just "is Ollama active".
    monkeypatch.setattr(api_provider, "ollama_supports_reasoning", lambda model: False)
    assert ComposerDocument().payload()["capabilities"]["reasoningSelection"] is False


def test_reasoning_selection_capability_is_real_for_cloud_providers_too(monkeypatch):
    # R8a: this used to be hardcoded to local-only - cloud providers now show
    # the control whenever their resolved model actually supports reasoning.
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)

    document = ComposerDocument()
    document.route_reader = lambda: {
        "mode": "api",
        "provider": config.API_PROVIDER_ANTHROPIC,
        "modelId": "claude-opus-4-6",
        "modelLabel": "claude-opus-4-6",
        "modelOptions": [],
        "label": config.API_PROVIDER_ANTHROPIC,
        "available": True,
        "canChange": False,
    }
    assert document.payload()["capabilities"]["reasoningSelection"] is True

    document.route_reader = lambda: {
        "mode": "api",
        "provider": config.API_PROVIDER_ANTHROPIC,
        "modelId": "claude-2",
        "modelLabel": "claude-2",
        "modelOptions": [],
        "label": config.API_PROVIDER_ANTHROPIC,
        "available": True,
        "canChange": False,
    }
    assert document.payload()["capabilities"]["reasoningSelection"] is False


def test_set_reasoning_level_reapplies_live_when_ollama_is_active(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_api_mode", lambda: False)
    calls = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: calls.append(a))

    async def run():
        bus, document, _, _ = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["high"])

    asyncio.run(run())

    assert manager.get_ollama_reasoning_level() == "high"
    assert calls == [(config.LOCAL_PROVIDER_OLLAMA, {"reasoning_level": "high"})]


def test_set_reasoning_level_reapplies_live_when_llama_cpp_is_active(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_api_mode", lambda: False)
    calls = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: calls.append(a))

    async def run():
        bus, document, _, _ = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["off"])

    asyncio.run(run())

    assert manager.get_llama_cpp_reasoning_level() == "off"
    assert len(calls) == 1
    provider, settings = calls[0]
    assert provider == config.LOCAL_PROVIDER_LLAMACPP
    assert settings["reasoning_level"] == "off"


def test_set_reasoning_level_llama_cpp_reapply_failure_surfaces_a_notification(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_api_mode", lambda: False)

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(api_provider, "initialize_local_provider", _boom)

    async def run():
        bus, document, notifications, recorder = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["high"])
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
    assert manager.get_llama_cpp_reasoning_level() == "high"


def test_set_reasoning_level_calls_the_right_cloud_apply_function(tmp_path, monkeypatch):
    # R8a: cloud providers now genuinely apply too (reasoningSelection only
    # ever gates the CONTROL's visibility, not whether a chosen value takes
    # effect) - one call per provider, routed by settings_manager.get_api_provider().
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_api_mode", lambda: True)

    for provider_const, getter_name, setter_name in (
        (config.API_PROVIDER_ANTHROPIC, "get_anthropic_reasoning_level", "set_anthropic_reasoning_level"),
        (config.API_PROVIDER_GEMINI, "get_gemini_reasoning_level", "set_gemini_reasoning_level"),
        (config.API_PROVIDER_OPENAI, "get_openai_reasoning_level", "set_openai_reasoning_level"),
    ):
        # No single-purpose setter exists (set_api_settings also needs a
        # base_url + 3 keys this test has no use for) - this is the same
        # direct-state shortcut a bare-bones test double would use.
        manager.state["api_provider"] = provider_const
        calls = []
        monkeypatch.setattr(api_provider, setter_name, lambda level, _calls=calls: _calls.append(level))

        async def run():
            bus, document, _, _ = _make_bus_with_settings(manager)
            await bus.dispatch_intent("app-composer", "setReasoningLevel", ["medium"])

        asyncio.run(run())

        assert getattr(manager, getter_name)() == "medium", provider_const
        assert calls == ["medium"], provider_const


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
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["high"])
        publishes_after = recorder.topics_seen().count("app-composer")
        return document, publishes_before, publishes_after

    document, publishes_before, publishes_after = asyncio.run(run())

    assert document.reasoning_level == "off"
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


def test_estimate_tokens_uses_the_real_tiktoken_estimator():
    # ADR-016 stage 16.2: real BPE tokenization, not a whitespace word count.
    # These are cl100k_base's actual counts (not word counts) - verified
    # directly against graphlink_token_estimator.TokenEstimator, not assumed.
    # Multi-space runs are the clearest tell: BPE tokenizes extra whitespace
    # as its own token(s), which a word count (str.split() collapses runs)
    # would never show - "  extra   spaces  " is 2 words but 5 real tokens.
    assert estimate_tokens("") == 0
    assert estimate_tokens("one two three") == 3
    assert estimate_tokens("  extra   spaces  ") == 5


def test_token_counter_payload_totals_all_three():
    state = TokenCounterState(input_tokens=5, output_tokens=2, context_tokens=1)
    payload = state.payload()
    assert payload["totalTokens"] == 8
    # ADR-006 stage 6.8: the four estimate keys plus the real-usage keys
    # (null/False until a provider reports counts).
    assert set(payload) == {
        "inputTokens", "outputTokens", "contextTokens", "totalTokens",
        "promptTokens", "completionTokens", "usageIsReal", "estimatedCostUsd",
        "sessionPromptTokens", "sessionCompletionTokens", "sessionEstimatedCostUsd",
    }
    assert payload["promptTokens"] is None
    assert payload["usageIsReal"] is False
    assert payload["estimatedCostUsd"] is None
    assert payload["sessionPromptTokens"] == 0
    assert payload["sessionCompletionTokens"] == 0
    assert payload["sessionEstimatedCostUsd"] == 0.0


def test_token_counter_real_usage_switches_total_and_estimates_cost():
    # ADR-006 stage 6.8: real usage REPLACES the estimate total (prompt
    # already includes context+input - alternatives, not additive).
    state = TokenCounterState(input_tokens=5, output_tokens=2, context_tokens=1)
    state.set_real_usage(1_000_000, 1_000_000, provider="Anthropic Claude", model="claude-sonnet-4-5")
    payload = state.payload()
    assert payload["usageIsReal"] is True
    assert payload["promptTokens"] == 1_000_000
    assert payload["completionTokens"] == 1_000_000
    assert payload["totalTokens"] == 2_000_000  # not 2_000_008
    assert payload["estimatedCostUsd"] == 18.0  # 3.0 in + 15.0 out per MTok


def test_token_counter_new_draft_typing_resets_real_usage():
    state = TokenCounterState()
    state.set_real_usage(10, 20, provider="ollama", model="llama3:8b")
    assert state.payload()["usageIsReal"] is True
    assert state.payload()["estimatedCostUsd"] == 0.0  # local models cost nothing
    state.set_input_text("a new draft")
    payload = state.payload()
    assert payload["usageIsReal"] is False
    assert payload["estimatedCostUsd"] is None


def test_token_counter_unknown_cloud_model_has_no_cost_guess():
    from backend.token_counter import estimate_cost_usd

    assert estimate_cost_usd("OpenAI-Compatible", "totally-unknown", 100, 100) is None
    assert estimate_cost_usd("ollama", "anything", 100, 100) == 0.0
    assert estimate_cost_usd("Anthropic Claude", "claude-opus-5", None, None) is None


# -- ADR-016 stage 16.2: pricing overrides + session-cumulative totals ------


def test_estimate_cost_usd_prefers_an_exact_override_over_the_built_in_table():
    from backend.token_counter import estimate_cost_usd

    overrides = {"claude-sonnet-4-5": {"input": 1.0, "output": 2.0}}
    cost = estimate_cost_usd(
        "Anthropic Claude", "claude-sonnet-4-5", 1_000_000, 1_000_000, overrides=overrides,
    )
    assert cost == 3.0  # 1.0 + 2.0, not the built-in 3.0/15.0 claude-sonnet prices


def test_estimate_cost_usd_falls_back_to_the_built_in_table_when_no_override_matches():
    from backend.token_counter import estimate_cost_usd

    overrides = {"some-other-model": {"input": 1.0, "output": 2.0}}
    cost = estimate_cost_usd(
        "Anthropic Claude", "claude-sonnet-4-5", 1_000_000, 1_000_000, overrides=overrides,
    )
    assert cost == 18.0  # the built-in claude-sonnet price, override didn't match


def test_token_counter_state_reads_overrides_live_via_the_accessor():
    # Not a stored snapshot at construction time - a later change to
    # whatever pricing_overrides_fn returns is picked up on the next
    # set_real_usage/payload() call, matching every other live-settings read
    # in this codebase.
    overrides = {}
    state = TokenCounterState(pricing_overrides_fn=lambda: overrides)
    state.set_real_usage(1_000_000, 1_000_000, provider="Anthropic Claude", model="claude-sonnet-4-5")
    assert state.payload()["estimatedCostUsd"] == 18.0  # built-in price, no override yet

    overrides["claude-sonnet-4-5"] = {"input": 0.0, "output": 0.0}
    assert state.payload()["estimatedCostUsd"] == 0.0  # now free, override applied live


def test_session_totals_accumulate_across_replies_and_survive_a_new_draft():
    state = TokenCounterState()
    state.set_real_usage(10, 20, provider="ollama", model="llama3:8b")
    state.set_input_text("typing a new draft")  # resets prompt/completion, not session totals
    state.set_real_usage(5, 15, provider="ollama", model="llama3:8b")

    payload = state.payload()
    assert payload["sessionPromptTokens"] == 15
    assert payload["sessionCompletionTokens"] == 35


def test_session_estimated_cost_accumulates_across_replies():
    state = TokenCounterState()
    state.set_real_usage(1_000_000, 1_000_000, provider="Anthropic Claude", model="claude-sonnet-4-5")
    state.set_real_usage(1_000_000, 1_000_000, provider="Anthropic Claude", model="claude-sonnet-4-5")

    assert state.payload()["sessionEstimatedCostUsd"] == 36.0  # 18.0 twice


def test_session_totals_are_unaffected_by_a_reply_with_no_real_usage():
    state = TokenCounterState()
    state.set_real_usage(10, 20, provider="ollama", model="llama3:8b")
    state.set_real_usage(None, None)  # e.g. a provider that reports nothing

    payload = state.payload()
    assert payload["sessionPromptTokens"] == 10
    assert payload["sessionCompletionTokens"] == 20


def test_set_output_text_estimates_the_same_way_as_set_input_text():
    state = TokenCounterState()
    state.set_output_text("a four word reply")
    assert state.output_tokens == 4
    assert state.input_tokens == 0
    assert state.context_tokens == 0


def test_set_context_text_estimates_the_same_way_as_set_input_text():
    state = TokenCounterState()
    state.set_context_text("some prior branch history text")
    assert state.context_tokens == 5
    assert state.input_tokens == 0
    assert state.output_tokens == 0


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


def test_notification_show_info_intent_publishes_a_fixed_info_type():
    # The one frontend-triggerable "show" entry point (Document View's
    # empty-content guard is its first real caller) - fixed to msg_type
    # "info" regardless of what the frontend passes, since only the
    # message string travels over the wire.
    async def run():
        bus, _, _, notifications, recorder = make_bus()
        await bus.dispatch_intent("notification", "showInfo", ["No document view content is available for this node yet."])
        assert notifications.payload() == {
            "visible": True,
            "message": "No document view content is available for this node yet.",
            "msgType": "info",
        }
        assert recorder.topics_seen().count("notification") == 1

    asyncio.run(run())


def test_notification_show_error_intent_publishes_a_fixed_error_type():
    # ADR-003 stage 3.1: WsTransport.fireIntent()'s own error-recovery path
    # (web_ui/src/lib/ws/transport.ts) calls this when a request() rejects
    # with a genuine server-side {"kind":"error"} reply - fixed to msg_type
    # "error" regardless of what the frontend passes, same posture as
    # showInfo above (see notifications.py's own comment for why an
    # arbitrary wire-supplied severity is not trusted for either).
    async def run():
        bus, _, _, notifications, recorder = make_bus()
        await bus.dispatch_intent("notification", "showError", ["unknown intent: scene/bogus"])
        assert notifications.payload() == {
            "visible": True,
            "message": "unknown intent: scene/bogus",
            "msgType": "error",
        }
        assert recorder.topics_seen().count("notification") == 1

    asyncio.run(run())


# -- R7.5d follow-up: the composer must DISPLAY the same persisted reasoning
# level it writes. The first R7.5d pass wired only the write half, leaving the
# composer's own private reasoning_level as the display source - and since
# get_ollama_reasoning_level() defaults to "high" while that field defaults
# to "off", a user who had never touched the setting saw the composer report
# "Off" while every chat call really ran with reasoning enabled.


def _make_bus_with_composer_and_settings(settings_manager):
    bus = SessionBus("composer-settings-sync-test")
    counter = register_token_counter(bus)
    notifications = register_notifications(bus)
    composer = register_composer(bus, counter, settings_manager, notifications)
    register_settings(bus, settings_manager, notifications)
    recorder = Recorder()
    bus.attach(recorder)
    return bus, composer, recorder


def test_composer_reasoning_level_derives_from_the_persisted_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    manager = SettingsManager(tmp_path / "session.dat")

    # The exact startup case that was wrong: never-touched settings, whose
    # getter defaults to "high", against a composer defaulting to "off".
    assert manager.get_ollama_reasoning_level() == "high"
    _, composer, _ = _make_bus_with_composer_and_settings(manager)
    assert composer.payload()["route"]["reasoning"]["level"] == "high"
    assert composer.payload()["route"]["reasoning"]["label"] == "High"

    manager.set_ollama_reasoning_level("off")
    assert composer.payload()["route"]["reasoning"]["level"] == "off"


def test_composer_reasoning_level_follows_llama_cpp_when_llama_cpp_is_active(tmp_path, monkeypatch):
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)
    manager = SettingsManager(tmp_path / "session.dat")
    manager.set_ollama_reasoning_level("high")
    manager.set_llama_cpp_reasoning_level("off")

    _, composer, _ = _make_bus_with_composer_and_settings(manager)
    # Must read the ACTIVE provider's setting, not whichever getter is first.
    assert composer.payload()["route"]["reasoning"]["level"] == "off"


def test_bare_composer_document_still_uses_its_own_fallback_level(monkeypatch):
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    # No reader wired (no SettingsManager) - the local field is the source,
    # which is what keeps register_composer(bus, counter) usable in tests.
    document = ComposerDocument()
    assert document.payload()["route"]["reasoning"]["level"] == "off"
    document.set_reasoning_level("high")
    assert document.payload()["route"]["reasoning"]["level"] == "high"


def test_settings_reasoning_change_republishes_the_composer(tmp_path, monkeypatch):
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: None)
    manager = SettingsManager(tmp_path / "session.dat")
    manager.set_ollama_reasoning_level("off")

    async def run():
        bus, composer, recorder = _make_bus_with_composer_and_settings(manager)
        recorder.messages.clear()
        await bus.dispatch_intent("app-settings", "setOllamaReasoningLevel", ["high"])
        return composer, recorder

    composer, recorder = asyncio.run(run())
    assert composer.payload()["route"]["reasoning"]["level"] == "high"
    # Both surfaces render this value, so both have to be told to rebuild.
    assert recorder.topics_seen().count("app-settings") == 1
    assert recorder.topics_seen().count("app-composer") == 1


def test_composer_reasoning_change_republishes_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: None)
    manager = SettingsManager(tmp_path / "session.dat")

    async def run():
        bus, _, recorder = _make_bus_with_composer_and_settings(manager)
        recorder.messages.clear()
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["off"])
        return recorder

    recorder = asyncio.run(run())
    assert manager.get_ollama_reasoning_level() == "off"
    assert recorder.topics_seen().count("app-composer") == 1
    assert recorder.topics_seen().count("app-settings") == 1


def test_composer_reasoning_republish_is_skipped_when_settings_is_not_registered(tmp_path, monkeypatch):
    # The guard that keeps focused unit tests (and any future composer-only
    # bus) from tripping UnknownTopicError on the cross-topic publish.
    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: None)
    manager = SettingsManager(tmp_path / "session.dat")

    async def run():
        bus, _, _, recorder = _make_bus_with_settings(manager)  # no register_settings
        await bus.dispatch_intent("app-composer", "setReasoningLevel", ["off"])
        return recorder

    recorder = asyncio.run(run())
    assert recorder.topics_seen().count("app-composer") == 1
    assert "app-settings" not in recorder.topics_seen()


# -- R8a: real file attachments -----------------------------------------------


def test_attach_file_stages_a_real_document_and_republishes(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    source = tmp_path / "notes.txt"
    source.write_text("real attached body", encoding="utf-8")

    async def fake_pick_file(*args, **kwargs):
        return str(source)

    monkeypatch.setattr("backend.native_dialogs.pick_file", fake_pick_file)

    async def run():
        bus, composer, notifications, recorder = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "attachFile", [])
        return composer, recorder

    composer, recorder = asyncio.run(run())

    assert len(composer.staged_attachments) == 1
    staged = composer.staged_attachments[0]
    assert staged.kind == "document"
    assert staged.extracted_text == "real attached body"

    payload = composer.payload()
    assert payload["capabilities"]["attachments"] is True
    assert payload["context"]["items"] == [
        {"id": staged.id, "kind": "document", "name": "notes.txt", "byteSize": staged.byte_size,
         "contextLabel": "Text", "tokenCount": staged.token_count}
    ]
    assert payload["context"]["reviewAvailable"] is True
    assert recorder.topics_seen().count("app-composer") == 1


def test_attach_file_cancel_is_a_quiet_noop(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")

    async def fake_pick_file(*args, **kwargs):
        return None  # user cancelled the native dialog

    monkeypatch.setattr("backend.native_dialogs.pick_file", fake_pick_file)

    async def run():
        bus, composer, _, recorder = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "attachFile", [])
        return composer, recorder

    composer, recorder = asyncio.run(run())
    assert composer.staged_attachments == []
    assert recorder.topics_seen() == [], "a cancelled dialog must not publish anything"


def test_attach_file_rejection_surfaces_a_real_notification_and_stages_nothing(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    source = tmp_path / "blob.xyz"
    source.write_bytes(bytes(range(256)) * 20)  # binary garbage, unknown extension

    async def fake_pick_file(*args, **kwargs):
        return str(source)

    monkeypatch.setattr("backend.native_dialogs.pick_file", fake_pick_file)

    async def run():
        bus, composer, notifications, recorder = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "attachFile", [])
        return composer, notifications, recorder

    composer, notifications, recorder = asyncio.run(run())
    assert composer.staged_attachments == []
    assert notifications.visible and notifications.msg_type == "warning"
    assert "Unsupported file type" in notifications.message
    assert "notification" in recorder.topics_seen()
    assert "app-composer" not in recorder.topics_seen(), "a rejected file must not publish a stale composer state"


def test_remove_attachment_removes_only_the_matching_id(tmp_path, monkeypatch):
    manager = SettingsManager(tmp_path / "session.dat")
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    paths = iter([str(first), str(second)])

    async def fake_pick_file(*args, **kwargs):
        return next(paths)

    monkeypatch.setattr("backend.native_dialogs.pick_file", fake_pick_file)

    async def run():
        bus, composer, _, _ = _make_bus_with_settings(manager)
        await bus.dispatch_intent("app-composer", "attachFile", [])
        await bus.dispatch_intent("app-composer", "attachFile", [])
        keep_id = composer.staged_attachments[1].id
        remove_id = composer.staged_attachments[0].id
        await bus.dispatch_intent("app-composer", "removeAttachment", [remove_id])
        return composer, keep_id

    composer, keep_id = asyncio.run(run())
    assert [item.id for item in composer.staged_attachments] == [keep_id]
