"""Settings dialog topic tests (Qt-removal plan R2.5d, extended R7.4a)."""

import asyncio

import pytest
from graphlink_licensing import SettingsManager

import api_provider
import graphlink_task_config as config
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.settings import register_settings, settings_payload


@pytest.fixture
def manager(tmp_path):
    return SettingsManager(tmp_path / "session.dat")


class Recorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)


def test_settings_payload_shape_matches_generated_validator_shape(manager):
    payload = settings_payload(manager)
    assert set(payload) == {
        "theme",
        "showTokenCounter",
        "enableSystemPrompt",
        "notificationPreferences",
        "githubTokenConfigured",
    }


def test_settings_payload_reflects_real_manager_defaults(manager):
    payload = settings_payload(manager)
    assert payload["theme"] == "dark"
    assert payload["showTokenCounter"] is True
    assert payload["enableSystemPrompt"] is True
    assert payload["githubTokenConfigured"] is False
    assert set(payload["notificationPreferences"]) == set(SettingsManager.NOTIFICATION_TYPES)


def test_settings_never_imports_qt():
    # A plain `assert "PySide6" not in sys.modules` is only meaningful in a
    # process where nothing else has imported PySide6 - running under the
    # full repo-wide pytest suite (alongside graphlink_app/tests' real Qt
    # widget tests), sys.modules is already contaminated regardless of what
    # this module itself imports. Only a fresh subprocess importing ONLY
    # backend.settings actually answers "does this transitively pull in Qt".
    import subprocess
    import sys as _sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [_sys.executable, "-c", "import backend.settings, sys; assert 'PySide6' not in sys.modules"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_register_settings_publishes_active_section_alongside_manager_state(manager):
    bus = SessionBus("settings-test")
    register_settings(bus, manager)

    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(bus.publish("app-settings"))
    payload = recorder.messages[0]["payload"]
    assert payload["activeSection"] == "general"
    assert payload["theme"] == "dark"


def test_set_active_section_intent_updates_only_local_ui_state(manager):
    bus = SessionBus("settings-active-section-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setActiveSection", ["integrations"]))
    payload = recorder.messages[-1]["payload"]
    assert payload["activeSection"] == "integrations"


def test_set_theme_intent_persists_to_the_real_settings_manager(manager):
    bus = SessionBus("settings-theme-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setTheme", ["light"]))
    assert manager.get_theme() == "light"
    # A fresh manager reading the same file proves it was actually persisted,
    # not just mutated in memory.
    reloaded = SettingsManager(manager.state_file)
    assert reloaded.get_theme() == "light"


def test_set_show_token_counter_intent(manager):
    bus = SessionBus("settings-token-counter-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setShowTokenCounter", [False]))
    assert manager.get_show_token_counter() is False


def test_set_enable_system_prompt_intent(manager):
    bus = SessionBus("settings-system-prompt-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setEnableSystemPrompt", [False]))
    assert manager.get_enable_system_prompt() is False


def test_set_notification_preference_intent_updates_a_single_type(manager):
    bus = SessionBus("settings-notification-pref-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setNotificationPreference", ["warning", False]))
    prefs = manager.get_notification_preferences()
    assert prefs["warning"] is False
    # Untouched types keep their default - a single-field update, not a
    # wholesale replace.
    assert prefs["info"] is True


def test_set_github_token_intent_persists_and_reports_configured(manager):
    bus = SessionBus("settings-github-token-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setGithubToken", ["ghp_abc123"]))
    assert manager.get_github_token() == "ghp_abc123"
    payload = settings_payload(manager)
    assert payload["githubTokenConfigured"] is True
    # Write-only: the payload never carries the raw token value.
    assert "ghp_abc123" not in payload.values()


def test_clear_github_token_intent(manager):
    bus = SessionBus("settings-github-token-clear-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setGithubToken", ["ghp_abc123"]))
    asyncio.run(bus.dispatch_intent("app-settings", "clearGithubToken", []))
    assert manager.get_github_token() == ""
    assert settings_payload(manager)["githubTokenConfigured"] is False


