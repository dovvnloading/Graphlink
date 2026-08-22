"""Settings dialog topic tests (Qt-removal plan R2.5d, extended R7.4a)."""

import asyncio

import ollama
import pytest
from graphlink_settings_store import SettingsManager

import api_provider
import graphlink_task_config as config
import backend.settings as settings_module
import backend.api.intents_settings_api_provider as intents_settings_api_provider_module
import backend.api.intents_settings_llama_cpp as intents_settings_llama_cpp_module
import backend.api.intents_settings_ollama as intents_settings_ollama_module
from backend import native_dialogs
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.settings import _mcp_servers_for_wire, register_settings, settings_payload


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
        "showTokenCounter",
        "enableSystemPrompt",
        "notificationPreferences",
        "githubTokenConfigured",
        "secretsEncryptedAtRest",
        "logLevel",
        "autoModelPolicy",
        "theme",
        "hasCompletedOnboarding",
    }


def test_settings_payload_reflects_real_manager_defaults(manager):
    payload = settings_payload(manager)
    # R8a: the token counter overlay is off by default - opt-in, not opt-out.
    assert payload["showTokenCounter"] is False
    assert payload["enableSystemPrompt"] is True
    assert payload["githubTokenConfigured"] is False
    assert set(payload["notificationPreferences"]) == set(SettingsManager.NOTIFICATION_TYPES)
    assert payload["logLevel"] == "INFO"
    # ADR-018 stage 18.4: "cheapest-capable" by default.
    assert payload["autoModelPolicy"] == "cheapest-capable"
    # ADR-012 stage 12.2: "system" by default - defers to prefers-color-scheme.
    assert payload["theme"] == "system"


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


def test_set_active_section_intent_updates_only_local_ui_state(manager):
    bus = SessionBus("settings-active-section-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setActiveSection", ["integrations"]))
    payload = recorder.messages[-1]["payload"]
    assert payload["activeSection"] == "integrations"


def test_set_show_token_counter_intent(manager):
    bus = SessionBus("settings-token-counter-test")
    register_settings(bus, manager)

    # Default is now False - assert the intent actually flips state (True is
    # the meaningful direction to prove; False-on-False would be a no-op
    # that could pass even if the setter never ran at all).
    assert manager.get_show_token_counter() is False
    asyncio.run(bus.dispatch_intent("app-settings", "setShowTokenCounter", [True]))
    assert manager.get_show_token_counter() is True

    asyncio.run(bus.dispatch_intent("app-settings", "setShowTokenCounter", [False]))
    assert manager.get_show_token_counter() is False


def test_set_enable_system_prompt_intent(manager):
    bus = SessionBus("settings-system-prompt-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setEnableSystemPrompt", [False]))
    assert manager.get_enable_system_prompt() is False


# -- ADR-016 stage 16.1: log-level setting -------------------------------


def test_get_log_level_defaults_to_info(manager):
    assert manager.get_log_level() == "INFO"


def test_set_log_level_persists_and_rejects_unknown_levels(manager):
    manager.set_log_level("DEBUG")
    assert manager.get_log_level() == "DEBUG"

    manager.set_log_level("not-a-real-level")
    assert manager.get_log_level() == "DEBUG"  # unchanged, not overwritten with garbage


def test_set_log_level_intent_persists_and_applies_live(manager):
    import logging

    bus = SessionBus("settings-log-level-test")
    register_settings(bus, manager)
    root_level_before = logging.getLogger().level
    try:
        asyncio.run(bus.dispatch_intent("app-settings", "setLogLevel", ["DEBUG"]))
        assert manager.get_log_level() == "DEBUG"
        assert logging.getLogger().level == logging.DEBUG
    finally:
        logging.getLogger().setLevel(root_level_before)


def test_set_log_level_intent_rejects_unknown_level_without_touching_root_logger(manager):
    import logging

    bus = SessionBus("settings-log-level-reject-test")
    register_settings(bus, manager)
    root_level_before = logging.getLogger().level
    try:
        asyncio.run(bus.dispatch_intent("app-settings", "setLogLevel", ["not-a-real-level"]))
        assert manager.get_log_level() == "INFO"  # unchanged
        assert logging.getLogger().level == root_level_before  # unchanged
    finally:
        logging.getLogger().setLevel(root_level_before)


# -- ADR-012 stage 12.2: theme setting -----------------------------------


def test_get_theme_defaults_to_system(manager):
    assert manager.get_theme() == "system"


def test_set_theme_persists_and_rejects_unknown_themes(manager):
    manager.set_theme("dark")
    assert manager.get_theme() == "dark"

    manager.set_theme("not-a-real-theme")
    assert manager.get_theme() == "dark"  # unchanged, not overwritten with garbage


def test_set_theme_intent_persists_and_republishes(manager):
    bus = SessionBus("settings-theme-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setTheme", ["light"]))
    assert manager.get_theme() == "light"


def test_set_theme_intent_rejects_unknown_theme(manager):
    bus = SessionBus("settings-theme-reject-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setTheme", ["not-a-real-theme"]))
    assert manager.get_theme() == "system"  # unchanged


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


# -- R7.3: SettingsManager.schema_version (graphlink_settings_store.py) - ported
# -- from graphlink_app/tests/test_schema_version.py's own Qt-free class.
# -- Its sibling TestChatPayloadSchemaVersion is NOT ported here: it exercises
# -- graphlink_scene.ChatScene/graphlink_session.serializers.SceneSerializer,
# -- both Qt-tainted with no backend/ equivalent to test against.


def test_a_fresh_settings_file_has_the_current_schema_version(manager):
    assert manager.get_schema_version() == SettingsManager.CURRENT_SCHEMA_VERSION


def test_an_old_settings_file_without_schema_version_is_backfilled(tmp_path):
    import json

    state_file = tmp_path / "session.dat"
    state_file.write_text(json.dumps({"show_token_counter": True}), encoding="utf-8")

    old_manager = SettingsManager(state_file)

    # Backfilled in memory immediately, the same as every other pre-existing
    # key here (show_token_counter, ...) - none of those write back to disk
    # until the next explicit set_*() call either, so schema_version
    # matching that existing pattern is correct, not a gap.
    assert old_manager.get_schema_version() == SettingsManager.CURRENT_SCHEMA_VERSION

    old_manager.set_show_token_counter(False)  # any setter call persists the whole (now-backfilled) state
    assert json.loads(state_file.read_text(encoding="utf-8"))["schema_version"] == SettingsManager.CURRENT_SCHEMA_VERSION


# -- ADR-009 stage 9.1: scattered `if 'field' not in state` backfills refactored
# -- into an explicit, ordered migration chain (graphlink_migrations.
# -- run_dict_migrations). These are before/after equivalence tests: loading an
# -- OLD-shape state dict through the new chain must land on exactly what the
# -- old scattered-if-check code produced, not just "doesn't crash".


def test_migration_chain_backfills_baseline_fields_to_documented_defaults(tmp_path):
    import json

    state_file = tmp_path / "session.dat"
    # A genuinely old file: only two of the ~35 migration-"1" (baseline)
    # fields present, everything else - including schema_version itself -
    # absent, the same shape a pre-schema-version session.dat had.
    state_file.write_text(
        json.dumps({"api_provider": "Anthropic", "current_mode": "API Endpoint"}),
        encoding="utf-8",
    )

    manager = SettingsManager(state_file)

    # Values that were already present survive untouched...
    assert manager.get_api_provider() == "Anthropic"
    assert manager.get_current_mode() == "API Endpoint"
    # ...and every missing baseline field lands on exactly the default
    # _create_initial_state() documents for a brand-new file - the retired
    # scattered if-check chain and the new migration chain must agree byte
    # for byte on these.
    assert manager.get_show_token_counter() is False
    assert manager.get_ollama_chat_model() == ""
    assert manager.get_ollama_scanned_models() == []
    assert manager.get_llama_cpp_n_ctx() == 4096
    assert manager.get_llama_cpp_n_gpu_layers() == 0
    assert manager.get_llama_cpp_n_threads() == 0
    assert manager.get_api_base_url() == "https://api.openai.com/v1"
    assert manager.get_openai_key() == ""
    assert manager.get_enable_system_prompt() is True
    assert manager.get_update_notifications_enabled() is False
    assert manager.get_notification_preferences() == {
        notification_type: True for notification_type in SettingsManager.NOTIFICATION_TYPES
    }
    assert manager.get_update_status_message() == "Automatic update checks are off."
    assert manager.get_update_status_level() == "info"
    assert manager.get_update_available() is False
    assert manager.get_schema_version() == SettingsManager.CURRENT_SCHEMA_VERSION


def test_migration_chain_applies_in_order_across_multiple_version_boundaries(tmp_path):
    # A file old enough to predate every version boundary at once: no
    # schema_version, no api_provider/api_models (migration "1", baseline),
    # no api_models_by_provider (migration "2") or
    # api_model_catalog_by_provider (migration "3"), and the pre-R8a 2-value
    # reasoning "mode" instead of the graded "level" (migration "4").
    #
    # api_provider is deliberately OMITTED (not just left at its default)
    # rather than set explicitly, so this test actually proves ordering, not
    # just presence: migration "2"'s api_models_by_provider default is keyed
    # by state['api_provider'] - which exists in the working dict ONLY
    # because migration "1" already ran and backfilled it earlier in the
    # SAME chain application. If the runner applied these out of order (or a
    # future edit reordered the migrations dict), this would key
    # api_models_by_provider by "" or crash instead of landing on the
    # documented "OpenAI-Compatible" default.
    import json

    state_file = tmp_path / "session.dat"
    state_file.write_text(
        json.dumps({
            "ollama_reasoning_mode": "Quick",
            "llama_cpp_reasoning_mode": "Thinking",
        }),
        encoding="utf-8",
    )

    manager = SettingsManager(state_file)

    # Migration "1" backfilled api_provider to its documented default...
    assert manager.get_api_provider() == "OpenAI-Compatible"
    # ...and migration "2" read that freshly-backfilled value (not a
    # missing/blank one) when it built api_models_by_provider - asserted
    # directly against the stored dict's own key, not through get_api_models'
    # provider-defaulting fallback, which would pass even if the key were
    # wrong.
    assert set(manager.state["api_models_by_provider"].keys()) == {"OpenAI-Compatible"}
    assert manager.state["api_models_by_provider"]["OpenAI-Compatible"] == {}
    # Migration "3" backfilled the empty catalog table.
    assert manager.get_api_model_catalog("OpenAI-Compatible") == []
    # Migration "4" faithfully translated the legacy 2-value mode fields
    # (Quick -> off, Thinking -> high), added the 3 new cloud fields, and
    # removed the retired legacy keys - and its own mcp_servers backfill
    # (added later, no version bump of its own - see that migration's
    # docstring) still ran too.
    assert manager.get_ollama_reasoning_level() == "off"
    assert manager.get_llama_cpp_reasoning_level() == "high"
    assert manager.get_anthropic_reasoning_level() == "off"
    assert manager.get_gemini_reasoning_level() == "off"
    assert manager.get_openai_reasoning_level() == "off"
    assert manager.get_mcp_servers() == []
    assert manager.get_schema_version() == SettingsManager.CURRENT_SCHEMA_VERSION

    assert "ollama_reasoning_mode" not in manager.state
    assert "llama_cpp_reasoning_mode" not in manager.state


