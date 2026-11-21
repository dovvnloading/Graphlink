"""Ollama /api/tags + /api/show + /api/ps inventory normalization.

The tests provide synthetic Ollama responses and never contact a real daemon
or read real user's model manifests. They exercise SDK object and dictionary
shapes, unavailable metadata, and the existing scanner fallback.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ByteSize

import api_provider

from backend import ollama_inventory


def test_live_inventory_combines_installed_metadata_capabilities_and_loaded_memory(monkeypatch):
    tags = {
        "models": [
            {
                "name": "qwen2.5-vl:7b", "size": 4_000_000_000,
                "details": {"family": "qwen2", "parameter_size": "7B", "quantization_level": "Q4_K_M"},
            },
            {
                "model": "nomic-embed-text:latest", "size": 274_000_000,
                "details": {"family": "nomic-bert", "parameter_size": "137M", "quantization_level": "F16"},
            },
        ],
    }
    shows = {
        "qwen2.5-vl:7b": {
            "details": {"family": "qwen2.5vl", "parameter_size": "7.6B", "quantization_level": "Q4_K_M"},
            "capabilities": ["completion", "tools", "vision", "thinking"],
            "model_info": {"general.architecture": "qwen2", "qwen2.context_length": 131072},
            "parameters": "num_ctx 4096",  # Do not confuse served context with trained limit.
        },
        "nomic-embed-text:latest": {
            "capabilities": ["embedding"],
            "model_info": {"nomic_bert.context_length": 8192},
        },
    }
    shown = Mock(side_effect=lambda name: shows[name])
    monkeypatch.setattr(ollama_inventory.ollama, "list", Mock(return_value=tags))
    monkeypatch.setattr(ollama_inventory.ollama, "show", shown)
    monkeypatch.setattr(ollama_inventory.ollama, "ps", Mock(return_value={
        "models": [{
            "name": "qwen2.5-vl:7b", "size": 5_200_000_000, "size_vram": 3_200_000_000,
        }],
    }))

    result = ollama_inventory.build_ollama_inventory()

    assert result["source"] == result["scan_mode"] == "server"
    assert result["server_reachable"] is True
    assert result["server_status"] == "running"
    assert result["models"] == ["nomic-embed-text:latest", "qwen2.5-vl:7b"]
    assert sorted(call.args[0] for call in shown.call_args_list) == result["models"]
    embed, chat = result["inventory"]
    assert embed["capabilities"] == ["embeddings"]
    assert embed["sizeBytes"] == 274_000_000
    assert embed["family"] == "nomic-bert"
    assert embed["loaded"] is False
    assert embed["memoryBytes"] is None
    assert embed["trainedContextLength"] == 8192
    assert chat == {
        "name": "qwen2.5-vl:7b",
        "sizeBytes": 4_000_000_000,
        "family": "qwen2.5vl",
        "parameterSize": "7.6B",
        "quantization": "Q4_K_M",
        "capabilities": ["chat", "thinking", "tools", "vision"],
        "trainedContextLength": 131072,
        "loaded": True,
        "memoryBytes": 5_200_000_000,
        "vramBytes": 3_200_000_000,
        "source": "server",
    }


def test_sdk_response_objects_and_latest_alias_match_loaded_state(monkeypatch):
    monkeypatch.setattr(ollama_inventory.ollama, "list", lambda: SimpleNamespace(models=[
        SimpleNamespace(model="gemma", size=1024, details=SimpleNamespace(
            family="gemma", parameter_size="2B", quantization_level="Q8_0",
        )),
    ]))
    monkeypatch.setattr(ollama_inventory.ollama, "show", lambda _name: SimpleNamespace(
        capabilities=["completion"], model_info={"gemma.context_length": 32768},
    ))
    monkeypatch.setattr(ollama_inventory.ollama, "ps", lambda: SimpleNamespace(models=[
        SimpleNamespace(model="gemma:latest", size=2048, size_vram=0),
    ]))
    row = ollama_inventory.build_ollama_inventory()["inventory"][0]
    assert row["loaded"] is True
    assert row["sizeBytes"] == 1024
    assert row["memoryBytes"] == 2048
    assert row["vramBytes"] == 0
    assert row["trainedContextLength"] == 32768
    assert row["capabilities"] == ["chat"]


def test_sdk_bytesize_subclasses_reach_inventory_context_and_live_pickers(monkeypatch):
    # The real Ollama SDK types /api/tags.size and /api/ps.size as
    # pydantic.ByteSize, not plain int. Rejecting type(value) is not int
    # previously made every size label and the context weight unknown.
    monkeypatch.setattr(ollama_inventory.ollama, "list", lambda: SimpleNamespace(models=[
        SimpleNamespace(model="gemma4:12b", size=ByteSize(7_400_000_000)),
    ]))
    monkeypatch.setattr(ollama_inventory.ollama, "show", lambda name: {
        "capabilities": ["completion"],
        "model_info": {"gemma4.context_length": 131072},
    })
    monkeypatch.setattr(ollama_inventory.ollama, "ps", lambda: SimpleNamespace(models=[
        SimpleNamespace(model="gemma4:12b", size=ByteSize(8_000_000_000),
                        size_vram=ByteSize(6_000_000_000)),
    ]))

    row = ollama_inventory.build_ollama_inventory()["inventory"][0]
    assert row["sizeBytes"] == 7_400_000_000 and type(row["sizeBytes"]) is int
    assert row["memoryBytes"] == 8_000_000_000 and type(row["memoryBytes"]) is int
    assert row["vramBytes"] == 6_000_000_000 and type(row["vramBytes"]) is int
    assert row["loaded"] is True

    from backend.context_engine import model_comparison

    monkeypatch.setattr(model_comparison, "available_comparison_models", lambda *_: [])
    picker = model_comparison.available_picker_models(object())
    assert picker[0]["modelId"] == "gemma4:12b"
    assert picker[0]["sizeBytes"] == 7_400_000_000
    assert ollama_inventory._nonnegative_int(True) is None
    assert ollama_inventory._nonnegative_int(3.5) is None
    assert ollama_inventory._nonnegative_int(2**53) is None


def test_refresh_loaded_state_uses_ps_without_relisting_and_marks_ps_failures_unknown(monkeypatch):
    row = {
        "name": "qwen:8b", "sizeBytes": 4_500_000_000, "source": "server",
        "loaded": False, "memoryBytes": None, "vramBytes": None,
    }
    monkeypatch.setattr(ollama_inventory.ollama, "list",
                        Mock(side_effect=AssertionError("Do not re-list tags after a Check")))
    monkeypatch.setattr(ollama_inventory.ollama, "show",
                        Mock(side_effect=AssertionError("Do not re-show the whole catalog")))
    monkeypatch.setattr(ollama_inventory.ollama, "ps", lambda: {"models": [
        {"name": "qwen:8b", "size": ByteSize(5_000_000_000), "size_vram": ByteSize(3_000_000_000)},
    ]})
    updated = ollama_inventory.refresh_ollama_loaded_state([row])
    assert row["loaded"] is False  # Do not mutate the old published snapshot.
    assert updated[0]["loaded"] is True
    assert updated[0]["sizeBytes"] == 4_500_000_000
    assert updated[0]["memoryBytes"] == 5_000_000_000
    assert updated[0]["vramBytes"] == 3_000_000_000

    monkeypatch.setattr(ollama_inventory.ollama, "ps", Mock(side_effect=ConnectionError("server down")))
    offline = ollama_inventory.refresh_ollama_loaded_state(updated)
    assert offline[0]["loaded"] is None
    assert offline[0]["memoryBytes"] is None
    assert offline[0]["vramBytes"] is None


def test_unavailable_show_and_ps_keep_tagged_models_with_unknown_metadata(monkeypatch):
    monkeypatch.setattr(ollama_inventory.ollama, "list", lambda: {
        "models": [{"name": "still-installed:latest", "size": 12}],
    })
    monkeypatch.setattr(ollama_inventory.ollama, "show", Mock(side_effect=ConnectionError("show unavailable")))
    monkeypatch.setattr(ollama_inventory.ollama, "ps", Mock(side_effect=TimeoutError("ps unavailable")))
    monkeypatch.setattr(
        api_provider, "scan_local_ollama_models",
        Mock(side_effect=AssertionError("A healthy /api/tags must not fall back to stale disk manifests")),
    )

    result = ollama_inventory.build_ollama_inventory()
    row = result["inventory"][0]
    assert result["server_reachable"] is True
    assert row["name"] == "still-installed:latest"
    assert row["capabilities"] == []
    assert row["loaded"] is None  # Not false: ps is inaccessible.
    assert row["memoryBytes"] is None
    assert row["sizeBytes"] == 12


def test_tags_available_but_empty_does_not_resurrect_old_folder_models(monkeypatch):
    monkeypatch.setattr(ollama_inventory.ollama, "list", lambda: {"models": []})
    monkeypatch.setattr(ollama_inventory.ollama, "ps", lambda: {"models": []})
    monkeypatch.setattr(
        api_provider, "scan_local_ollama_models",
        Mock(side_effect=AssertionError("Successful tags are authoritative")),
    )
    result = ollama_inventory.build_ollama_inventory()
    assert result["source"] == "server"
    assert result["models"] == []
    assert result["inventory"] == []


def test_unreachable_daemon_falls_back_to_existing_system_folder_scan(monkeypatch):
    monkeypatch.setattr(ollama_inventory.ollama, "list", Mock(side_effect=ConnectionError("connection refused")))
    scan = Mock(return_value={
        "models": ["nomic-embed-text:latest", "llama3:latest"],
        "descriptors": [{
            "model_id": "llama3:latest", "size_bytes": 123456,
            "quantization": "Q4_0",
        }],
        "scan_mode": "system",
        "scan_path": "",
        "locations": ["/tmp/synthetic/ollama/models/manifests"],
    })
    monkeypatch.setattr(api_provider, "scan_local_ollama_models", scan)
    monkeypatch.setattr(ollama_inventory, "_installed_ollama_command", lambda: ["/opt/ollama", "serve"])
    result = ollama_inventory.build_ollama_inventory()

    scan.assert_called_once_with(None)
    assert result["server_status"] == "installed"
    assert result["source"] == "folder"
    assert result["scan_mode"] == "system"
    assert result["server_reachable"] is False
    assert "connection refused" in result["server_error"]
    assert result["models"] == ["llama3:latest", "nomic-embed-text:latest"]
    assert result["inventory"][0]["sizeBytes"] == 123456
    assert result["inventory"][0]["quantization"] == "Q4_0"
    for row in result["inventory"]:
        assert row["source"] == "folder"
        assert row["loaded"] is None
        assert row["memoryBytes"] is None
        assert row["capabilities"] == []  # Manifest names cannot verify embedding support.


def test_explicit_scan_folder_preserves_manual_scan_without_requiring_server(monkeypatch):
    show = Mock(side_effect=AssertionError("No Ollama API calls for explicit folder scan"))
    monkeypatch.setattr(ollama_inventory.ollama, "list", show)
    monkeypatch.setattr(ollama_inventory.ollama, "show", show)
    monkeypatch.setattr(ollama_inventory.ollama, "ps", show)
    scan = Mock(return_value={
        "models": ["custom:latest"], "scan_mode": "folder",
        "scan_path": "/tmp/synthetic/models", "locations": ["/tmp/synthetic/models"],
    })
    monkeypatch.setattr(api_provider, "scan_local_ollama_models", scan)
    result = ollama_inventory.build_ollama_inventory("/tmp/synthetic/models")
    scan.assert_called_once_with("/tmp/synthetic/models")
    show.assert_not_called()
    assert result["source"] == "folder"
    assert result["models"] == ["custom:latest"]
    assert result["server_reachable"] is None

def test_lightweight_status_probe_distinguishes_running_installed_and_missing(monkeypatch):
    # /api/tags is already the shared inventory's reachability signal; do
    # not call /api/show, /api/ps or traverse model manifests for health.
    monkeypatch.setattr(ollama_inventory, "_status_tags", Mock(return_value={"models": []}))
    monkeypatch.setattr(ollama_inventory.ollama, "show", Mock(side_effect=AssertionError("no /api/show")))
    monkeypatch.setattr(ollama_inventory.ollama, "ps", Mock(side_effect=AssertionError("no /api/ps")))
    monkeypatch.setattr(ollama_inventory, "_installed_ollama_command", lambda: None)
    assert ollama_inventory.check_ollama_server_status() == "running"

    monkeypatch.setattr(ollama_inventory, "_status_tags", Mock(side_effect=ConnectionError("refused")))
    monkeypatch.setattr(ollama_inventory, "_installed_ollama_command", lambda: ["/opt/ollama", "serve"])
    assert ollama_inventory.check_ollama_server_status() == "installed"
    monkeypatch.setattr(ollama_inventory, "_installed_ollama_command", lambda: None)
    assert ollama_inventory.check_ollama_server_status() == "not_found"


def test_status_probe_uses_bounded_ollama_client_and_closes_it(monkeypatch):
    seen = []

    class FakeClient:
        def __init__(self, *, timeout):
            seen.append(("timeout", timeout))

        def __enter__(self):
            return self

        def list(self):
            return {"models": []}

        def __exit__(self, *_args):
            seen.append(("closed", True))

    monkeypatch.setattr(ollama_inventory.ollama, "Client", FakeClient)
    assert ollama_inventory.check_ollama_server_status() == "running"
    assert seen == [("timeout", 2.0), ("closed", True)]


def test_status_ignores_stale_models_when_ollama_executable_is_missing(monkeypatch):
    monkeypatch.setattr(ollama_inventory, "_status_tags", Mock(side_effect=ConnectionError("offline")))
    monkeypatch.setattr(ollama_inventory, "_installed_ollama_command", lambda: None)
    # No model manifest (even a saved inventory) can make a missing app launchable.
    assert ollama_inventory.check_ollama_server_status() == "not_found"


def test_installed_ollama_cli_is_spawned_without_shell_or_download(monkeypatch):
    monkeypatch.setattr(ollama_inventory, "check_ollama_server_status", lambda: "installed")
    monkeypatch.setattr(ollama_inventory, "_installed_ollama_command", lambda: ["/opt/ollama", "serve"])
    spawn = Mock()
    monkeypatch.setattr(ollama_inventory.subprocess, "Popen", spawn)

    assert ollama_inventory.start_installed_ollama() is True
    spawn.assert_called_once()
    args, kwargs = spawn.call_args
    assert args == (["/opt/ollama", "serve"],)
    assert kwargs["shell"] is False
    assert kwargs["close_fds"] is True
    assert kwargs["stdin"] == ollama_inventory.subprocess.DEVNULL
    assert kwargs["stdout"] == ollama_inventory.subprocess.DEVNULL
    assert kwargs["stderr"] == ollama_inventory.subprocess.DEVNULL
    if ollama_inventory.sys.platform != "win32":
        assert kwargs["start_new_session"] is True


def test_start_does_nothing_when_running_and_rejects_missing_install(monkeypatch):
    spawn = Mock(side_effect=AssertionError("no process may be launched"))
    monkeypatch.setattr(ollama_inventory.subprocess, "Popen", spawn)
    monkeypatch.setattr(ollama_inventory, "check_ollama_server_status", lambda: "running")
    assert ollama_inventory.start_installed_ollama() is False
    monkeypatch.setattr(ollama_inventory, "check_ollama_server_status", lambda: "not_found")
    monkeypatch.setattr(ollama_inventory, "_installed_ollama_command", lambda: None)
    with pytest.raises(FileNotFoundError, match="not installed"):
        ollama_inventory.start_installed_ollama()
    spawn.assert_not_called()


def test_windows_installed_app_is_detected_without_path_entry(tmp_path, monkeypatch):
    app = tmp_path / "Programs" / "Ollama" / "ollama app.exe"
    app.parent.mkdir(parents=True)
    app.touch()
    monkeypatch.setattr(ollama_inventory.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.setattr(ollama_inventory.shutil, "which", lambda _name: None)
    assert ollama_inventory._installed_ollama_command() == [str(app)]


def test_windows_tray_falls_back_to_installed_cli_without_shell(tmp_path, monkeypatch):
    folder = tmp_path / "Programs" / "Ollama"
    folder.mkdir(parents=True)
    app, cli = folder / "ollama app.exe", folder / "ollama.exe"
    app.touch()
    cli.touch()
    monkeypatch.setattr(ollama_inventory.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.setattr(ollama_inventory.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ollama_inventory, "check_ollama_server_status", lambda: "installed")
    spawn = Mock()
    monkeypatch.setattr(ollama_inventory.subprocess, "Popen", spawn)

    assert ollama_inventory.start_installed_ollama() is True
    assert ollama_inventory.start_installed_ollama_server_fallback() is True
    assert [call.args[0] for call in spawn.call_args_list] == [[str(app)], [str(cli), "serve"]]
    for call in spawn.call_args_list:
        assert call.kwargs["shell"] is False
        assert call.kwargs["close_fds"] is True
    assert spawn.call_args_list[1].kwargs["creationflags"] == getattr(
        ollama_inventory.subprocess, "CREATE_NO_WINDOW", 0
    )


def test_windows_tray_without_cli_cannot_spawn_an_unrelated_command(tmp_path, monkeypatch):
    app = tmp_path / "Programs" / "Ollama" / "ollama app.exe"
    app.parent.mkdir(parents=True)
    app.touch()
    monkeypatch.setattr(ollama_inventory.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)
    monkeypatch.setattr(ollama_inventory.shutil, "which", lambda _name: None)
    spawn = Mock(side_effect=AssertionError("no CLI available"))
    monkeypatch.setattr(ollama_inventory.subprocess, "Popen", spawn)
    assert ollama_inventory.start_installed_ollama_server_fallback() is False
    spawn.assert_not_called()


def test_macos_installed_app_is_detected_without_path_entry(tmp_path, monkeypatch):
    app = tmp_path / "Applications" / "Ollama.app"
    app.mkdir(parents=True)
    monkeypatch.setattr(ollama_inventory.sys, "platform", "darwin")
    monkeypatch.setattr(ollama_inventory.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(ollama_inventory.shutil, "which", lambda _name: None)
    assert ollama_inventory._installed_ollama_command() == ["/usr/bin/open", str(app)]


@pytest.mark.parametrize("invalid", [
    "", " ", "../outside:latest", "dir/../outside:latest",
    "/tmp/escape", "C:\\\\Windows\\\\system32", "https://example.com/model",
    "model\u000a:latest", "--insecure", "name:tag with spaces", "name:" + "x" * 256,
    None, {"name": "model"}, True,
])
def test_model_name_validator_blocks_paths_commands_and_non_strings(invalid):
    from backend.ollama_model_names import valid_ollama_model_name

    assert not valid_ollama_model_name(invalid)


@pytest.mark.parametrize("valid", [
    "llama3", "qwen3:4b", "namespace/my-model:Q4_K_M",
    "registry.ollama.ai/library/gemma3:latest", "namespace/subproject/model:tag",
])
def test_model_name_validator_accepts_supported_ollama_identifiers(valid):
    from backend.ollama_model_names import valid_ollama_model_name

    assert valid_ollama_model_name(valid)


def test_daemon_inventory_ignores_unsafe_names_and_untrusted_metadata(monkeypatch):
    names = ["safe:latest", "../bad:latest", "bad\\\\name", "--option", "evil:latest\u000a"]
    monkeypatch.setattr(ollama_inventory.ollama, "list", lambda: {"models": [
        {"name": name, "size": float("inf"), "details": {
            "family": "\u0000" if name == "safe:latest" else "unknown",
            "parameter_size": {"nested": "data"},
        }} for name in names
    ]})
    shown = Mock(return_value={
        "capabilities": {"tools": True}, "model_info": {"general.architecture": "llama",
        "llama.context_length": float("inf")},
    })
    monkeypatch.setattr(ollama_inventory.ollama, "show", shown)
    monkeypatch.setattr(ollama_inventory.ollama, "ps", lambda: {"models": {"bad": "value"}})

    result = ollama_inventory.build_ollama_inventory()
    assert result["models"] == ["safe:latest"]
    shown.assert_called_once_with("safe:latest")
    row = result["inventory"][0]
    assert row["sizeBytes"] is None
    assert row["parameterSize"] == ""
    assert row["family"] == ""
    assert row["loaded"] is None
    assert row["capabilities"] == []
    assert row["trainedContextLength"] is None


def test_bad_tags_response_is_not_marked_as_running(monkeypatch):
    monkeypatch.setattr(ollama_inventory.ollama, "list", lambda: {"models": "not an array"})
    monkeypatch.setattr(ollama_inventory, "_status_tags", lambda: {"models": "not an array"})
    monkeypatch.setattr(ollama_inventory, "_installed_ollama_command", lambda: None)
    monkeypatch.setattr(api_provider, "scan_local_ollama_models", lambda _path: {
        "models": [], "scan_mode": "system", "locations": [],
    })
    assert ollama_inventory.check_ollama_server_status() == "not_found"
    assert ollama_inventory.build_ollama_inventory()["source"] == "folder"


def test_relative_path_ollama_cli_is_not_executed_from_working_directory(monkeypatch, tmp_path):
    executable = tmp_path / "ollama"
    executable.write_text("#!/bin/sh\\n")
    executable.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ollama_inventory.sys, "platform", "linux")
    monkeypatch.setattr(ollama_inventory.shutil, "which", lambda _: "ollama")
    monkeypatch.setattr(ollama_inventory.Path, "home", classmethod(lambda _cls: tmp_path / "no-home"))
    # CI machines may themselves have /usr/bin/ollama installed.
    monkeypatch.setattr(ollama_inventory.Path, "is_file", lambda self: self == executable)
    assert ollama_inventory._installed_ollama_command() is None