# -- R7.3: SettingsManager.schema_version (graphlink_licensing.py) - ported
# -- from graphlink_app/tests/test_schema_version.py's own Qt-free class.
# -- Its sibling TestChatPayloadSchemaVersion is NOT ported here: it exercises
# -- graphlink_scene.ChatScene/graphlink_session.serializers.SceneSerializer,
# -- both Qt-tainted with no backend/ equivalent to test against.


def test_a_fresh_settings_file_has_the_current_schema_version(manager):
    assert manager.get_schema_version() == SettingsManager.CURRENT_SCHEMA_VERSION


def test_an_old_settings_file_without_schema_version_is_backfilled(tmp_path):
    import json

    state_file = tmp_path / "session.dat"
    state_file.write_text(json.dumps({"theme": "mono"}), encoding="utf-8")

    old_manager = SettingsManager(state_file)

    # Backfilled in memory immediately, the same as every other pre-existing
    # key here (theme, show_token_counter, ...) - none of those write back to
    # disk until the next explicit set_*() call either, so schema_version
    # matching that existing pattern is correct, not a gap.
    assert old_manager.get_schema_version() == SettingsManager.CURRENT_SCHEMA_VERSION

    old_manager.set_theme("mono")  # any setter call persists the whole (now-backfilled) state
    assert json.loads(state_file.read_text(encoding="utf-8"))["schema_version"] == SettingsManager.CURRENT_SCHEMA_VERSION


# -- R7.4a: API-provider settings page. New intents on the same "app-settings"
# -- topic: setViewingApiProvider (session-local, mirrors setActiveSection),
# -- loadApiModels/saveApiConfiguration (async, wrap api_provider.py calls via
# -- asyncio.to_thread - monkeypatched below so no real network call happens),
# -- resetApiSettings. Ported/adapted from graphlink_app/tests/
# -- test_settings_bridge_api_page.py's pure-logic assertions (required-task-
# -- per-provider validation, commit-only-on-init-success ordering,
# -- key-never-in-payload invariant) - its QThread-driving TestLoadAvailable
# -- Models tests are NOT ported as-is since there is no QThread here; the
# -- same contract is covered directly via loadApiModels instead.


def _six_task_models(**overrides):
    models = {
        config.TASK_TITLE: "gpt-4o-mini",
        config.TASK_CHAT: "gpt-4o",
        config.TASK_CHART: "gpt-4o",
        config.TASK_IMAGE_GEN: "dall-e-3",
        config.TASK_WEB_VALIDATE: "gpt-4o-mini",
        config.TASK_WEB_SUMMARIZE: "gpt-4o-mini",
    }
    models.update(overrides)
    return models


def test_set_viewing_api_provider_intent_updates_only_local_ui_state(manager):
    bus = SessionBus("settings-viewing-provider-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setViewingApiProvider", [config.API_PROVIDER_ANTHROPIC]))

    payload = recorder.messages[-1]["payload"]
    assert payload["viewingApiProvider"] == config.API_PROVIDER_ANTHROPIC
    # The persisted/active provider is untouched - only Save changes this.
    assert payload["activeApiProvider"] == "OpenAI-Compatible"


def test_load_api_models_success_persists_catalog_and_publishes_status(manager, monkeypatch):
    from graphlink_model_catalog import ModelDescriptor

    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
    # initialize_api is mocked to a no-op, so it never sets the real
    # api_provider.API_PROVIDER_TYPE global the way load_api_models' own
    # post-review stale-provider guard checks against - set it directly to
    # simulate what the real call would have done.
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(
        api_provider,
        "get_available_model_descriptors",
        lambda: [ModelDescriptor(model_id="gpt-4o", provider=config.API_PROVIDER_OPENAI, capabilities=frozenset({"text"}))],
    )
    bus = SessionBus("settings-load-models-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "loadApiModels", [config.API_PROVIDER_OPENAI, "sk-test", "https://api.openai.com/v1"]
        )
    )

    payload = recorder.messages[-1]["payload"]
    assert payload["apiCatalogStatus"] == "success"
    assert manager.get_api_model_catalog(config.API_PROVIDER_OPENAI)[0]["model_id"] == "gpt-4o"