def test_migration_chain_is_a_no_op_on_an_already_current_freshly_created_file(tmp_path):
    # Pin for the eager-save timing this refactor had to preserve
    # (test_migration_does_not_rewrite_when_nothing_needs_migrating in
    # test_backend_secrets_at_rest.py already covers the on-disk mtime side
    # of this from a fresh file; this covers the in-memory signal the
    # refactor derives that from) - re-running every migration function
    # against a dict _create_initial_state() already fully populated must
    # change nothing at all, not even incidentally.
    state_file = tmp_path / "session.dat"
    SettingsManager(state_file)  # creates the file fresh
    on_disk_after_create = state_file.read_text(encoding="utf-8")

    SettingsManager(state_file)  # second load - every migration is a no-op

    assert state_file.read_text(encoding="utf-8") == on_disk_after_create


# -- ADR-007 stage 7.5: MCP server configuration -----------------------------


def test_get_mcp_servers_defaults_to_an_empty_list(manager):
    assert manager.get_mcp_servers() == []


def test_a_pre_7_5_settings_file_backfills_mcp_servers_to_an_empty_list(tmp_path):
    import json

    state_file = tmp_path / "session.dat"
    state_file.write_text(json.dumps({"show_token_counter": True}), encoding="utf-8")

    old_manager = SettingsManager(state_file)
    assert old_manager.get_mcp_servers() == []


def test_set_mcp_servers_persists_and_normalizes_a_round_trip(manager):
    manager.set_mcp_servers([
        {
            "name": "fs",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "scopes": ["fs.read"],
            "approval": "always",
            "enabled_tools": ["read_file"],
            "enabled": True,
        },
    ])
    assert manager.get_mcp_servers() == [
        {
            "name": "fs",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "scopes": ["fs.read"],
            "approval": "always",
            "enabled_tools": ["read_file"],
            "enabled": True,
            "timeout": 30.0,
            "env": {},
        },
    ]

    reloaded = SettingsManager(manager.state_file)
    assert reloaded.get_mcp_servers() == manager.get_mcp_servers()


def test_set_mcp_servers_replaces_the_whole_list_not_a_merge(manager):
    manager.set_mcp_servers([{"name": "fs", "command": "npx"}])
    manager.set_mcp_servers([{"name": "git", "command": "uvx"}])
    assert [entry["name"] for entry in manager.get_mcp_servers()] == ["git"]


def test_set_mcp_servers_drops_an_entry_missing_a_name_or_command(manager):
    manager.set_mcp_servers([
        {"name": "", "command": "npx"},
        {"name": "fs"},  # no command
        {"name": "valid", "command": "npx"},
    ])
    assert [entry["name"] for entry in manager.get_mcp_servers()] == ["valid"]


def test_get_mcp_servers_tolerates_a_malformed_entry_on_disk_without_dropping_the_rest(tmp_path):
    import json

    state_file = tmp_path / "session.dat"
    state_file.write_text(
        json.dumps({"mcp_servers": ["not a dict", {"name": "ok", "command": "npx"}, {"command": "no name"}]}),
        encoding="utf-8",
    )
    manager = SettingsManager(state_file)
    assert [entry["name"] for entry in manager.get_mcp_servers()] == ["ok"]


# -- ADR-016 stage 16.2: pricing overrides -----------------------------------


def test_get_pricing_overrides_defaults_to_empty(manager):
    assert manager.get_pricing_overrides() == {}


def test_set_pricing_overrides_persists_and_normalizes_a_round_trip(manager):
    manager.set_pricing_overrides({"My-Custom-Model": {"input": 1.5, "output": 3.0}})
    assert manager.get_pricing_overrides() == {
        "my-custom-model": {"input": 1.5, "output": 3.0},  # lowercased key
    }

    reloaded = SettingsManager(manager.state_file)
    assert reloaded.get_pricing_overrides() == manager.get_pricing_overrides()


def test_set_pricing_overrides_replaces_the_whole_table_not_a_merge(manager):
    manager.set_pricing_overrides({"model-a": {"input": 1.0, "output": 1.0}})
    manager.set_pricing_overrides({"model-b": {"input": 2.0, "output": 2.0}})
    assert set(manager.get_pricing_overrides()) == {"model-b"}


def test_set_pricing_overrides_drops_malformed_entries(manager):
    manager.set_pricing_overrides({
        "": {"input": 1.0, "output": 1.0},  # empty key
        "negative": {"input": -1.0, "output": 1.0},  # negative price
        "not-a-dict": "oops",
        "valid": {"input": 1.0, "output": 2.0},
    })
    assert manager.get_pricing_overrides() == {"valid": {"input": 1.0, "output": 2.0}}


def test_get_pricing_overrides_tolerates_a_malformed_table_on_disk(tmp_path):
    import json

    state_file = tmp_path / "session.dat"
    state_file.write_text(json.dumps({"pricing_overrides": "not a dict"}), encoding="utf-8")
    manager = SettingsManager(state_file)
    assert manager.get_pricing_overrides() == {}


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
    # ADR-006 stage 6.5: loadApiModels routes through the state-free
    # list_models_for_config (a throwaway client), never initialize_api -
    # the old mock-API_PROVIDER_TYPE dance for the post-hoc stale-provider
    # guard is gone with the guard itself.
    monkeypatch.setattr(api_provider, "list_models_for_config", lambda *a, **k: ["gpt-4o"])
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

    monkeypatch.setattr(api_provider, "list_models_for_config", _boom)
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
    monkeypatch.setattr(api_provider, "list_models_for_config", lambda *a, **k: calls.append(a))
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


def test_save_api_configuration_accepts_a_text_only_endpoint_without_an_image_model(manager, monkeypatch):
    # ADR-006 stage 6.5 (H6): TASK_IMAGE_GEN is optional for EVERY provider
    # now, not just Anthropic - a text-only OpenAI-compatible endpoint
    # (vLLM, LM Studio, llama-server) has no image model to offer, and
    # image generation is capability-gated at call time instead
    # (generate_image's own "No image generation model configured" error).
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
    notifications = NotificationState()
    bus = SessionBus("settings-save-text-only-endpoint-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)

    models = _six_task_models()
    del models[config.TASK_IMAGE_GEN]
    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-test", models]
        )
    )

    assert manager.get_api_provider() == config.API_PROVIDER_OPENAI
    assert manager.get_openai_key() == "sk-test"
    assert notifications.msg_type == "success"


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
    # ADR-006 stage 6.5: a successful save flips the LIVE provider to this
    # API endpoint, so the persisted mode must follow - previously a restart
    # silently booted back into whatever mode was last persisted.
    assert manager.get_current_mode() == config.MODE_API_ENDPOINT


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

    monkeypatch.setattr(api_provider, "list_models_for_config", _boom)
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


def test_load_api_models_falls_back_to_the_saved_key_when_the_field_is_blank(manager, monkeypatch):
    # This page never sends the saved key to the client, so an already-
    # configured user opens the dialog with a blank key field. Without a
    # server-side fallback, Load is simply unusable for them - it fails
    # with "API key not configured" while the same payload reports
    # apiKeyConfigured=true. Legacy avoided this by pre-filling the field.
    seen = []
    monkeypatch.setattr(
        api_provider,
        "list_models_for_config",
        lambda provider, key, base_url=None: seen.append(key) or [],
    )
    manager.set_api_settings(config.API_PROVIDER_OPENAI, "https://x/v1", "sk-saved-openai", "", "")
    bus = SessionBus("settings-load-key-fallback-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "loadApiModels", [config.API_PROVIDER_OPENAI, "", "https://x/v1"]))

    assert seen == ["sk-saved-openai"]


def test_load_api_models_reports_a_clean_error_when_no_key_is_available_anywhere(manager, monkeypatch):
    called = []
    monkeypatch.setattr(api_provider, "list_models_for_config", lambda *a, **k: called.append(a))
    bus = SessionBus("settings-load-no-key-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "loadApiModels", [config.API_PROVIDER_OPENAI, "", "https://x/v1"]))

    assert called == []  # never contacts a provider with an empty key
    payload = recorder.messages[-1]["payload"]
    assert payload["apiCatalogStatus"] == "error"
    assert payload["apiCatalogMessage"] == "Please enter the API Key."


def test_load_api_models_requires_a_base_url_for_the_openai_compatible_provider(manager, monkeypatch):
    # Without this an empty Base URL falls through to api_provider's own
    # api.openai.com default, sending a self-hosted-proxy key to OpenAI.
    called = []
    monkeypatch.setattr(api_provider, "list_models_for_config", lambda *a, **k: called.append(a))
    bus = SessionBus("settings-load-no-base-url-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "loadApiModels", [config.API_PROVIDER_OPENAI, "sk-test", "   "]))

    assert called == []
    assert "Base URL" in recorder.messages[-1]["payload"]["apiCatalogMessage"]


def test_save_api_configuration_requires_a_base_url_for_the_openai_compatible_provider(manager):
    # Worse on the save path than the load path: an empty base_url is
    # PERSISTED, and SettingsManager.get_api_base_url's default only covers
    # a missing key, not a stored "", so every later bootstrap silently
    # re-points the saved key at api.openai.com.
    notifications = NotificationState()
    bus = SessionBus("settings-save-no-base-url-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)

    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_OPENAI, "  ", "sk-test", _six_task_models()]
        )
    )

    assert manager.get_openai_key() == ""
    assert notifications.msg_type == "warning"
    assert "Base URL" in notifications.message


def test_load_api_models_survives_non_string_wire_arguments(manager, monkeypatch):
    # Intent args arrive as raw JSON with no validation anywhere in the
    # dispatch path. Two of these used to raise straight out of the
    # handler: a non-str key hit str.replace inside the except block (where
    # its own try cannot catch it), and an unhashable provider was used
    # directly as a dict key.
    monkeypatch.setattr(
        api_provider, "list_models_for_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope"))
    )
    bus = SessionBus("settings-load-wire-types-test")
    register_settings(bus, manager)

    for bad_args in (
        [config.API_PROVIDER_OPENAI, 12345, "https://x/v1"],
        [config.API_PROVIDER_OPENAI, ["a"], "https://x/v1"],
        [config.API_PROVIDER_OPENAI, {"k": "v"}, "https://x/v1"],
        [["unhashable"], "sk-test", "https://x/v1"],
        [{"also": "unhashable"}, "sk-test", "https://x/v1"],
        [config.API_PROVIDER_OPENAI, "sk-test", 99],
    ):
        asyncio.run(bus.dispatch_intent("app-settings", "loadApiModels", bad_args))  # must not raise


