"""Ollama /api/tags + /api/show + /api/ps inventory normalization.

The tests provide synthetic Ollama responses and never contact a real daemon
or read real user's model manifests. They exercise SDK object and dictionary
shapes, unavailable metadata, and the existing scanner fallback.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

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
    result = ollama_inventory.build_ollama_inventory()

    scan.assert_called_once_with(None)
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