def test_load_api_models_failure_sets_error_status_and_does_not_persist(manager, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(api_provider, "initialize_api", _boom)
    bus = SessionBus("settings-load-models-error-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(
        bus.dispatch_intent("app-settings", "loadApiModels", [config.API_PROVIDER_OPENAI, "sk-test", "https://x/v1"])
    )

    payload = recorder.messages[-1]["payload"]
    assert payload["apiCatalogStatus"] == "error"
    assert "connection refused" in payload["apiCatalogMessage"]
    assert manager.get_api_model_catalog(config.API_PROVIDER_OPENAI) == []


def test_load_api_models_rejects_gemini_since_it_has_no_live_catalog(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: calls.append(a))
    bus = SessionBus("settings-load-models-gemini-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    # apiCatalogStatus is scoped per-provider (post-review fix) - it's only
    # visible in the payload for whichever provider is currently viewed, so
    # switch there first to observe Gemini's own rejection outcome.
    asyncio.run(bus.dispatch_intent("app-settings", "setViewingApiProvider", [config.API_PROVIDER_GEMINI]))
    asyncio.run(
        bus.dispatch_intent("app-settings", "loadApiModels", [config.API_PROVIDER_GEMINI, "AIza-test", ""])
    )

    assert calls == []  # never even attempted a connection, matching legacy's hidden Load button
    payload = recorder.messages[-1]["payload"]
    assert payload["apiCatalogStatus"] == "error"


def test_save_api_configuration_rejects_missing_api_key(manager):
    notifications = NotificationState()
    bus = SessionBus("settings-save-missing-key-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)

    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_OPENAI, "https://x/v1", "", _six_task_models()]
        )
    )

    assert manager.get_openai_key() == ""
    assert notifications.msg_type == "warning"
    assert "API Key" in notifications.message


def test_save_api_configuration_rejects_missing_required_model(manager):
    notifications = NotificationState()
    bus = SessionBus("settings-save-missing-model-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)

    asyncio.run(
        bus.dispatch_intent(
            "app-settings",
            "saveApiConfiguration",
            [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-test", _six_task_models(task_chat="")],
        )
    )

    assert manager.get_openai_key() == ""
    assert notifications.msg_type == "warning"
    assert config.TASK_CHAT in notifications.message


def test_save_api_configuration_anthropic_does_not_require_image_gen_task(manager, monkeypatch):
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
    bus = SessionBus("settings-save-anthropic-test")
    register_settings(bus, manager)

    models = _six_task_models()
    del models[config.TASK_IMAGE_GEN]  # Anthropic doesn't support image generation
    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_ANTHROPIC, "", "sk-ant-test", models]
        )
    )

    assert manager.get_api_provider() == config.API_PROVIDER_ANTHROPIC
    assert manager.get_anthropic_key() == "sk-ant-test"


def test_save_api_configuration_does_not_persist_when_provider_init_fails(manager):
    def _boom(*a, **k):
        raise RuntimeError("bad endpoint")

    notifications = NotificationState()
    bus = SessionBus("settings-save-init-fails-test")
    bus.register_topic("notification", notifications.payload)

    import backend.settings as settings_module

    # No monkeypatch fixture at module scope here - patch directly and undo,
    # keeping this test symmetric with the others' monkeypatch(manager)-only
    # signature while still exercising the real failure path.
    original = settings_module.api_provider.initialize_api
    settings_module.api_provider.initialize_api = _boom
    try:
        register_settings(bus, manager, notifications)
        asyncio.run(
            bus.dispatch_intent(
                "app-settings",
                "saveApiConfiguration",
                [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-test", _six_task_models()],
            )
        )
    finally:
        settings_module.api_provider.initialize_api = original

    assert manager.get_openai_key() == ""  # rejected key must not overwrite the last known-good profile
    assert notifications.msg_type == "error"
    assert "bad endpoint" in notifications.message


def test_save_api_configuration_persists_on_success_and_routes_task_models(manager, monkeypatch):
    routed = {}
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
    monkeypatch.setattr(api_provider, "set_task_model", lambda task, model: routed.__setitem__(task, model))
    notifications = NotificationState()
    bus = SessionBus("settings-save-success-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)

    models = _six_task_models()
    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-test", models]
        )
    )

    assert manager.get_api_provider() == config.API_PROVIDER_OPENAI
    assert manager.get_openai_key() == "sk-test"
    assert manager.get_api_models(config.API_PROVIDER_OPENAI) == models
    assert routed == models
    assert notifications.msg_type == "success"