def test_load_api_models_does_not_grow_catalog_state_for_unknown_providers(manager):
    # api_catalog_state is keyed by a client-supplied string; only the
    # three providers the UI can actually display may occupy a slot, or a
    # misbehaving client grows this dict without bound for the life of the
    # session.
    bus = SessionBus("settings-load-unbounded-growth-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    for i in range(50):
        asyncio.run(bus.dispatch_intent("app-settings", "loadApiModels", [f"junk-provider-{i}", "sk", "u"]))

    # Nothing was published at all for an unknown provider, and the viewed
    # provider's own state is untouched.
    asyncio.run(bus.publish("app-settings"))
    assert recorder.messages[-1]["payload"]["apiCatalogStatus"] == "idle"


def test_reset_api_settings_also_clears_the_catalog_and_snaps_the_viewed_provider_back(manager, monkeypatch):
    monkeypatch.setattr(api_provider, "list_models_for_config", lambda *a, **k: ["claude-sonnet"])
    bus = SessionBus("settings-reset-clears-catalog-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setViewingApiProvider", [config.API_PROVIDER_ANTHROPIC]))
    asyncio.run(bus.dispatch_intent("app-settings", "loadApiModels", [config.API_PROVIDER_ANTHROPIC, "sk-ant", ""]))
    assert recorder.messages[-1]["payload"]["apiCatalogStatus"] == "success"

    asyncio.run(bus.dispatch_intent("app-settings", "resetApiSettings", []))

    payload = recorder.messages[-1]["payload"]
    # "All API settings have been cleared" has to actually mean it: no
    # stale success banner, no stale catalog, and the viewed provider
    # follows the reset back to the default (as legacy's reset did).
    assert payload["apiCatalogStatus"] == "idle"
    assert payload["apiModelCatalog"] == []
    assert payload["viewingApiProvider"] == config.API_PROVIDER_OPENAI
    assert manager.get_api_model_catalog(config.API_PROVIDER_ANTHROPIC) == []


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
    # "keep as-is" providers on the event loop, OUTSIDE _manager_lock, then
    # wrote them back much later inside the locked persist - so a second,
    # concurrent saveApiConfiguration for a different provider (two browser
    # tabs share one session) committing in that window got its own
    # freshly-saved key silently reverted by this call's stale pre-read.
    #
    # The injection point matters and is the whole point of this test. An
    # earlier version of it injected the "concurrent" write inside the
    # mocked initialize_api, which sits BEFORE the racy read - so it also
    # passed against the still-racy ordering it was meant to catch (a
    # second-pass audit caught that). The write has to land AFTER the read
    # the buggy version performed and BEFORE the persist, which is exactly
    # the window run_locked() opens: patching run_locked to run the
    # concurrent commit first reproduces "another connection committed
    # while this save was between its read and its write".
    #
    # ADR-002 stage 2.7: save_api_configuration now lives in
    # backend/api/intents_settings_api_provider.py, which holds its own
    # `from backend.api._settings_shared import run_locked` name binding -
    # patching backend.settings.run_locked (or backend.api._settings_shared
    # .run_locked) would not reach it, since a `from x import y` binds a
    # separate local name in the importing module. The patch target must
    # be the module that actually calls it.
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)

    real_apply = intents_settings_api_provider_module.run_locked
    injected = {"done": False}

    def _apply_with_a_concurrent_commit_in_the_window(mutation, *args):
        if not injected["done"]:
            injected["done"] = True
            # Another connection's save lands and commits, in full, right
            # here - after our read (if the code reads too early) and
            # before our own write.
            manager.set_api_settings(config.API_PROVIDER_ANTHROPIC, "", "", "sk-concurrent-ant", "")
        return real_apply(mutation, *args)

    monkeypatch.setattr(
        intents_settings_api_provider_module, "run_locked", _apply_with_a_concurrent_commit_in_the_window
    )
    bus = SessionBus("settings-save-race-regression-test")
    register_settings(bus, manager)

    asyncio.run(
        bus.dispatch_intent(
            "app-settings", "saveApiConfiguration", [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-openai", _six_task_models()]
        )
    )

    assert injected["done"], "the concurrent commit never ran - the test no longer exercises the window"
    assert manager.get_openai_key() == "sk-openai"
    # Must NOT be reverted to "" - our own save's "keep Anthropic as-is"
    # read has to happen inside the same locked section as its write, so it
    # sees the concurrently-committed value rather than a stale earlier one.
    assert manager.get_anthropic_key() == "sk-concurrent-ant"


def test_load_api_models_never_mutates_the_live_provider_state(manager, monkeypatch):
    # ADR-006 stage 6.5 regression pin, replacing the retired F2
    # stale-provider-guard test: loadApiModels used to call initialize_api
    # just to refresh a Settings dropdown, silently repointing the
    # process's LIVE provider (that repointing race is what the F2 post-hoc
    # guard existed to detect). It now routes through
    # list_models_for_config's throwaway client, so a catalog refresh must
    # leave USE_API_MODE/API_PROVIDER_TYPE/API_CLIENT exactly as they were.
    fake_client = object()
    monkeypatch.setattr(
        api_provider, "_build_api_client", lambda provider, key, base_url=None: (fake_client, key, base_url)
    )
    monkeypatch.setattr(api_provider, "_list_models", lambda provider, client, key=None: ["gpt-4o"])
    before = (
        api_provider.USE_API_MODE,
        api_provider.API_PROVIDER_TYPE,
        api_provider.API_CLIENT,
        api_provider.API_KEY,
        api_provider.API_BASE_URL,
    )
    bus = SessionBus("settings-load-no-live-mutation-test")
    register_settings(bus, manager)

    asyncio.run(
        bus.dispatch_intent("app-settings", "loadApiModels", [config.API_PROVIDER_OPENAI, "sk-test", "https://x/v1"])
    )

    # The catalog DID refresh (the real list_models_for_config ran, through
    # the fake client)...
    assert manager.get_api_model_catalog(config.API_PROVIDER_OPENAI)[0]["model_id"] == "gpt-4o"
    # ...and the live provider globals are untouched, byte for byte.
    assert (
        api_provider.USE_API_MODE,
        api_provider.API_PROVIDER_TYPE,
        api_provider.API_CLIENT,
        api_provider.API_KEY,
        api_provider.API_BASE_URL,
    ) == before


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

    monkeypatch.setattr(api_provider, "list_models_for_config", _boom)
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


class TestApiKeySourceHelper:
    """ADR-004 stage 4.4: _api_key_source is the single source of truth for
    whether a provider's key is "stored", "environment", or "none" - a
    stored key always wins over the environment, matching every OR-chain in
    api_provider.py that tries the stored/argument key first."""

    def test_stored_wins_even_when_the_environment_is_also_set(self, monkeypatch):
        monkeypatch.setattr(api_provider, "env_api_key_configured", lambda provider: True)

        assert settings_module._api_key_source(True, config.API_PROVIDER_OPENAI) == "stored"

    def test_environment_when_nothing_is_stored_but_the_environment_is_set(self, monkeypatch):
        monkeypatch.setattr(api_provider, "env_api_key_configured", lambda provider: True)

        assert settings_module._api_key_source(False, config.API_PROVIDER_OPENAI) == "environment"

    def test_none_when_neither_is_stored_nor_set_in_the_environment(self, monkeypatch):
        monkeypatch.setattr(api_provider, "env_api_key_configured", lambda provider: False)

        assert settings_module._api_key_source(False, config.API_PROVIDER_OPENAI) == "none"

    def test_passes_the_right_provider_through_to_env_api_key_configured(self, monkeypatch):
        seen = []
        monkeypatch.setattr(api_provider, "env_api_key_configured", lambda provider: seen.append(provider) or False)

        settings_module._api_key_source(False, config.API_PROVIDER_ANTHROPIC)

        assert seen == [config.API_PROVIDER_ANTHROPIC]


def test_api_key_source_on_the_wire_reports_environment_only_when_nothing_is_stored(manager, monkeypatch):
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
    monkeypatch.setattr(
        api_provider,
        "env_api_key_configured",
        lambda provider: provider in (config.API_PROVIDER_OPENAI, config.API_PROVIDER_ANTHROPIC),
    )
    bus = SessionBus("settings-api-key-source-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(bus.publish("app-settings"))

    payload = recorder.messages[0]["payload"]
    # OpenAI/Anthropic: env is set, nothing stored -> "environment". Gemini:
    # neither -> "none".
    assert payload["apiKeySource"] == {"openai": "environment", "anthropic": "environment", "gemini": "none"}

    # Now save an OpenAI key - a stored key must flip that provider (and
    # only that provider) to "stored", even though its env var is still set.
    asyncio.run(
        bus.dispatch_intent(
            "app-settings",
            "saveApiConfiguration",
            [config.API_PROVIDER_OPENAI, "https://x/v1", "sk-test", _six_task_models()],
        )
    )
    payload = recorder.messages[-1]["payload"]
    assert payload["apiKeySource"] == {"openai": "stored", "anthropic": "environment", "gemini": "none"}


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


# -- R7.4b: Ollama settings page - reasoning mode, per-task model
# -- assignment, system model scan, model pull. "Scan Folder..." is
# -- deliberately NOT covered here (deferred alongside R7.4c's native
# -- folder-picker capability - see backend/settings.py's module
# -- docstring).
#
# graphlink_task_config.OLLAMA_MODELS is a module-level dict mutated
# IN PLACE by the real sync_ollama_task_models/set_current_model this
# file's intents call - a plain monkeypatch.setattr on an attribute a
# function later does `dict[key] = value` on would NOT be restored by
# monkeypatch's teardown (only whole-object reassignment is). Every test
# below that can trigger either function replaces the dict with a fresh
# copy first, so mutations are contained and reverted like any other
# monkeypatched value.


def _isolate_ollama_task_config(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_MODELS", dict(config.OLLAMA_MODELS))


def test_set_ollama_reasoning_level_persists_and_rejects_unknown_levels(manager):
    bus = SessionBus("settings-ollama-reasoning-level-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaReasoningLevel", ["off"]))
    assert manager.get_ollama_reasoning_level() == "off"

    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaReasoningLevel", ["not-a-real-level"]))
    assert manager.get_ollama_reasoning_level() == "off"  # unchanged, not overwritten with garbage


def test_set_ollama_reasoning_level_reapplies_live_only_when_ollama_is_the_active_provider(manager, monkeypatch):
    # Regression-shaped test for a race preempted at design time (the same
    # class the R7.4a audit found after the fact): re-applying the live
    # provider state unconditionally would forcibly switch an active
    # Anthropic/OpenAI session back to Ollama just because its reasoning
    # level changed in the background.
    calls = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: calls.append(a))

    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: False)
    bus = SessionBus("settings-ollama-reasoning-not-active-test")
    register_settings(bus, manager)
    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaReasoningLevel", ["off"]))
    assert calls == []  # Ollama isn't live - must not touch the live provider at all

    monkeypatch.setattr(api_provider, "is_local_ollama_mode", lambda: True)
    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaReasoningLevel", ["high"]))
    assert calls == [(config.LOCAL_PROVIDER_OLLAMA, {"reasoning_level": "high"})]


