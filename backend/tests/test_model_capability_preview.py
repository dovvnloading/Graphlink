"""Capability warnings use explicit evidence, never provider-family guesses."""

import api_provider

from backend.api.intents_harness import _model_capability_preview


class Catalog:
    def __init__(self, *, installed=(), cloud=None):
        self.installed = list(installed)
        self.cloud = cloud or {}

    def get_ollama_scanned_models(self):
        return self.installed

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