def test_save_api_configuration_redacts_the_key_from_a_provider_error_message(manager):
    # Regression test: some HTTP client libraries embed request parameters
    # (including a rejected key) in their exception text. The legacy bridge
    # this page replaces redacted for exactly this reason; the original
    # R7.4a port surfaced str(exc) verbatim, re-leaking a "write-only" key
    # through the failure path this page's own module docstring says never
    # happens.
    secret = "sk-should-never-leak"

    def _boom(*a, **k):
        raise RuntimeError(f"401 unauthorized: rejected key '{secret}'")

    import backend.settings as settings_module

    original = settings_module.api_provider.initialize_api
    settings_module.api_provider.initialize_api = _boom
    notifications = NotificationState()
    bus = SessionBus("settings-save-redaction-test")
    bus.register_topic("notification", notifications.payload)
    try:
        register_settings(bus, manager, notifications)
        asyncio.run(
            bus.dispatch_intent(
                "app-settings",
                "saveApiConfiguration",
                [config.API_PROVIDER_OPENAI, "https://x/v1", secret, _six_task_models()],
            )
        )
    finally:
        settings_module.api_provider.initialize_api = original

    assert secret not in notifications.message
    assert "***" in notifications.message


def test_load_api_models_redacts_the_key_from_a_provider_error_message(manager, monkeypatch):
    secret = "sk-should-never-leak-either"

    def _boom(*a, **k):
        raise RuntimeError(f"connection refused for key {secret}")

    monkeypatch.setattr(api_provider, "initialize_api", _boom)
    bus = SessionBus("settings-load-redaction-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(
        bus.dispatch_intent("app-settings", "loadApiModels", [config.API_PROVIDER_OPENAI, secret, "https://x/v1"])
    )

    payload = recorder.messages[-1]["payload"]
    assert secret not in payload["apiCatalogMessage"]
    assert "***" in payload["apiCatalogMessage"]


def test_save_api_configuration_rejects_a_non_dict_models_argument_without_crashing(manager):
    # Regression test: a non-dict 4th argument (e.g. a WS client sending
    # null) used to raise an uncaught AttributeError from models_by_task.get
    # inside the required-task loop, bypassing this intent's own
    # validation-notice path in favor of a generic "intent failed" error.
    notifications = NotificationState()
    bus = SessionBus("settings-save-non-dict-models-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)

    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-test", None]
        )
    )

    assert manager.get_openai_key() == ""
    assert notifications.msg_type == "warning"
    assert "Please select a model" in notifications.message


def test_save_api_configuration_preserves_other_providers_keys(manager, monkeypatch):
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
    bus = SessionBus("settings-save-preserves-other-keys-test")
    register_settings(bus, manager)

    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-openai", _six_task_models()]
        )
    )
    asyncio.run(
        bus.dispatch_intent(
            "app-settings",
            "saveApiConfiguration",
            [config.API_PROVIDER_ANTHROPIC, "", "sk-ant", {k: v for k, v in _six_task_models().items() if k != config.TASK_IMAGE_GEN}],
        )
    )

    # Switching to and saving Anthropic must not clobber the previously
    # saved OpenAI key - each provider's key is independent.
    assert manager.get_openai_key() == "sk-openai"
    assert manager.get_anthropic_key() == "sk-ant"