def test_set_ollama_model_assignment_rejects_unknown_task(manager, monkeypatch):
    _isolate_ollama_task_config(monkeypatch)
    bus = SessionBus("settings-ollama-assignment-unknown-task-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaModelAssignment", ["task_image_gen", "llava"]))

    assert manager.get_ollama_model_assignments().get("task_image_gen") is None


def test_set_ollama_model_assignment_explicit_auto_and_inherit(manager, monkeypatch):
    _isolate_ollama_task_config(monkeypatch)
    bus = SessionBus("settings-ollama-assignment-modes-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaModelAssignment", [config.TASK_TITLE, "llama3.2:3b"]))
    assert manager.get_ollama_model_assignments()[config.TASK_TITLE] == {"mode": "explicit", "model_id": "llama3.2:3b"}

    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaModelAssignment", [config.TASK_TITLE, "inherit"]))
    assert manager.get_ollama_model_assignments()[config.TASK_TITLE]["mode"] == "inherit"

    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaModelAssignment", [config.TASK_TITLE, ""]))
    assert manager.get_ollama_model_assignments()[config.TASK_TITLE]["mode"] == "auto"


def test_set_ollama_model_assignment_normalizes_inherit_to_auto_for_task_chat(manager, monkeypatch):
    # Adversarial-review finding: task_chat has no "inherit" concept (it IS
    # the base chat model) and its <select> in SettingsDialog.tsx never
    # renders that option for this one task - a stray/hand-edited "inherit"
    # for task_chat must not persist as-is, since the frontend would then
    # show a value its own <select> has no matching <option> for.
    _isolate_ollama_task_config(monkeypatch)
    bus = SessionBus("settings-ollama-assignment-chat-inherit-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaModelAssignment", [config.TASK_CHAT, "inherit"]))

    assert manager.get_ollama_model_assignments()[config.TASK_CHAT] == {"mode": "auto", "model_id": ""}


def test_set_ollama_model_assignment_sets_current_model_for_an_explicit_chat_task(manager, monkeypatch):
    _isolate_ollama_task_config(monkeypatch)
    bus = SessionBus("settings-ollama-assignment-current-model-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaModelAssignment", [config.TASK_CHAT, "qwen3:8b"]))

    assert config.OLLAMA_MODELS[config.TASK_CHAT] == "qwen3:8b"


def test_set_ollama_model_assignment_reads_and_writes_atomically_across_concurrent_calls(manager, monkeypatch):
    # Mirrors the R7.4a save_api_configuration regression test: the
    # read-modify-write of the whole assignments dict must happen inside
    # ONE locked critical section, or a concurrent assignment change for a
    # DIFFERENT task landing between this call's read and its write gets
    # silently reverted by this call's stale copy of the dict.
    _isolate_ollama_task_config(monkeypatch)

    # ADR-002 stage 2.7: set_ollama_model_assignment now lives in
    # backend/api/intents_settings_ollama.py, holding its own
    # `from backend.api._settings_shared import run_locked` binding - see
    # the api-provider atomicity test above for why the patch target has
    # to be that module, not backend.settings.
    real_apply = intents_settings_ollama_module.run_locked
    injected = {"done": False}

    def _apply_with_a_concurrent_assignment_in_the_window(mutation, *args):
        if not injected["done"]:
            injected["done"] = True
            assignments = manager.get_ollama_model_assignments()
            assignments[config.TASK_CHART] = {"mode": "explicit", "model_id": "concurrent-chart-model"}
            manager.set_ollama_model_assignments(assignments)
        return real_apply(mutation, *args)

    monkeypatch.setattr(
        intents_settings_ollama_module, "run_locked", _apply_with_a_concurrent_assignment_in_the_window
    )
    bus = SessionBus("settings-ollama-assignment-race-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setOllamaModelAssignment", [config.TASK_TITLE, "llama3.2:3b"]))

    assert injected["done"], "the concurrent write never ran - the test no longer exercises the window"
    assert manager.get_ollama_model_assignments()[config.TASK_TITLE] == {"mode": "explicit", "model_id": "llama3.2:3b"}
    # Must NOT be reverted - the concurrently-assigned chart model must
    # survive this call's own read-modify-write.
    assert manager.get_ollama_model_assignments()[config.TASK_CHART]["model_id"] == "concurrent-chart-model"


