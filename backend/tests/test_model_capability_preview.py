"""Capability warnings use explicit evidence, never provider-family guesses."""

import api_provider
from backend.api import intents_harness
from backend.api.intents_harness import _model_capability_preview

_GIB = 1024 ** 3


def _verified_inventory(monkeypatch, rows, available=6 * _GIB):
    monkeypatch.setattr(intents_harness, "available_memory_bytes", lambda: available)
    monkeypatch.setattr(intents_harness, "build_ollama_inventory", lambda: {
        "source": "server", "inventory": rows,
    })
    monkeypatch.setattr(intents_harness.ollama, "show", lambda _model: {"model_info": {}})


def _tool_row(name, size):
    return {
        "name": name, "source": "server", "sizeBytes": size,
        "capabilities": ["chat", "tools"], "trainedContextLength": 32_768,
    }


class Catalog:
    def __init__(self, *, installed=(), cloud=None, windows=None):
        self.installed = list(installed)
        self.cloud = cloud or {}
        self.windows = windows or {}

    def get_ollama_scanned_models(self):
        return self.installed

    def get_ollama_context_windows(self):
        return self.windows

    def get_api_model_catalog(self, provider):
        return self.cloud.get(provider, [])


def test_ollama_negative_probe_warns_and_suggests_confirmed_installed_model(monkeypatch):
    show = {
        "plain:latest": {"completion"},
        "tool-model:latest": {"completion", "tools", "vision"},
        "unverified:latest": None,
    }
    calls = []

    def probe(model):
        calls.append(model)
        return show.get(model)

    monkeypatch.setattr(api_provider, "_get_ollama_capabilities", probe)
    _verified_inventory(monkeypatch, [_tool_row("tool-model:latest", 2 * _GIB)])
    result = _model_capability_preview(
        Catalog(installed=["unverified:latest", "tool-model:latest", "plain:latest"]),
        "Ollama (Local)", "plain:latest",
    )
    assert result == {
        "provider": "Ollama (Local)", "modelId": "plain:latest",
        "tools": False, "vision": False,
        "suggestedToolModel": {"provider": "Ollama (Local)", "modelId": "tool-model:latest"},
    }
    assert calls[0] == "plain:latest"


def test_failed_ollama_probe_is_unknown_and_does_not_suggest(monkeypatch):
    calls = []

    def probe(model):
        calls.append(model)
        return None

    monkeypatch.setattr(api_provider, "_get_ollama_capabilities", probe)
    result = _model_capability_preview(Catalog(installed=["tool-model"]), "Ollama", "offline")
    assert result["tools"] is None
    assert result["vision"] is None
    assert result["suggestedToolModel"] is None
    assert calls == ["offline"]


def test_cloud_catalog_only_warns_when_capabilities_are_known(monkeypatch):
    monkeypatch.setattr(api_provider, "_get_ollama_capabilities", lambda model: {"completion", "tools"})
    _verified_inventory(monkeypatch, [_tool_row("installed-tools", 2 * _GIB)])
    settings = Catalog(
        installed=["installed-tools"],
        cloud={"OpenAI": [
            {"model_id": "text-only", "capabilities": ["text"]},
            {"model_id": "multimodal", "capabilities": ["text", "vision", "tools"]},
            {"model_id": "not-described", "capabilities": []},
        ]},
    )
    text_only = _model_capability_preview(settings, "OpenAI", "text-only")
    assert text_only["tools"] is False
    assert text_only["vision"] is False
    assert text_only["suggestedToolModel"] == {"provider": "Ollama (Local)", "modelId": "installed-tools"}

    multimodal = _model_capability_preview(settings, "OpenAI", "multimodal")
    assert multimodal["tools"] is True
    assert multimodal["vision"] is True
    assert multimodal["suggestedToolModel"] is None

    for model_id in ("not-described", "not-in-catalog"):
        result = _model_capability_preview(settings, "OpenAI", model_id)
        assert result["tools"] is None
        assert result["vision"] is None
        assert result["suggestedToolModel"] is None


def test_no_inference_for_other_local_models_or_unconfigured_providers():
    settings = Catalog()
    for provider in ("Llama.cpp (Local)", "Unconfigured provider"):
        result = _model_capability_preview(settings, provider, "model.gguf")
        assert result["tools"] is None
        assert result["vision"] is None


def test_empty_ollama_capability_list_is_unknown(monkeypatch):
    monkeypatch.setattr(api_provider, "_get_ollama_capabilities", lambda model: set())
    result = _model_capability_preview(Catalog(), "Ollama (Local)", "untagged")
    assert result["tools"] is None
    assert result["vision"] is None
    assert result["suggestedToolModel"] is None


def test_suggestion_skips_oversized_gemma_and_selects_installed_qwen(monkeypatch):
    monkeypatch.setattr(api_provider, "_get_ollama_capabilities", lambda _model: {"completion"})
    # Even when the alphabetically earlier model supports tools, its estimated
    # weights + default 8k KV exceeds available RAM.
    _verified_inventory(monkeypatch, [
        _tool_row("gemma4:12b", 8 * _GIB),
        _tool_row("qwen3:8b", 4 * _GIB),
    ])
    result = _model_capability_preview(Catalog(), "Ollama (Local)", "translategemma:4b")
    assert result["tools"] is False
    assert result["suggestedToolModel"] == {"provider": "Ollama (Local)", "modelId": "qwen3:8b"}


def test_suggestion_respects_context_window_kv_estimate(monkeypatch):
    monkeypatch.setattr(api_provider, "_get_ollama_capabilities", lambda _model: {"completion"})
    _verified_inventory(monkeypatch, [_tool_row("qwen3:8b", 4 * _GIB)])
    settings = Catalog(windows={"qwen3:8b": 32_768})
    # 4 GiB weights + 32k * 128 KiB KV = 8 GiB, above the 6 GiB available.
    result = _model_capability_preview(settings, "Ollama", "translategemma:4b")
    assert result["suggestedToolModel"] is None


def test_suggestion_needs_server_verified_size_and_available_ram(monkeypatch):
    monkeypatch.setattr(api_provider, "_get_ollama_capabilities", lambda _model: {"completion"})
    _verified_inventory(monkeypatch, [_tool_row("qwen3:8b", None)])
    assert _model_capability_preview(Catalog(), "Ollama", "plain")["suggestedToolModel"] is None

    _verified_inventory(monkeypatch, [_tool_row("qwen3:8b", 2 * _GIB)], available=0)
    assert _model_capability_preview(Catalog(), "Ollama", "plain")["suggestedToolModel"] is None

    _verified_inventory(monkeypatch, [_tool_row("qwen3:8b", 2 * _GIB)])
    monkeypatch.setattr(intents_harness, "build_ollama_inventory", lambda: {
        "source": "folder", "inventory": [_tool_row("qwen3:8b", 2 * _GIB)],
    })
    assert _model_capability_preview(Catalog(), "Ollama", "plain")["suggestedToolModel"] is None
