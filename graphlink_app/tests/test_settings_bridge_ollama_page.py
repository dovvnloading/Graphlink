"""Contract tests for the settings bridge's Ollama page (Phase 3 increment
6) - model-assignment semantics (inherit/auto/explicit), unavailable-model
preservation, and the scan/pull worker status ports.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

import graphlink_agents_tools
import graphlink_config as config
import graphlink_settings_workers
from graphlink_licensing import SettingsManager
from graphlink_settings_bridge import OLLAMA_TASKS, SettingsBridge


@pytest.fixture(autouse=True)
def _restore_ollama_globals():
    # config.OLLAMA_MODELS/CURRENT_MODEL are process-global module state -
    # setOllamaModelAssignment/pullOllamaModel mutate them via
    # config.set_current_model()/sync_ollama_task_models(), same class of
    # leak test_theme_tokens.py's own save/restore convention already
    # guards against for config.CURRENT_THEME.
    original_models = dict(config.OLLAMA_MODELS)
    original_current_model = config.CURRENT_MODEL
    yield
    config.OLLAMA_MODELS.clear()
    config.OLLAMA_MODELS.update(original_models)
    config.CURRENT_MODEL = original_current_model


def _bridge(tmp_path) -> SettingsBridge:
    return SettingsBridge(SettingsManager(tmp_path / "session.dat"))


def _last_payload(bridge: SettingsBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def _wait_for_worker(worker) -> None:
    if worker is not None:
        worker.wait(2000)
    QApplication.processEvents()


class TestInitialPayload:
    def test_defaults(self, tmp_path):
        payload = _last_payload(_bridge(tmp_path))

        assert payload["ollamaReasoningMode"] == "Thinking"
        assert payload["ollamaCurrentModel"] == ""
        assert payload["ollamaModelAssignments"] == {
            "task_chat": "auto",
            "task_title": "inherit",
            "task_chart": "inherit",
            "task_web_validate": "inherit",
            "task_web_summarize": "inherit",
        }
        assert payload["ollamaScannedModels"] == []
        assert payload["ollamaScanSummary"].startswith("No saved scan yet")
        assert payload["ollamaScanStatus"] == "idle"
        assert payload["ollamaPullStatus"] == "idle"


class TestSetOllamaReasoningMode:
    def test_valid_mode_persists_and_publishes(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setOllamaReasoningMode("Quick")

        assert settings_manager.get_ollama_reasoning_mode() == "Quick"
        assert _last_payload(bridge)["ollamaReasoningMode"] == "Quick"

    def test_invalid_mode_is_ignored(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setOllamaReasoningMode("Not A Real Mode")

        assert states == []

    def test_reasoning_change_reinitializes_the_running_agent(self, tmp_path):
        # Regression guard for the parity gap found before the increment-9
        # flip: reasoning mode feeds the agent's system prompt, so it must
        # take effect live (legacy Ollama save called reinitialize_agent).
        class _FakeMainWindow:
            def __init__(self):
                self.reinit_calls = 0

            def reinitialize_agent(self):
                self.reinit_calls += 1

        main_window = _FakeMainWindow()
        bridge = SettingsBridge(SettingsManager(tmp_path / "session.dat"), main_window=main_window)

        bridge.setOllamaReasoningMode("Quick")

        assert main_window.reinit_calls == 1


class TestSetOllamaModelAssignment:
    def test_explicit_value_persists_and_resolves_chat(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.setOllamaModelAssignment(config.TASK_CHAT, "llama3:8b")

        payload = _last_payload(bridge)
        assert payload["ollamaModelAssignments"]["task_chat"] == "llama3:8b"
        assert payload["ollamaCurrentModel"] == "llama3:8b"
        assert config.OLLAMA_MODELS[config.TASK_CHAT] == "llama3:8b"

    def test_an_unavailable_explicit_model_is_preserved_verbatim(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.setOllamaModelAssignment(config.TASK_CHART, "not-a-scanned-model")

        payload = _last_payload(bridge)
        assert payload["ollamaModelAssignments"]["task_chart"] == "not-a-scanned-model"

    def test_inherit_and_auto_round_trip(self, tmp_path):
        bridge = _bridge(tmp_path)
        bridge.setOllamaModelAssignment(config.TASK_TITLE, "some-model")

        bridge.setOllamaModelAssignment(config.TASK_TITLE, "inherit")
        assert _last_payload(bridge)["ollamaModelAssignments"]["task_title"] == "inherit"

        bridge.setOllamaModelAssignment(config.TASK_TITLE, "auto")
        assert _last_payload(bridge)["ollamaModelAssignments"]["task_title"] == "auto"

    def test_an_unrecognized_task_is_ignored(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setOllamaModelAssignment("not_a_real_task", "some-model")

        assert states == []

    def test_all_five_tasks_are_settable(self, tmp_path):
        bridge = _bridge(tmp_path)
        for task in OLLAMA_TASKS:
            bridge.setOllamaModelAssignment(task, f"model-for-{task}")

        payload = _last_payload(bridge)
        for task in OLLAMA_TASKS:
            assert payload["ollamaModelAssignments"][task] == f"model-for-{task}"


class TestOllamaScan:
    def test_system_scan_updates_status_and_scanned_models(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(
            graphlink_settings_workers.api_provider,
            "scan_local_ollama_models",
            lambda scan_path: {"models": ["llama3:8b", "mistral:7b"], "scan_mode": "system", "scan_path": "", "locations": []},
        )

        bridge.scanOllamaSystem()
        _wait_for_worker(bridge._ollama_scan_worker)

        payload = _last_payload(bridge)
        assert payload["ollamaScanStatus"] == "done"
        assert sorted(payload["ollamaScannedModels"]) == ["llama3:8b", "mistral:7b"]

    def test_scan_failure_sets_status_error_and_a_notice(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)

        def _raise(_scan_path):
            raise RuntimeError("ollama not running")

        monkeypatch.setattr(graphlink_settings_workers.api_provider, "scan_local_ollama_models", _raise)

        bridge.scanOllamaSystem()
        _wait_for_worker(bridge._ollama_scan_worker)

        payload = _last_payload(bridge)
        assert payload["ollamaScanStatus"] == "error"
        assert "ollama not running" in payload["notice"]

    def test_reentrant_scan_while_running_is_a_no_op(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(
            graphlink_settings_workers.api_provider,
            "scan_local_ollama_models",
            lambda scan_path: {"models": [], "scan_mode": "system", "scan_path": "", "locations": []},
        )
        bridge.scanOllamaSystem()
        first_worker = bridge._ollama_scan_worker

        bridge.scanOllamaSystem()

        assert bridge._ollama_scan_worker is first_worker
        _wait_for_worker(first_worker)

    def test_pick_scan_folder_starts_a_scan_of_the_chosen_directory(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path)))
        monkeypatch.setattr(
            graphlink_settings_workers.api_provider,
            "scan_local_ollama_models",
            lambda scan_path: {"models": ["llama3:8b"], "scan_mode": "folder", "scan_path": scan_path, "locations": []},
        )

        bridge.pickOllamaScanFolder()
        _wait_for_worker(bridge._ollama_scan_worker)

        payload = _last_payload(bridge)
        assert payload["ollamaScanSummary"] == f"Using saved scan from folder: {tmp_path}"

    def test_pick_scan_folder_cancelled_does_not_start_a_scan(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))

        bridge.pickOllamaScanFolder()

        assert bridge._ollama_scan_worker is None


class TestOllamaPull:
    def test_a_successful_pull_updates_status_and_resolves_the_model(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(graphlink_agents_tools, "ollama", type("Fake", (), {"pull": staticmethod(lambda model: None)}))
        monkeypatch.setattr(graphlink_agents_tools.api_provider, "invalidate_ollama_capability_cache", lambda model: None)

        bridge.pullOllamaModel("llama3:8b")
        _wait_for_worker(bridge._ollama_pull_worker)

        payload = _last_payload(bridge)
        assert payload["ollamaPullStatus"] == "done"
        assert payload["ollamaCurrentModel"] == "llama3:8b"

    def test_a_failed_pull_sets_status_error_and_a_notice(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)

        def _raise(model):
            raise RuntimeError("model not found")

        monkeypatch.setattr(graphlink_agents_tools, "ollama", type("Fake", (), {"pull": staticmethod(_raise)}))

        bridge.pullOllamaModel("nonexistent:model")
        _wait_for_worker(bridge._ollama_pull_worker)

        payload = _last_payload(bridge)
        assert payload["ollamaPullStatus"] == "error"
        assert payload["notice"]

    def test_an_empty_model_name_is_rejected_before_starting_a_worker(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.pullOllamaModel("   ")

        assert bridge._ollama_pull_worker is None
        payload = _last_payload(bridge)
        assert payload["notice"] == "Model name cannot be empty."