def test_scan_ollama_system_persists_results_and_reports_done(manager, monkeypatch):
    _isolate_ollama_task_config(monkeypatch)
    monkeypatch.setattr(
        api_provider,
        "scan_local_ollama_models",
        lambda scan_path: {"models": ["llama3.2:3b"], "scan_mode": "system", "scan_path": "", "locations": ["~/.ollama"]},
    )
    bus = SessionBus("settings-ollama-scan-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "scanOllamaSystem", []))

    assert manager.get_ollama_scanned_models() == ["llama3.2:3b"]
    payload = recorder.messages[-1]["payload"]
    assert payload["ollamaScanStatus"] == "done"
    assert "llama3.2:3b" in payload["ollamaScannedModels"]


def test_scan_ollama_system_reports_error_on_a_genuine_exception(manager, monkeypatch):
    def _boom(scan_path):
        raise OSError("permission denied walking manifest folder")

    monkeypatch.setattr(api_provider, "scan_local_ollama_models", _boom)
    bus = SessionBus("settings-ollama-scan-error-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "scanOllamaSystem", []))

    payload = recorder.messages[-1]["payload"]
    assert payload["ollamaScanStatus"] == "error"
    assert "permission denied" in payload["ollamaNotice"]
    assert manager.get_ollama_scanned_models() == []  # a failed scan does not persist a partial result


def test_scan_ollama_system_is_a_no_op_while_already_running(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(api_provider, "scan_local_ollama_models", lambda scan_path: calls.append(1) or {"models": []})
    bus = SessionBus("settings-ollama-scan-no-op-test")
    register_settings(bus, manager)

    async def _run():
        first = asyncio.create_task(bus.dispatch_intent("app-settings", "scanOllamaSystem", []))
        await asyncio.sleep(0)  # let the first call claim "running" before the second is dispatched
        await bus.dispatch_intent("app-settings", "scanOllamaSystem", [])
        await first

    asyncio.run(_run())
    assert len(calls) == 1


def test_scan_ollama_system_persist_failure_reports_error_and_does_not_strand_the_running_gate(manager, monkeypatch):
    # Adversarial-review finding: set_ollama_model_scan_cache does a real
    # disk write (json dump + fsync + atomic replace) that CAN fail (locked
    # file, permission denied, disk full). Before this fix, that exception
    # propagated uncaught out of the intent handler, leaving
    # ollama_scan_status["value"] stuck at "running" forever - every future
    # scanOllamaSystem call would then silently no-op via the "already
    # running" guard, with no way to recover short of restarting the app.
    _isolate_ollama_task_config(monkeypatch)
    monkeypatch.setattr(
        api_provider, "scan_local_ollama_models", lambda scan_path: {"models": ["llama3.2:3b"], "scan_mode": "system"}
    )

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(SettingsManager, "set_ollama_model_scan_cache", _boom)
    bus = SessionBus("settings-ollama-scan-persist-failure-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "scanOllamaSystem", []))

    payload = recorder.messages[-1]["payload"]
    assert payload["ollamaScanStatus"] == "error"
    assert "disk full" in payload["ollamaNotice"]

    # The gate must not be stranded - a second scan must actually run, not
    # silently no-op against a status that never left "running".
    calls = []
    monkeypatch.setattr(SettingsManager, "set_ollama_model_scan_cache", lambda *a, **k: calls.append(1))
    asyncio.run(bus.dispatch_intent("app-settings", "scanOllamaSystem", []))
    assert calls == [1]


def test_pull_ollama_model_rejects_an_empty_model_name(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(ollama, "pull", lambda name: calls.append(name))
    bus = SessionBus("settings-ollama-pull-empty-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pullOllamaModel", ["   "]))

    assert calls == []
    assert recorder.messages[-1]["payload"]["ollamaNotice"] == "Model name cannot be empty."


def test_pull_ollama_model_success_invalidates_cache_and_sets_current_model(manager, monkeypatch):
    _isolate_ollama_task_config(monkeypatch)
    monkeypatch.setattr(ollama, "pull", lambda name: None)
    invalidated = []
    monkeypatch.setattr(api_provider, "invalidate_ollama_capability_cache", lambda name: invalidated.append(name))
    bus = SessionBus("settings-ollama-pull-success-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pullOllamaModel", ["qwen3:8b"]))

    assert invalidated == ["qwen3:8b"]
    assert config.OLLAMA_MODELS[config.TASK_CHAT] == "qwen3:8b"
    payload = recorder.messages[-1]["payload"]
    assert payload["ollamaPullStatus"] == "done"
    assert payload["ollamaNotice"] == ""


@pytest.mark.parametrize(
    "raw_error,expected_fragment",
    [
        ("model 'bogus' not found", "was not found on the Ollama hub"),
        ("connection refused", "Is Ollama running?"),
        ("some other disk i/o failure", "unexpected error occurred"),
    ],
)
def test_pull_ollama_model_maps_errors_to_friendly_messages(manager, monkeypatch, raw_error, expected_fragment):
    def _boom(name):
        raise RuntimeError(raw_error)

    monkeypatch.setattr(ollama, "pull", _boom)
    bus = SessionBus("settings-ollama-pull-error-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pullOllamaModel", ["some-model"]))

    payload = recorder.messages[-1]["payload"]
    assert payload["ollamaPullStatus"] == "error"
    assert expected_fragment in payload["ollamaNotice"]


def test_pull_ollama_model_is_a_no_op_while_already_running(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(ollama, "pull", lambda name: calls.append(name))
    bus = SessionBus("settings-ollama-pull-no-op-test")
    register_settings(bus, manager)

    async def _run():
        first = asyncio.create_task(bus.dispatch_intent("app-settings", "pullOllamaModel", ["model-a"]))
        await asyncio.sleep(0)
        await bus.dispatch_intent("app-settings", "pullOllamaModel", ["model-b"])
        await first

    asyncio.run(_run())
    assert calls == ["model-a"]


def test_pull_ollama_model_persist_failure_reports_error_and_does_not_strand_the_running_gate(manager, monkeypatch):
    # Same reentrancy-gate hazard as scan_ollama_system's own persist step -
    # guarded here even though neither invalidate_ollama_capability_cache
    # nor set_current_model do disk I/O today, so a stuck "running" gate
    # can't reappear if one of them grows a fallible path later.
    _isolate_ollama_task_config(monkeypatch)
    monkeypatch.setattr(ollama, "pull", lambda name: None)

    def _boom(name):
        raise RuntimeError("cache invalidation blew up")

    monkeypatch.setattr(api_provider, "invalidate_ollama_capability_cache", _boom)
    bus = SessionBus("settings-ollama-pull-persist-failure-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pullOllamaModel", ["qwen3:8b"]))

    payload = recorder.messages[-1]["payload"]
    assert payload["ollamaPullStatus"] == "error"
    assert "cache invalidation blew up" in payload["ollamaNotice"]

    calls = []
    monkeypatch.setattr(api_provider, "invalidate_ollama_capability_cache", lambda name: calls.append(name))
    asyncio.run(bus.dispatch_intent("app-settings", "pullOllamaModel", ["qwen3:8b"]))
    assert calls == ["qwen3:8b"]


# -- R7.4c: Llama.cpp settings page (reasoning mode, runtime tunables,
# -- GGUF model scan/browse, chat/naming model paths) plus the retroactive
# -- un-defer of the Ollama page's own "Scan Folder..." button, now that
# -- native_dialogs.py exists.


def test_pick_ollama_scan_folder_scans_the_picked_folder(manager, monkeypatch):
    async def _fake_pick_folder(directory=""):
        return "C:/models/ollama"

    monkeypatch.setattr(native_dialogs, "pick_folder", _fake_pick_folder)
    monkeypatch.setattr(
        api_provider,
        "scan_local_ollama_models",
        lambda scan_path: {"models": ["llama3.2:3b"], "scan_mode": "folder", "scan_path": scan_path, "locations": [scan_path]},
    )
    bus = SessionBus("settings-pick-ollama-scan-folder-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pickOllamaScanFolder", []))

    assert manager.get_ollama_scanned_models() == ["llama3.2:3b"]
    payload = recorder.messages[-1]["payload"]
    assert payload["ollamaScanStatus"] == "done"


def test_pick_ollama_scan_folder_is_a_no_op_when_cancelled(manager, monkeypatch):
    async def _fake_pick_folder(directory=""):
        return None

    calls = []
    monkeypatch.setattr(native_dialogs, "pick_folder", _fake_pick_folder)
    monkeypatch.setattr(api_provider, "scan_local_ollama_models", lambda scan_path: calls.append(1) or {"models": []})
    bus = SessionBus("settings-pick-ollama-scan-folder-cancel-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pickOllamaScanFolder", []))

    assert calls == []
    assert recorder.messages[-1]["payload"]["ollamaScanStatus"] == "idle"


def test_pick_ollama_scan_folder_is_a_no_op_while_already_running(manager, monkeypatch):
    picker_calls = []

    async def _fake_pick_folder(directory=""):
        picker_calls.append(1)
        return "C:/models"

    monkeypatch.setattr(native_dialogs, "pick_folder", _fake_pick_folder)
    monkeypatch.setattr(api_provider, "scan_local_ollama_models", lambda scan_path: {"models": []})
    bus = SessionBus("settings-pick-ollama-scan-folder-no-op-test")
    register_settings(bus, manager)

    async def _run():
        first = asyncio.create_task(bus.dispatch_intent("app-settings", "pickOllamaScanFolder", []))
        await asyncio.sleep(0)
        await bus.dispatch_intent("app-settings", "pickOllamaScanFolder", [])
        await first

    asyncio.run(_run())
    assert len(picker_calls) == 1


def test_pick_ollama_scan_folder_dialog_failure_reports_error_and_does_not_strand_the_running_gate(manager, monkeypatch):
    # Adversarial-review finding: native_dialogs.pick_folder() itself can
    # raise (a per-platform dialog failure inside pywebview's
    # create_file_dialog - confirmed reachable, not theoretical). Before
    # this fix, that exception propagated uncaught, leaving
    # ollama_scan_status["value"] stuck at "running" forever - the same
    # reentrancy-gate hazard already fixed twice elsewhere in this file for
    # the scan/persist steps, reintroduced here via the dialog call itself.
    async def _boom(directory=""):
        raise RuntimeError("native dialog backend crashed")

    monkeypatch.setattr(native_dialogs, "pick_folder", _boom)
    bus = SessionBus("settings-pick-ollama-scan-folder-dialog-failure-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pickOllamaScanFolder", []))

    payload = recorder.messages[-1]["payload"]
    assert payload["ollamaScanStatus"] == "error"
    assert "native dialog backend crashed" in payload["ollamaNotice"]

    # The gate must not be stranded - a second call must actually reach the
    # picker again, not silently no-op against a status stuck at "running".
    calls = []

    async def _fake_pick_folder(directory=""):
        calls.append(1)
        return None

    monkeypatch.setattr(native_dialogs, "pick_folder", _fake_pick_folder)
    asyncio.run(bus.dispatch_intent("app-settings", "pickOllamaScanFolder", []))
    assert calls == [1]


def test_set_llama_cpp_reasoning_level_persists_and_rejects_unknown_levels(manager):
    bus = SessionBus("settings-llama-cpp-reasoning-level-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppReasoningLevel", ["off"]))
    assert manager.get_llama_cpp_reasoning_level() == "off"

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppReasoningLevel", ["not-a-real-level"]))
    assert manager.get_llama_cpp_reasoning_level() == "off"


def test_set_llama_cpp_reasoning_level_reapplies_live_only_when_llama_cpp_is_the_active_provider(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: calls.append(a))

    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    bus = SessionBus("settings-llama-cpp-reasoning-not-active-test")
    register_settings(bus, manager)
    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppReasoningLevel", ["off"]))
    assert calls == []

    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)
    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppReasoningLevel", ["high"]))
    assert len(calls) == 1
    assert calls[0][0] == config.LOCAL_PROVIDER_LLAMACPP
    assert calls[0][1]["reasoning_level"] == "high"


def test_set_llama_cpp_reasoning_level_reapply_failure_reports_a_notice_without_crashing(manager, monkeypatch):
    # Unlike Ollama's reasoning-level reapply, this one has a REAL failure
    # mode: initialize_local_provider re-validates chat_model_path every
    # call, which can raise if the persisted GGUF file no longer exists.
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("Llama.cpp model file was not found: C:/gone.gguf")

    monkeypatch.setattr(api_provider, "initialize_local_provider", _boom)
    bus = SessionBus("settings-llama-cpp-reasoning-reapply-failure-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppReasoningLevel", ["off"]))

    # The level is still persisted even though the live reapply failed.
    assert manager.get_llama_cpp_reasoning_level() == "off"
    payload = recorder.messages[-1]["payload"]
    assert "could not be applied to the live model" in payload["llamaCppNotice"]


def test_set_llama_cpp_chat_format_persists(manager):
    bus = SessionBus("settings-llama-cpp-chat-format-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppChatFormat", ["chatml"]))

    assert manager.get_llama_cpp_chat_format() == "chatml"


def test_set_llama_cpp_n_ctx_persists_and_rejects_non_numeric(manager):
    bus = SessionBus("settings-llama-cpp-n-ctx-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppNCtx", [8192]))
    assert manager.get_llama_cpp_n_ctx() == 8192

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppNCtx", ["not-a-number"]))
    assert manager.get_llama_cpp_n_ctx() == 8192  # unchanged, not coerced to garbage


def test_set_llama_cpp_n_gpu_layers_persists(manager):
    bus = SessionBus("settings-llama-cpp-n-gpu-layers-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppNGpuLayers", [20]))

    assert manager.get_llama_cpp_n_gpu_layers() == 20


