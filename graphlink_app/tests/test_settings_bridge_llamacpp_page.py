"""Contract tests for the settings bridge's LlamaCpp page (Phase 3
increment 7) - staged file paths (Browse fills, Save persists+validates),
runtime tuning fields, and the scan worker status port.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

import api_provider
import graphlink_config as config
import graphlink_settings_workers
from graphlink_licensing import SettingsManager
from graphlink_settings_bridge import SettingsBridge


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


def _gguf_file(tmp_path, name="model.gguf") -> str:
    path = tmp_path / name
    path.write_bytes(b"fake gguf")
    return str(path)


class TestInitialPayload:
    def test_defaults(self, tmp_path):
        payload = _last_payload(_bridge(tmp_path))

        assert payload["llamaCppReasoningMode"] == "Thinking"
        assert payload["llamaCppChatModelPath"] == ""
        assert payload["llamaCppTitleModelPath"] == ""
        assert payload["llamaCppChatFormat"] == ""
        assert payload["llamaCppNCtx"] == 4096
        assert payload["llamaCppNGpuLayers"] == 0
        assert payload["llamaCppNThreads"] == 0
        assert payload["llamaCppScannedModels"] == []
        assert payload["llamaCppScanSummary"].startswith("No saved GGUF scan yet")
        assert payload["llamaCppScanStatus"] == "idle"


class TestSetLlamaCppReasoningMode:
    def test_valid_mode_persists_and_publishes(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setLlamaCppReasoningMode("Quick")

        assert settings_manager.get_llama_cpp_reasoning_mode() == "Quick"
        assert _last_payload(bridge)["llamaCppReasoningMode"] == "Quick"

    def test_invalid_mode_is_ignored(self, tmp_path):
        bridge = _bridge(tmp_path)
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.setLlamaCppReasoningMode("Not A Real Mode")

        assert states == []

    def test_reasoning_change_reinitializes_the_running_agent(self, tmp_path):
        # Symmetric with the Ollama fix: when Llama.cpp is the active
        # provider its reasoning mode feeds _get_current_system_prompt, so a
        # change must rebuild the running agent to take effect live.
        class _FakeMainWindow:
            def __init__(self):
                self.reinit_calls = 0

            def reinitialize_agent(self):
                self.reinit_calls += 1

        main_window = _FakeMainWindow()
        bridge = SettingsBridge(SettingsManager(tmp_path / "session.dat"), main_window=main_window)

        bridge.setLlamaCppReasoningMode("Quick")

        assert main_window.reinit_calls == 1


class TestStageScannedModelPaths:
    def test_set_chat_model_path_stages_but_does_not_persist(self, tmp_path):
        # The scanned-model dropdown's counterpart to the native picker:
        # stages the chosen path only, committed by saveLlamaCppSettings.
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setLlamaCppChatModelPath("/models/scanned-chat.gguf")

        assert _last_payload(bridge)["llamaCppChatModelPath"] == "/models/scanned-chat.gguf"
        assert settings_manager.get_llama_cpp_chat_model_path() == ""

    def test_set_title_model_path_stages_but_does_not_persist(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setLlamaCppTitleModelPath("/models/scanned-title.gguf")

        assert _last_payload(bridge)["llamaCppTitleModelPath"] == "/models/scanned-title.gguf"
        assert settings_manager.get_llama_cpp_title_model_override_path() == ""

    def test_empty_path_clears_the_staged_chat_model(self, tmp_path):
        bridge = _bridge(tmp_path)
        bridge.setLlamaCppChatModelPath("/models/scanned-chat.gguf")

        bridge.setLlamaCppChatModelPath("")

        assert _last_payload(bridge)["llamaCppChatModelPath"] == ""


class TestRuntimeTuning:
    def test_set_chat_format_persists_immediately(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setLlamaCppChatFormat("chatml")

        assert settings_manager.get_llama_cpp_chat_format() == "chatml"
        assert _last_payload(bridge)["llamaCppChatFormat"] == "chatml"

    def test_set_n_ctx_persists_without_clobbering_other_runtime_fields(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        bridge.setLlamaCppNGpuLayers(12)

        bridge.setLlamaCppNCtx(8192)

        payload = _last_payload(bridge)
        assert payload["llamaCppNCtx"] == 8192
        assert payload["llamaCppNGpuLayers"] == 12

    def test_set_n_gpu_layers_persists(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setLlamaCppNGpuLayers(-1)

        assert settings_manager.get_llama_cpp_n_gpu_layers() == -1
        assert _last_payload(bridge)["llamaCppNGpuLayers"] == -1

    def test_set_n_threads_persists(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)

        bridge.setLlamaCppNThreads(8)

        assert settings_manager.get_llama_cpp_n_threads() == 8
        assert _last_payload(bridge)["llamaCppNThreads"] == 8


class TestPickModelFiles:
    def test_pick_chat_model_file_stages_but_does_not_persist(self, tmp_path, monkeypatch):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        model_path = _gguf_file(tmp_path)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (model_path, "")))

        bridge.pickLlamaCppChatModelFile()

        assert _last_payload(bridge)["llamaCppChatModelPath"] == model_path
        assert settings_manager.get_llama_cpp_chat_model_path() == ""

    def test_pick_chat_model_file_cancelled_is_a_no_op(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
        states = []
        bridge.stateChanged.connect(states.append)

        bridge.pickLlamaCppChatModelFile()

        assert states == []

    def test_pick_title_model_file_stages_but_does_not_persist(self, tmp_path, monkeypatch):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        model_path = _gguf_file(tmp_path, "title.gguf")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (model_path, "")))

        bridge.pickLlamaCppTitleModelFile()

        assert _last_payload(bridge)["llamaCppTitleModelPath"] == model_path
        assert settings_manager.get_llama_cpp_title_model_override_path() == ""


class TestLlamaCppScan:
    def test_system_scan_updates_status_and_scanned_models(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        model_path = _gguf_file(tmp_path)
        monkeypatch.setattr(
            graphlink_settings_workers.api_provider,
            "scan_local_llama_cpp_models",
            lambda scan_path: {"models": [model_path], "scan_mode": "system", "scan_path": "", "locations": []},
        )

        bridge.scanLlamaCppSystem()
        _wait_for_worker(bridge._llama_scan_worker)

        payload = _last_payload(bridge)
        assert payload["llamaCppScanStatus"] == "done"
        assert payload["llamaCppScannedModels"] == [model_path]

    def test_scan_failure_sets_status_error_and_a_notice(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)

        def _raise(_scan_path):
            raise RuntimeError("no folders found")

        monkeypatch.setattr(graphlink_settings_workers.api_provider, "scan_local_llama_cpp_models", _raise)

        bridge.scanLlamaCppSystem()
        _wait_for_worker(bridge._llama_scan_worker)

        payload = _last_payload(bridge)
        assert payload["llamaCppScanStatus"] == "error"
        assert "no folders found" in payload["notice"]

    def test_reentrant_scan_while_running_is_a_no_op(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(
            graphlink_settings_workers.api_provider,
            "scan_local_llama_cpp_models",
            lambda scan_path: {"models": [], "scan_mode": "system", "scan_path": "", "locations": []},
        )
        bridge.scanLlamaCppSystem()
        first_worker = bridge._llama_scan_worker

        bridge.scanLlamaCppSystem()

        assert bridge._llama_scan_worker is first_worker
        _wait_for_worker(first_worker)

    def test_pick_scan_folder_starts_a_scan_of_the_chosen_directory(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        model_path = _gguf_file(tmp_path)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path)))
        monkeypatch.setattr(
            graphlink_settings_workers.api_provider,
            "scan_local_llama_cpp_models",
            lambda scan_path: {"models": [model_path], "scan_mode": "folder", "scan_path": scan_path, "locations": []},
        )

        bridge.pickLlamaCppScanFolder()
        _wait_for_worker(bridge._llama_scan_worker)

        payload = _last_payload(bridge)
        assert payload["llamaCppScanSummary"] == f"Using saved scan from folder: {tmp_path}"

    def test_pick_scan_folder_cancelled_does_not_start_a_scan(self, tmp_path, monkeypatch):
        bridge = _bridge(tmp_path)
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))

        bridge.pickLlamaCppScanFolder()

        assert bridge._llama_scan_worker is None


class TestSaveLlamaCppSettings:
    def test_empty_chat_path_is_rejected(self, tmp_path):
        bridge = _bridge(tmp_path)

        bridge.saveLlamaCppSettings()

        payload = _last_payload(bridge)
        assert payload["notice"] == "Chat Model File cannot be empty."

    def test_nonexistent_chat_path_is_rejected(self, tmp_path):
        bridge = _bridge(tmp_path)
        bridge._llama_chat_model_path = str(tmp_path / "missing.gguf")

        bridge.saveLlamaCppSettings()

        payload = _last_payload(bridge)
        assert "was not found" in payload["notice"]

    def test_non_gguf_chat_path_is_rejected(self, tmp_path):
        bridge = _bridge(tmp_path)
        not_gguf = tmp_path / "model.bin"
        not_gguf.write_bytes(b"data")
        bridge._llama_chat_model_path = str(not_gguf)

        bridge.saveLlamaCppSettings()

        payload = _last_payload(bridge)
        assert payload["notice"] == "Chat Model File must point to a .gguf file."

    def test_invalid_title_path_is_rejected_even_with_a_valid_chat_path(self, tmp_path):
        bridge = _bridge(tmp_path)
        bridge._llama_chat_model_path = _gguf_file(tmp_path)
        bridge._llama_title_model_path = str(tmp_path / "missing_title.gguf")

        bridge.saveLlamaCppSettings()

        payload = _last_payload(bridge)
        assert "Chat naming model file was not found" in payload["notice"]

    def test_valid_paths_persist_when_not_in_llamacpp_mode(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        chat_path = _gguf_file(tmp_path)
        bridge._llama_chat_model_path = chat_path

        bridge.saveLlamaCppSettings()

        assert settings_manager.get_llama_cpp_chat_model_path() == chat_path
        payload = _last_payload(bridge)
        assert payload["notice"] is None
        assert payload["llamaCppChatModelPath"] == chat_path

    def test_optional_title_path_left_blank_persists_as_blank(self, tmp_path):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        bridge = SettingsBridge(settings_manager)
        bridge._llama_chat_model_path = _gguf_file(tmp_path)

        bridge.saveLlamaCppSettings()

        assert settings_manager.get_llama_cpp_title_model_override_path() == ""

    def test_in_llamacpp_mode_a_rejected_provider_init_does_not_persist(self, tmp_path, monkeypatch):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        settings_manager.set_current_mode(config.MODE_LLAMACPP_LOCAL)
        bridge = SettingsBridge(settings_manager)
        bridge._llama_chat_model_path = _gguf_file(tmp_path)

        def _raise(provider, settings, preload_model=False):
            raise RuntimeError("failed to load model")

        monkeypatch.setattr(api_provider, "initialize_local_provider", _raise)

        bridge.saveLlamaCppSettings()

        assert settings_manager.get_llama_cpp_chat_model_path() == ""
        payload = _last_payload(bridge)
        assert "Invalid Llama.cpp configuration" in payload["notice"]

    def test_in_llamacpp_mode_a_successful_init_persists(self, tmp_path, monkeypatch):
        settings_manager = SettingsManager(tmp_path / "session.dat")
        settings_manager.set_current_mode(config.MODE_LLAMACPP_LOCAL)
        bridge = SettingsBridge(settings_manager)
        chat_path = _gguf_file(tmp_path)
        bridge._llama_chat_model_path = chat_path

        monkeypatch.setattr(api_provider, "initialize_local_provider", lambda provider, settings, preload_model=False: {"provider": provider})

        bridge.saveLlamaCppSettings()

        assert settings_manager.get_llama_cpp_chat_model_path() == chat_path
        assert _last_payload(bridge)["notice"] is None