def test_save_api_configuration_reads_other_providers_keys_atomically_with_its_own_write(manager, monkeypatch):
    # Regression test for a post-review R7.4a race: the original code read
    # manager.get_anthropic_key()/get_openai_key()/get_gemini_key() for the
    # "keep as-is" providers on the event loop, BEFORE awaiting
    # initialize_api and persisting - so a second, concurrent
    # saveApiConfiguration for a different provider (e.g. two browser tabs
    # on the same session) landing in between got its own freshly-committed
    # key silently reverted by this call's stale pre-read. The fix moved
    # that read inside _persist(), which only ever runs under _manager_lock
    # - this test simulates the interleaving by having initialize_api's own
    # mock perform the "concurrent" write, proving the later read (inside
    # _persist) picks up the fresh value rather than a stale one captured
    # earlier.
    def _simulate_concurrent_anthropic_save_during_our_own_initialize_api(*_args, **_kwargs):
        manager.set_api_settings(config.API_PROVIDER_ANTHROPIC, "", "", "sk-concurrent-ant", "")

    monkeypatch.setattr(api_provider, "initialize_api", _simulate_concurrent_anthropic_save_during_our_own_initialize_api)
    bus = SessionBus("settings-save-race-regression-test")
    register_settings(bus, manager)

    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-openai", _six_task_models()]
        )
    )

    assert manager.get_openai_key() == "sk-openai"
    # Must NOT be reverted to "" - our own save's "keep Anthropic as-is"
    # logic must see the concurrently-committed value, not a stale read
    # taken before the concurrent write happened.
    assert manager.get_anthropic_key() == "sk-concurrent-ant"


def test_catalog_state_is_isolated_per_provider(manager, monkeypatch):
    # Regression test: apiCatalogStatus/apiCatalogMessage used to be one
    # flat pair of session-local cells shared by every provider, so a load
    # result for provider A leaked onto provider B's page as soon as the
    # user switched viewingApiProvider - and B's own Load button appeared
    # disabled ("loading") whenever A's load was still in flight. Fixed by
    # keying catalog state per-provider.
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)

    def _boom(*a, **k):
        raise RuntimeError("openai endpoint unreachable")

    bus = SessionBus("settings-catalog-isolation-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    monkeypatch.setattr(api_provider, "initialize_api", _boom)
    asyncio.run(
        bus.dispatch_intent("app-settings", "loadApiModels", [config.API_PROVIDER_OPENAI, "sk-test", "https://x/v1"])
    )
    payload = recorder.messages[-1]["payload"]
    assert payload["apiCatalogStatus"] == "error"  # viewing OpenAI (the default) - sees its own failure

    asyncio.run(bus.dispatch_intent("app-settings", "setViewingApiProvider", [config.API_PROVIDER_ANTHROPIC]))
    payload = recorder.messages[-1]["payload"]
    assert payload["apiCatalogStatus"] == "idle"  # Anthropic was never touched - not OpenAI's error
    assert "openai endpoint unreachable" not in payload["apiCatalogMessage"]


def test_api_key_never_appears_in_the_settings_payload(manager, monkeypatch):
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
    bus = SessionBus("settings-save-key-write-only-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(
        bus.dispatch_intent(
            "app-settings",
            "saveApiConfiguration",
            [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-super-secret-key", _six_task_models()],
        )
    )

    payload = recorder.messages[-1]["payload"]
    assert payload["apiKeyConfigured"] == {"openai": True, "anthropic": False, "gemini": False}
    assert "sk-super-secret-key" not in str(payload)


def test_reset_api_settings_intent_clears_everything(manager, monkeypatch):
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
    bus = SessionBus("settings-reset-api-test")
    register_settings(bus, manager)
    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-test", _six_task_models()]
        )
    )

    asyncio.run(bus.dispatch_intent("app-settings", "resetApiSettings", []))

    assert manager.get_openai_key() == ""
    assert manager.get_api_provider() == "OpenAI-Compatible"
    assert manager.get_api_models("OpenAI-Compatible") == {}