def test_set_llama_cpp_n_threads_persists(manager):
    bus = SessionBus("settings-llama-cpp-n-threads-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppNThreads", [4]))

    assert manager.get_llama_cpp_n_threads() == 4


def test_set_llama_cpp_runtime_fields_read_and_write_atomically_across_concurrent_calls(manager, monkeypatch):
    # Mirrors R7.4b's set_ollama_model_assignment atomicity regression test:
    # set_llama_cpp_runtime requires ALL FOUR kwargs every call (no partial
    # update), so a stale pre-await read of the "current" values would
    # silently revert a concurrent change to a DIFFERENT runtime field
    # landing in the window between this call's read and its write.
    # ADR-002 stage 2.7: _set_llama_cpp_runtime_field now lives in
    # backend/api/intents_settings_llama_cpp.py, holding its own
    # `from backend.api._settings_shared import run_locked` binding - see
    # the api-provider atomicity test above for why the patch target has
    # to be that module, not backend.settings.
    real_apply = intents_settings_llama_cpp_module.run_locked
    injected = {"done": False}

    def _apply_with_a_concurrent_runtime_change_in_the_window(mutation, *args):
        if not injected["done"]:
            injected["done"] = True
            manager.set_llama_cpp_runtime(n_ctx=4096, n_gpu_layers=0, n_threads=99, chat_format="")
        return real_apply(mutation, *args)

    monkeypatch.setattr(
        intents_settings_llama_cpp_module, "run_locked", _apply_with_a_concurrent_runtime_change_in_the_window
    )
    bus = SessionBus("settings-llama-cpp-runtime-race-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppNCtx", [16384]))

    assert injected["done"], "the concurrent write never ran - the test no longer exercises the window"
    assert manager.get_llama_cpp_n_ctx() == 16384
    # Must NOT be reverted - the concurrently-set n_threads must survive.
    assert manager.get_llama_cpp_n_threads() == 99


def test_pick_llama_cpp_chat_model_file_stages_path_when_a_file_is_picked(manager, monkeypatch):
    async def _fake_pick_file(file_types=(), directory=""):
        return "C:/models/chat.gguf"

    monkeypatch.setattr(native_dialogs, "pick_file", _fake_pick_file)
    bus = SessionBus("settings-llama-cpp-pick-chat-file-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pickLlamaCppChatModelFile", []))

    assert recorder.messages[-1]["payload"]["llamaCppChatModelPath"] == "C:/models/chat.gguf"
    # Staged only - never persisted until Save.
    assert manager.get_llama_cpp_chat_model_path() == ""


def test_pick_llama_cpp_chat_model_file_does_nothing_when_the_dialog_is_cancelled(manager, monkeypatch):
    async def _fake_pick_file(file_types=(), directory=""):
        return None

    monkeypatch.setattr(native_dialogs, "pick_file", _fake_pick_file)
    bus = SessionBus("settings-llama-cpp-pick-chat-file-cancel-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pickLlamaCppChatModelFile", []))

    assert recorder.messages == []  # no publish at all when nothing was picked


def test_pick_llama_cpp_title_model_file_stages_path(manager, monkeypatch):
    async def _fake_pick_file(file_types=(), directory=""):
        return "C:/models/title.gguf"

    monkeypatch.setattr(native_dialogs, "pick_file", _fake_pick_file)
    bus = SessionBus("settings-llama-cpp-pick-title-file-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pickLlamaCppTitleModelFile", []))

    assert recorder.messages[-1]["payload"]["llamaCppTitleModelPath"] == "C:/models/title.gguf"
    assert manager.get_llama_cpp_title_model_override_path() == ""


def test_set_llama_cpp_chat_model_path_stages_without_persisting(manager):
    bus = SessionBus("settings-llama-cpp-set-chat-path-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppChatModelPath", ["C:/models/scanned.gguf"]))

    assert recorder.messages[-1]["payload"]["llamaCppChatModelPath"] == "C:/models/scanned.gguf"
    assert manager.get_llama_cpp_chat_model_path() == ""


def test_set_llama_cpp_title_model_path_stages_without_persisting(manager):
    bus = SessionBus("settings-llama-cpp-set-title-path-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppTitleModelPath", ["C:/models/scanned-title.gguf"]))

    assert recorder.messages[-1]["payload"]["llamaCppTitleModelPath"] == "C:/models/scanned-title.gguf"
    assert manager.get_llama_cpp_title_model_override_path() == ""


def test_scan_llama_cpp_system_persists_results_and_reports_done(manager, monkeypatch):
    monkeypatch.setattr(
        api_provider,
        "scan_local_llama_cpp_models",
        lambda scan_path: {"models": ["C:/models/a.gguf"], "scan_mode": "system", "scan_path": "", "locations": ["C:/models"], "truncated": False},
    )
    bus = SessionBus("settings-llama-cpp-scan-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "scanLlamaCppSystem", []))

    assert manager.get_llama_cpp_scanned_models() == ["C:/models/a.gguf"]
    payload = recorder.messages[-1]["payload"]
    assert payload["llamaCppScanStatus"] == "done"
    assert payload["llamaCppNotice"] == ""


def test_scan_llama_cpp_system_reports_truncated_scans(manager, monkeypatch):
    monkeypatch.setattr(
        api_provider,
        "scan_local_llama_cpp_models",
        lambda scan_path: {"models": [], "scan_mode": "system", "scan_path": "", "locations": [], "truncated": True},
    )
    bus = SessionBus("settings-llama-cpp-scan-truncated-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "scanLlamaCppSystem", []))

    payload = recorder.messages[-1]["payload"]
    assert payload["llamaCppScanStatus"] == "done"
    assert "stopped early" in payload["llamaCppNotice"]


def test_scan_llama_cpp_system_reports_error_on_a_genuine_exception(manager, monkeypatch):
    def _boom(scan_path):
        raise RuntimeError("Scan folder does not exist: /nope")

    monkeypatch.setattr(api_provider, "scan_local_llama_cpp_models", _boom)
    bus = SessionBus("settings-llama-cpp-scan-error-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "scanLlamaCppSystem", []))

    payload = recorder.messages[-1]["payload"]
    assert payload["llamaCppScanStatus"] == "error"
    assert "Scan folder does not exist" in payload["llamaCppNotice"]
    assert manager.get_llama_cpp_scanned_models() == []


def test_scan_llama_cpp_system_is_a_no_op_while_already_running(manager, monkeypatch):
    calls = []
    monkeypatch.setattr(
        api_provider, "scan_local_llama_cpp_models", lambda scan_path: calls.append(1) or {"models": []}
    )
    bus = SessionBus("settings-llama-cpp-scan-no-op-test")
    register_settings(bus, manager)

    async def _run():
        first = asyncio.create_task(bus.dispatch_intent("app-settings", "scanLlamaCppSystem", []))
        await asyncio.sleep(0)
        await bus.dispatch_intent("app-settings", "scanLlamaCppSystem", [])
        await first

    asyncio.run(_run())
    assert len(calls) == 1


def test_scan_llama_cpp_system_persist_failure_reports_error_and_does_not_strand_the_running_gate(manager, monkeypatch):
    monkeypatch.setattr(
        api_provider,
        "scan_local_llama_cpp_models",
        lambda scan_path: {"models": ["a.gguf"], "scan_mode": "system", "scan_path": "", "locations": []},
    )

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(SettingsManager, "set_llama_cpp_model_scan_cache", _boom)
    bus = SessionBus("settings-llama-cpp-scan-persist-failure-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "scanLlamaCppSystem", []))

    payload = recorder.messages[-1]["payload"]
    assert payload["llamaCppScanStatus"] == "error"
    assert "disk full" in payload["llamaCppNotice"]

    calls = []
    monkeypatch.setattr(SettingsManager, "set_llama_cpp_model_scan_cache", lambda *a, **k: calls.append(1))
    asyncio.run(bus.dispatch_intent("app-settings", "scanLlamaCppSystem", []))
    assert calls == [1]


def test_pick_llama_cpp_scan_folder_scans_the_picked_folder(manager, monkeypatch):
    async def _fake_pick_folder(directory=""):
        return "C:/models/gguf"

    monkeypatch.setattr(native_dialogs, "pick_folder", _fake_pick_folder)
    monkeypatch.setattr(
        api_provider,
        "scan_local_llama_cpp_models",
        lambda scan_path: {"models": ["a.gguf"], "scan_mode": "folder", "scan_path": scan_path, "locations": [scan_path]},
    )
    bus = SessionBus("settings-llama-cpp-pick-scan-folder-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pickLlamaCppScanFolder", []))

    assert manager.get_llama_cpp_scanned_models() == ["a.gguf"]
    assert recorder.messages[-1]["payload"]["llamaCppScanStatus"] == "done"


def test_pick_llama_cpp_scan_folder_is_a_no_op_when_cancelled(manager, monkeypatch):
    async def _fake_pick_folder(directory=""):
        return None

    calls = []
    monkeypatch.setattr(native_dialogs, "pick_folder", _fake_pick_folder)
    monkeypatch.setattr(api_provider, "scan_local_llama_cpp_models", lambda scan_path: calls.append(1) or {"models": []})
    bus = SessionBus("settings-llama-cpp-pick-scan-folder-cancel-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pickLlamaCppScanFolder", []))

    assert calls == []
    assert recorder.messages[-1]["payload"]["llamaCppScanStatus"] == "idle"


def test_pick_llama_cpp_scan_folder_is_a_no_op_while_already_running(manager, monkeypatch):
    picker_calls = []

    async def _fake_pick_folder(directory=""):
        picker_calls.append(1)
        return "C:/models"

    monkeypatch.setattr(native_dialogs, "pick_folder", _fake_pick_folder)
    monkeypatch.setattr(api_provider, "scan_local_llama_cpp_models", lambda scan_path: {"models": []})
    bus = SessionBus("settings-llama-cpp-pick-scan-folder-no-op-test")
    register_settings(bus, manager)

    async def _run():
        first = asyncio.create_task(bus.dispatch_intent("app-settings", "pickLlamaCppScanFolder", []))
        await asyncio.sleep(0)
        await bus.dispatch_intent("app-settings", "pickLlamaCppScanFolder", [])
        await first

    asyncio.run(_run())
    assert len(picker_calls) == 1


def test_pick_llama_cpp_scan_folder_dialog_failure_reports_error_and_does_not_strand_the_running_gate(manager, monkeypatch):
    # Same reentrancy-gate hazard fixed above for pick_ollama_scan_folder.
    async def _boom(directory=""):
        raise RuntimeError("native dialog backend crashed")

    monkeypatch.setattr(native_dialogs, "pick_folder", _boom)
    bus = SessionBus("settings-llama-cpp-pick-scan-folder-dialog-failure-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "pickLlamaCppScanFolder", []))

    payload = recorder.messages[-1]["payload"]
    assert payload["llamaCppScanStatus"] == "error"
    assert "native dialog backend crashed" in payload["llamaCppNotice"]

    calls = []

    async def _fake_pick_folder(directory=""):
        calls.append(1)
        return None

    monkeypatch.setattr(native_dialogs, "pick_folder", _fake_pick_folder)
    asyncio.run(bus.dispatch_intent("app-settings", "pickLlamaCppScanFolder", []))
    assert calls == [1]


def test_save_llama_cpp_settings_rejects_missing_chat_path(manager):
    bus = SessionBus("settings-llama-cpp-save-missing-chat-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "saveLlamaCppSettings", []))

    assert recorder.messages[-1]["payload"]["llamaCppNotice"] == "Chat Model File cannot be empty."
    assert manager.get_llama_cpp_chat_model_path() == ""


def test_save_llama_cpp_settings_rejects_a_chat_path_that_is_not_a_real_file(manager):
    bus = SessionBus("settings-llama-cpp-save-nonexistent-chat-test")
    register_settings(bus, manager)
    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppChatModelPath", ["C:/does/not/exist.gguf"]))
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "saveLlamaCppSettings", []))

    assert recorder.messages[-1]["payload"]["llamaCppNotice"] == "Chat model file was not found: C:/does/not/exist.gguf"
    assert manager.get_llama_cpp_chat_model_path() == ""


def test_save_llama_cpp_settings_rejects_a_chat_path_with_the_wrong_extension(manager, tmp_path):
    not_gguf = tmp_path / "chat.txt"
    not_gguf.write_text("not a real model")
    bus = SessionBus("settings-llama-cpp-save-wrong-ext-test")
    register_settings(bus, manager)
    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppChatModelPath", [str(not_gguf)]))
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "saveLlamaCppSettings", []))

    assert recorder.messages[-1]["payload"]["llamaCppNotice"] == "Chat Model File must point to a .gguf file."
    assert manager.get_llama_cpp_chat_model_path() == ""


