"""Contract tests for the settings bridge's API page (Phase 3 increment 5) -
the provider-conditional field matrix, the atomic save-transaction order,
and the ApiModelLoadWorker status port.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtWidgets import QApplication

import api_provider
import graphlink_config as config
from graphlink_licensing import SettingsManager
from graphlink_settings_bridge import API_TASKS, SettingsBridge

_SAVE_ENV_VARS = (
    "GRAPHLINK_API_PROVIDER",
    "GRAPHLINK_OPENAI_API_KEY",
    "GRAPHLINK_API_BASE",
    "GRAPHLINK_ANTHROPIC_API_KEY",
    "GRAPHLINK_GEMINI_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_save_env_vars():
    # saveApiConfiguration() writes os.environ directly (matching the
    # original widget), bypassing monkeypatch's own auto-revert - clean up
    # around every test in this file so a successful save never leaks into
    # whatever runs next.
    for var in _SAVE_ENV_VARS:
        os.environ.pop(var, None)
    yield
    for var in _SAVE_ENV_VARS:
        os.environ.pop(var, None)


def _bridge(tmp_path) -> SettingsBridge:
    return SettingsBridge(SettingsManager(tmp_path / "session.dat"))


def _last_payload(bridge: SettingsBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def _wait_for_worker(bridge: SettingsBridge) -> None:
    """ApiModelLoadWorker's finished/error signals cross threads, so PySide6
    delivers them as a QUEUED connection - the worker thread finishing
    (wait()) does not by itself invoke the bridge's handler on the main
    thread. Something has to pump the main thread's event queue for that
    queued call to actually fire, same pattern test_command_palette_bridge.py
    already uses for its own QTimer.singleShot(0, ...) callback."""
    worker = bridge._api_worker
    if worker is not None:
        worker.wait(2000)
    QApplication.processEvents()


def _all_task_models(value: str) -> dict:
    return {task: value for task in API_TASKS}


def _valid_config(**overrides) -> str:
    base = {
        "provider": config.API_PROVIDER_OPENAI,
        "baseUrl": "https://api.openai.com/v1",
        "apiKey": "sk-test-key",
        "taskModels": _all_task_models("gpt-4o"),
    }
    base.update(overrides)
    return json.dumps(base)


class TestInitialPayload:
    def test_defaults_match_settings_manager_defaults(self, tmp_path):
        payload = _last_payload(_bridge(tmp_path))

        assert payload["apiProvider"] == "OpenAI-Compatible"
        assert payload["apiBaseUrl"] == "https://api.openai.com/v1"
        assert payload["openaiKeyConfigured"] is False
        assert payload["anthropicKeyConfigured"] is False
        assert payload["geminiKeyConfigured"] is False
        assert payload["apiTaskModels"] == {}
        assert payload["apiAvailableModels"] == []
        # OpenAI (default): no separate image list - the image field reuses
        # apiAvailableModels, so this stays empty.
        assert payload["apiImageModels"] == []
        assert payload["apiLoadStatus"] == "idle"
        assert payload["notice"] is None


class TestSetApiProvider:
    def test_switches_provider_and_republishes(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setApiProvider(config.API_PROVIDER_GEMINI)

        payload = json.loads(states[-1])
        assert payload["apiProvider"] == config.API_PROVIDER_GEMINI
        assert payload["apiAvailableModels"] == list(api_provider.GEMINI_MODELS_STATIC)

    def test_gemini_carries_the_curated_image_model_list_distinct_from_chat(self, tmp_path):
        # Regression guard for the parity gap found before the increment-9
        # flip: Gemini's image-generation field must suggest the curated
        # GEMINI_IMAGE_MODELS_STATIC, not the chat models (which would
        # silently break image generation).
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setApiProvider(config.API_PROVIDER_GEMINI)

        payload = json.loads(states[-1])
        assert payload["apiImageModels"] == list(api_provider.GEMINI_IMAGE_MODELS_STATIC)
        # The image list is genuinely distinct from the chat list.
        assert payload["apiImageModels"] != payload["apiAvailableModels"]

    def test_non_gemini_providers_have_no_separate_image_list(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setApiProvider(config.API_PROVIDER_ANTHROPIC)

        assert json.loads(states[-1])["apiImageModels"] == []

    def test_ignores_an_unrecognized_provider(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setApiProvider("Not A Real Provider")

        assert states == []

    def test_switching_provider_reads_that_providers_own_saved_models(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        settings_manager.set_api_models({config.TASK_CHAT: "claude-3-opus"}, config.API_PROVIDER_ANTHROPIC)
        bridge = SettingsBridge(settings_manager)

        bridge.setApiProvider(config.API_PROVIDER_ANTHROPIC)

        payload = _last_payload(bridge)
        assert payload["apiTaskModels"] == {config.TASK_CHAT: "claude-3-opus"}


class TestSaveApiConfiguration:
    def test_a_valid_save_persists_and_republishes(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.saveApiConfiguration(_valid_config())

        assert settings_manager.get_openai_key() == "sk-test-key"
        assert settings_manager.get_api_models()[config.TASK_CHAT] == "gpt-4o"
        payload = _last_payload(bridge)
        assert payload["openaiKeyConfigured"] is True
        assert payload["notice"] is None
        assert os.environ["GRAPHLINK_OPENAI_API_KEY"] == "sk-test-key"

    def test_anthropic_does_not_require_an_image_gen_model(self, tmp_path):
        bridge = _bridge(tmp_path)
        models = _all_task_models("claude-3-opus")
        del models[config.TASK_IMAGE_GEN]

        bridge.saveApiConfiguration(
            _valid_config(provider=config.API_PROVIDER_ANTHROPIC, baseUrl="", taskModels=models)
        )

        payload = _last_payload(bridge)
        assert payload["notice"] is None
        assert payload["anthropicKeyConfigured"] is True

    def test_missing_api_key_sets_a_notice_and_does_not_persist(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.saveApiConfiguration(_valid_config(apiKey=""))

        assert settings_manager.get_openai_key() == ""
        payload = _last_payload(bridge)
        assert payload["notice"] == "Please enter your API Key."

    def test_missing_base_url_for_openai_sets_a_notice(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.saveApiConfiguration(_valid_config(baseUrl=""))

        payload = _last_payload(bridge)
        assert payload["notice"] == "Please enter the Base URL for the OpenAI-compatible provider."

    def test_missing_a_required_task_model_sets_a_notice(self, tmp_path):
        bridge = _bridge(tmp_path)
        models = _all_task_models("gpt-4o")
        del models[config.TASK_CHART]

        bridge.saveApiConfiguration(_valid_config(taskModels=models))

        payload = _last_payload(bridge)
        assert "task_chart" in payload["notice"]

    def test_malformed_json_sets_a_notice_and_does_not_crash(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.saveApiConfiguration("not valid json{{{")

        payload = _last_payload(bridge)
        assert payload["notice"] == "Malformed configuration payload."

    def test_an_unrecognized_provider_sets_a_notice(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.saveApiConfiguration(_valid_config(provider="Not A Real Provider"))

        payload = _last_payload(bridge)
        assert payload["notice"] == "Unrecognized provider."

    def test_a_rejected_key_does_not_overwrite_the_last_known_good_profile(self, tmp_path, monkeypatch):
        # The exact invariant ApiSettingsWidget.save_settings()'s own comment
        # states: commit only after provider init succeeds.
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        bridge.saveApiConfiguration(_valid_config(apiKey="sk-good-key"))
        assert settings_manager.get_openai_key() == "sk-good-key"

        def _raise(*_args, **_kwargs):
            raise RuntimeError("rejected by provider")

        monkeypatch.setattr(api_provider, "initialize_api", _raise)

        bridge.saveApiConfiguration(_valid_config(apiKey="sk-bad-key"))

        assert settings_manager.get_openai_key() == "sk-good-key"  # unchanged
        payload = _last_payload(bridge)
        assert "rejected by provider" in payload["notice"]

    def test_saving_a_different_providers_key_leaves_other_provider_keys_untouched(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        bridge.saveApiConfiguration(_valid_config(apiKey="sk-openai-key"))

        models = _all_task_models("claude-3-opus")
        del models[config.TASK_IMAGE_GEN]
        bridge.saveApiConfiguration(
            _valid_config(provider=config.API_PROVIDER_ANTHROPIC, baseUrl="", apiKey="anthropic-key", taskModels=models)
        )

        assert settings_manager.get_openai_key() == "sk-openai-key"
        assert settings_manager.get_anthropic_key() == "anthropic-key"


class TestLoadAvailableModels:
    def test_a_successful_load_updates_status_and_available_models(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
        monkeypatch.setattr(
            api_provider,
            "get_available_model_descriptors",
            lambda: [
                api_provider.ModelDescriptor(model_id="gpt-4o", provider="OpenAI-Compatible", ready=True, available=True, source="endpoint"),
            ],
        )

        bridge.loadAvailableModels("sk-test-key")
        _wait_for_worker(bridge)

        payload = _last_payload(bridge)
        assert payload["apiLoadStatus"] == "done"
        assert "gpt-4o" in payload["apiAvailableModels"]

    def test_a_failed_load_sets_status_error_and_a_notice(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)

        def _raise(*_args, **_kwargs):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(api_provider, "initialize_api", _raise)

        bridge.loadAvailableModels("sk-test-key")
        _wait_for_worker(bridge)

        payload = _last_payload(bridge)
        assert payload["apiLoadStatus"] == "error"
        assert "connection refused" in payload["notice"]

    def test_missing_api_key_is_rejected_before_starting_a_worker(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.loadAvailableModels("")

        assert bridge._api_worker is None
        payload = _last_payload(bridge)
        assert payload["apiLoadStatus"] == "idle"
        assert payload["notice"] == "Please enter the API Key."

    def test_a_stale_result_after_switching_provider_mid_flight_is_discarded(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(api_provider, "initialize_api", lambda *a, **k: None)
        monkeypatch.setattr(
            api_provider,
            "get_available_model_descriptors",
            lambda: [
                api_provider.ModelDescriptor(model_id="gpt-4o", provider="OpenAI-Compatible", ready=True, available=True, source="endpoint"),
            ],
        )

        bridge.loadAvailableModels("sk-test-key")
        bridge.setApiProvider(config.API_PROVIDER_ANTHROPIC)  # switched before the worker finishes
        _wait_for_worker(bridge)

        payload = _last_payload(bridge)
        # The Anthropic catalog must not have been overwritten with the
        # OpenAI result that arrived for a provider no longer selected.
        assert payload["apiAvailableModels"] == []


class TestResetApiSettings:
    def test_clears_keys_and_resets_to_the_default_provider(self, tmp_path):
        bridge = _bridge(tmp_path)
        bridge.saveApiConfiguration(_valid_config())

        bridge.resetApiSettings()

        payload = _last_payload(bridge)
        assert payload["apiProvider"] == config.API_PROVIDER_OPENAI
        assert payload["openaiKeyConfigured"] is False
        assert payload["notice"] is None