def test_save_llama_cpp_settings_rejects_an_invalid_title_path_even_with_a_valid_chat_path(manager, tmp_path):
    chat_gguf = tmp_path / "chat.gguf"
    chat_gguf.write_text("fake gguf bytes")
    bus = SessionBus("settings-llama-cpp-save-invalid-title-test")
    register_settings(bus, manager)
    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppChatModelPath", [str(chat_gguf)]))
    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppTitleModelPath", ["C:/does/not/exist-title.gguf"]))
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "saveLlamaCppSettings", []))

    assert (
        recorder.messages[-1]["payload"]["llamaCppNotice"]
        == "Chat naming model file was not found: C:/does/not/exist-title.gguf"
    )
    assert manager.get_llama_cpp_chat_model_path() == ""  # nothing persisted, not even the valid chat path


def test_save_llama_cpp_settings_persists_on_success_with_an_optional_blank_title(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    chat_gguf = tmp_path / "chat.gguf"
    chat_gguf.write_text("fake gguf bytes")
    bus = SessionBus("settings-llama-cpp-save-success-test")
    register_settings(bus, manager)
    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppChatModelPath", [str(chat_gguf)]))
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "saveLlamaCppSettings", []))

    assert manager.get_llama_cpp_chat_model_path() == str(chat_gguf)
    assert manager.get_llama_cpp_title_model_override_path() == ""
    assert recorder.messages[-1]["payload"]["llamaCppNotice"] == ""


def test_save_llama_cpp_settings_reapplies_live_only_when_llama_cpp_is_the_active_provider(manager, tmp_path, monkeypatch):
    chat_gguf = tmp_path / "chat.gguf"
    chat_gguf.write_text("fake gguf bytes")
    calls = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: calls.append(a))
    bus = SessionBus("settings-llama-cpp-save-reapply-gate-test")
    register_settings(bus, manager)
    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppChatModelPath", [str(chat_gguf)]))

    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: False)
    asyncio.run(bus.dispatch_intent("app-settings", "saveLlamaCppSettings", []))
    assert calls == []
    assert manager.get_llama_cpp_chat_model_path() == str(chat_gguf)  # still persists even when not live-active

    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppChatModelPath", [str(chat_gguf)]))
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)
    asyncio.run(bus.dispatch_intent("app-settings", "saveLlamaCppSettings", []))
    assert len(calls) == 1
    assert calls[0][0] == config.LOCAL_PROVIDER_LLAMACPP
    assert calls[0][1]["chat_model_path"] == str(chat_gguf)


def test_save_llama_cpp_settings_aborts_without_persisting_when_the_live_reapply_fails(manager, tmp_path, monkeypatch):
    # The most important defect this shape has to catch: a bad live-init
    # call (e.g. an invalid GGUF file that fails to load) must not
    # persist the new paths at all - Save should be all-or-nothing.
    chat_gguf = tmp_path / "chat.gguf"
    chat_gguf.write_text("fake gguf bytes")
    monkeypatch.setattr(api_provider, "is_local_llama_cpp_mode", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("failed to load model")

    monkeypatch.setattr(api_provider, "initialize_local_provider", _boom)
    bus = SessionBus("settings-llama-cpp-save-reapply-failure-test")
    register_settings(bus, manager)
    asyncio.run(bus.dispatch_intent("app-settings", "setLlamaCppChatModelPath", [str(chat_gguf)]))
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "saveLlamaCppSettings", []))

    assert "Invalid Llama.cpp configuration" in recorder.messages[-1]["payload"]["llamaCppNotice"]
    assert manager.get_llama_cpp_chat_model_path() == ""  # NOT persisted - the whole save aborted


# -- ADR-006 stage 6.5: setProviderMode - the first runtime path back to
# -- Ollama/Llama.cpp from API mode without a restart. Routes through
# -- backend.agents.apply_provider_mode (the same three-way dispatch
# -- bootstrap_provider_state uses) and persists manager.set_current_mode.


def test_set_provider_mode_switches_to_ollama_and_persists_the_mode(manager, monkeypatch):
    applied = []
    monkeypatch.setattr(
        api_provider, "initialize_local_provider", lambda provider, settings=None, **k: applied.append((provider, settings))
    )
    manager.set_current_mode(config.MODE_API_ENDPOINT)  # simulate a running API session
    notifications = NotificationState()
    bus = SessionBus("settings-set-provider-mode-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setProviderMode", [config.MODE_OLLAMA_LOCAL]))

    # The live switch ran through apply_provider_mode's Ollama branch...
    assert applied == [(config.LOCAL_PROVIDER_OLLAMA, {"reasoning_level": manager.get_ollama_reasoning_level()})]
    # ...the choice survives a restart...
    assert manager.get_current_mode() == config.MODE_OLLAMA_LOCAL
    # ...and the session heard about both the banner and the settings state.
    assert notifications.msg_type == "success"
    assert config.MODE_OLLAMA_LOCAL in notifications.message
    assert recorder.messages[-1]["topic"] == "app-settings"


def test_set_provider_mode_rejects_an_unknown_mode_without_touching_state(manager, monkeypatch):
    applied = []
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: applied.append(a))
    monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: applied.append(a))
    notifications = NotificationState()
    bus = SessionBus("settings-set-provider-mode-unknown-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)

    asyncio.run(bus.dispatch_intent("app-settings", "setProviderMode", ["Some Nonsense Mode"]))

    assert applied == []  # no live switch was even attempted
    assert manager.get_current_mode() == config.MODE_OLLAMA_LOCAL  # the persisted default is untouched
    assert notifications.msg_type == "warning"
    assert "Some Nonsense Mode" in notifications.message


def test_set_provider_mode_failure_does_not_persist_the_mode(manager, monkeypatch):
    # A failed live switch must not change what the next restart boots into.
    def _boom(*a, **k):
        raise RuntimeError("ollama daemon unreachable")

    monkeypatch.setattr(api_provider, "initialize_local_provider", _boom)
    manager.set_current_mode(config.MODE_API_ENDPOINT)
    notifications = NotificationState()
    bus = SessionBus("settings-set-provider-mode-failure-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)

    asyncio.run(bus.dispatch_intent("app-settings", "setProviderMode", [config.MODE_OLLAMA_LOCAL]))

    assert manager.get_current_mode() == config.MODE_API_ENDPOINT  # unchanged
    assert notifications.msg_type == "error"
    assert "ollama daemon unreachable" in notifications.message


# -- ADR-012 stage 12.6: providerMode on the app-settings snapshot - the
# -- switcher's read side. setProviderMode (above) has existed since ADR-006
# -- stage 6.5 with nothing in the wire payload ever surfacing which mode was
# -- actually active, so the frontend had no way to render current state.


def test_provider_mode_is_exposed_on_the_app_settings_snapshot(manager):
    bus = SessionBus("settings-provider-mode-snapshot-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.publish("app-settings"))

    # manager.get_current_mode()'s own default (graphlink_settings_store.py).
    assert recorder.messages[-1]["payload"]["providerMode"] == config.MODE_OLLAMA_LOCAL


def test_provider_mode_on_the_snapshot_updates_after_a_successful_switch(manager, monkeypatch):
    monkeypatch.setattr(api_provider, "initialize_local_provider", lambda *a, **k: None)
    manager.set_current_mode(config.MODE_API_ENDPOINT)
    notifications = NotificationState()
    bus = SessionBus("settings-provider-mode-snapshot-switch-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setProviderMode", [config.MODE_OLLAMA_LOCAL]))

    assert recorder.messages[-1]["payload"]["providerMode"] == config.MODE_OLLAMA_LOCAL


def test_provider_mode_on_the_snapshot_is_untouched_by_a_failed_switch(manager, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("ollama daemon unreachable")

    monkeypatch.setattr(api_provider, "initialize_local_provider", _boom)
    manager.set_current_mode(config.MODE_API_ENDPOINT)
    notifications = NotificationState()
    bus = SessionBus("settings-provider-mode-snapshot-failure-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)
    recorder = Recorder()
    bus.attach(recorder)
    asyncio.run(bus.publish("app-settings"))  # seed one snapshot to inspect below

    asyncio.run(bus.dispatch_intent("app-settings", "setProviderMode", [config.MODE_OLLAMA_LOCAL]))

    # set_provider_mode's except branch (intents_settings_general.py)
    # publishes "notification", never "app-settings" - so no NEW snapshot
    # exists; the only one on record is the seed publish above, still
    # showing the untouched persisted mode.
    app_settings_messages = [m for m in recorder.messages if m["topic"] == "app-settings"]
    assert len(app_settings_messages) == 1
    assert app_settings_messages[-1]["payload"]["providerMode"] == config.MODE_API_ENDPOINT


# -- ADR-012 stage 12.6: setMcpServers - the deferred UI-write half of
# -- ADR-007 stage 7.5's MCP client (backend/mcp_client.py's own module
# -- docstring explicitly deferred this surface to ADR-012). Bulk-replace,
# -- same "whole value" persistence shape as SettingsManager.get_mcp_servers/
# -- set_mcp_servers themselves.


def test_set_mcp_servers_is_registered_on_app_settings(manager):
    bus = SessionBus("settings-set-mcp-servers-registration-test")
    register_settings(bus, manager)

    assert ("app-settings", "setMcpServers") in bus._intents


def test_set_mcp_servers_round_trips_a_valid_call(manager):
    bus = SessionBus("settings-set-mcp-servers-round-trip-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setMcpServers", [[
        {
            "name": "fs",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "scopes": ["fs.read"],
            "approval": "always",
            "enabledTools": ["read_file"],
            "enabled": True,
            "timeout": 45.0,
        },
    ]]))

    # Persisted through SettingsManager.set_mcp_servers' own snake_case shape...
    assert manager.get_mcp_servers() == [
        {
            "name": "fs",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "scopes": ["fs.read"],
            "approval": "always",
            "enabled_tools": ["read_file"],
            "enabled": True,
            "timeout": 45.0,
            "env": {},
        },
    ]
    # ...and republished on the wire in the camelCase shape the frontend reads.
    assert recorder.messages[-1]["topic"] == "app-settings"
    assert recorder.messages[-1]["payload"]["mcpServers"] == [
        {
            "name": "fs",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "scopes": ["fs.read"],
            "approval": "always",
            "enabledTools": ["read_file"],
            "enabled": True,
            "timeout": 45.0,
            # Names only - env VALUES are write-only on this wire now.
            "envKeys": [],
        },
    ]


def test_set_mcp_servers_is_a_bulk_replace_not_a_merge(manager):
    bus = SessionBus("settings-set-mcp-servers-bulk-replace-test")
    register_settings(bus, manager)
    asyncio.run(bus.dispatch_intent("app-settings", "setMcpServers", [[{"name": "fs", "command": "npx"}]]))

    asyncio.run(bus.dispatch_intent("app-settings", "setMcpServers", [[{"name": "git", "command": "uvx"}]]))

    assert [entry["name"] for entry in manager.get_mcp_servers()] == ["git"]


def test_set_mcp_servers_rejects_a_non_list_payload_without_persisting(manager):
    manager.set_mcp_servers([{"name": "fs", "command": "npx"}])  # seed a pre-existing config
    notifications = NotificationState()
    bus = SessionBus("settings-set-mcp-servers-non-list-test")
    bus.register_topic("notification", notifications.payload)
    register_settings(bus, manager, notifications)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.dispatch_intent("app-settings", "setMcpServers", [{"not": "a list"}]))

    # The seeded config survives untouched...
    assert [entry["name"] for entry in manager.get_mcp_servers()] == ["fs"]
    # ...and the rejection is surfaced, not silent.
    assert notifications.msg_type == "warning"
    assert "malformed" in notifications.message.lower()
    # No new app-settings snapshot was published - only the notification.
    assert all(m["topic"] != "app-settings" for m in recorder.messages)


def test_set_mcp_servers_rejects_a_list_containing_a_non_dict_entry(manager):
    manager.set_mcp_servers([{"name": "fs", "command": "npx"}])  # seed a pre-existing config
    bus = SessionBus("settings-set-mcp-servers-non-dict-entry-test")
    register_settings(bus, manager)

    asyncio.run(bus.dispatch_intent("app-settings", "setMcpServers", [["not a dict"]]))

    assert [entry["name"] for entry in manager.get_mcp_servers()] == ["fs"]


def test_mcp_servers_is_exposed_on_the_app_settings_snapshot(manager):
    manager.set_mcp_servers([{"name": "git", "command": "uvx", "enabled": False}])
    bus = SessionBus("settings-mcp-servers-snapshot-test")
    register_settings(bus, manager)
    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(bus.publish("app-settings"))

    servers = recorder.messages[-1]["payload"]["mcpServers"]
    assert len(servers) == 1
    assert servers[0]["name"] == "git"
    assert servers[0]["command"] == "uvx"
    assert servers[0]["enabled"] is False
    # camelCase on the wire, not SettingsManager's own snake_case.
    assert "enabledTools" in servers[0]
    assert "enabled_tools" not in servers[0]


# -- ADR-008 stage 8.6: Builder recipes ---------------------------------------

def test_get_recipes_defaults_to_an_empty_list(manager):
    assert manager.get_recipes() == []


def test_recipes_round_trip_normalized(manager):
    manager.set_recipes([
        {"name": " My recipe ", "goal": "do the thing", "steps": ["a", " b ", ""],
         "mode": "autopilot", "description": "d"},
        {"name": "", "goal": "nameless - dropped"},
        "not a dict",
        {"name": "goalless - dropped"},
        {"name": "bad mode", "goal": "g", "mode": "warp-speed"},
    ])
    recipes = manager.get_recipes()
    assert [r["name"] for r in recipes] == ["My recipe", "bad mode"]
    assert recipes[0]["steps"] == ["a", "b"]
    assert recipes[0]["mode"] == "autopilot"
    assert recipes[1]["mode"] == "copilot", "an unknown mode normalizes to copilot"


def test_set_recipes_replaces_the_whole_list(manager):
    manager.set_recipes([{"name": "one", "goal": "g"}])
    manager.set_recipes([{"name": "two", "goal": "g"}])
    assert [r["name"] for r in manager.get_recipes()] == ["two"]


# -- MCP env values are secrets: encrypted at rest, never on the wire --------


def test_mcp_env_values_are_encrypted_at_rest_and_never_sent_on_the_wire(tmp_path):
    """The regression this closes: per-server `env` was introduced to stop MCP
    servers inheriting the backend's whole environment, but the values it
    holds - a GITHUB_TOKEN, a BRAVE_API_KEY - were written to session.dat as
    plaintext JSON AND republished in every app-settings payload, while the
    API keys sitting beside them were encrypted and write-only."""
    import json as _json

    from graphlink_settings_store import SettingsManager as _SettingsManager

    state_file = tmp_path / "session.dat"
    manager = _SettingsManager(state_file)
    manager.set_mcp_servers([
        {"name": "gh", "command": "npx", "env": {"GITHUB_TOKEN": "ghp_super_secret_value"}},
    ])

    # 1. Not readable as plaintext in the settings file.
    on_disk = state_file.read_text(encoding="utf-8")
    assert "ghp_super_secret_value" not in on_disk, "MCP env value stored in plaintext"
    # The NAME is fine - it is not the secret, and Settings lists it.
    assert "GITHUB_TOKEN" in on_disk

    # 2. The spawn path still gets the real value back.
    servers = manager.get_mcp_servers()
    assert servers[0]["env"] == {"GITHUB_TOKEN": "ghp_super_secret_value"}

    # 3. The wire payload carries the name and NOT the value.
    wire = _mcp_servers_for_wire(manager)
    assert wire[0]["envKeys"] == ["GITHUB_TOKEN"]
    assert "env" not in wire[0]
    assert "ghp_super_secret_value" not in _json.dumps(wire)


def test_editing_another_server_does_not_wipe_configured_env_values(tmp_path):
    """Because the wire no longer echoes env values back, an ordinary edit -
    toggling one server's checkbox - sends the whole list WITHOUT them. That
    must mean "leave them alone", not "clear them"."""
    from graphlink_settings_store import SettingsManager as _SettingsManager

    manager = _SettingsManager(tmp_path / "session.dat")
    manager.set_mcp_servers([
        {"name": "gh", "command": "npx", "env": {"GITHUB_TOKEN": "ghp_keep_me"}},
        {"name": "fs", "command": "uvx", "env": {}},
    ])

    # Exactly what the settings page now sends when the user unticks "fs":
    # every field it knows about, and no `env` at all.
    manager.set_mcp_servers([
        {"name": "gh", "command": "npx"},
        {"name": "fs", "command": "uvx", "enabled": False},
    ])

    servers = {s["name"]: s for s in manager.get_mcp_servers()}
    assert servers["gh"]["env"] == {"GITHUB_TOKEN": "ghp_keep_me"}, "an unrelated edit erased a configured secret"
    assert servers["fs"]["enabled"] is False


def test_an_explicit_env_still_replaces_what_is_stored(tmp_path):
    """The preservation rule must not make env impossible to change or clear:
    an entry that DOES carry `env` is authoritative for that server."""
    from graphlink_settings_store import SettingsManager as _SettingsManager

    manager = _SettingsManager(tmp_path / "session.dat")
    manager.set_mcp_servers([{"name": "gh", "command": "npx", "env": {"OLD": "1"}}])

    manager.set_mcp_servers([{"name": "gh", "command": "npx", "env": {"NEW": "2"}}])
    assert manager.get_mcp_servers()[0]["env"] == {"NEW": "2"}

    manager.set_mcp_servers([{"name": "gh", "command": "npx", "env": {}}])
    assert manager.get_mcp_servers()[0]["env"] == {}
