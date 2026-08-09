"""ADR-018 stage 18.1: ModelRef dispatch and the unified catalog.

Layer map:
1. graphlink_model_catalog's pure data/resolution functions - ModelRef,
   choose_auto_model_ref's three policies, resolve_model_ref's chain,
   unified_catalog's aggregation. No mocking needed; these are ordinary
   functions over plain data.
2. api_provider.chat()/chat_stream() actually DISPATCHING on a supplied
   model_ref - the real exit criterion. A model_ref must both bypass the
   task-keyed lookup AND be able to route to a DIFFERENT provider than the
   session's configured mode (Ollama is always reachable regardless of
   mode - see _provider_for_model_ref's own docstring for exactly why).
3. The cross-cloud-provider mismatch: pinning to a cloud provider the
   session isn't currently configured for must raise an actionable error,
   never silently fall back to the session default or reach for
   credentials the request was never given.
"""

from __future__ import annotations

import time

import pytest

import api_provider
import graphlink_task_config as config
import graphlink_model_catalog as mc

# Captured at import time, before the autouse conftest fixture swaps
# api_provider.chat_stream for its one-chunk stub - see test_providers.py's
# own identical pattern/comment.
_REAL_CHAT_STREAM = api_provider.chat_stream


# -- layer 1: pure catalog/resolution functions ------------------------------


def _descriptor(model_id, provider, *, cost=None, capabilities=(), latency="", ready=True, available=True):
    cost_in, cost_out = cost if cost is not None else (None, None)
    return mc.ModelDescriptor(
        model_id=model_id, provider=provider, ready=ready, available=available,
        capabilities=frozenset(capabilities), cost_input_per_mtok=cost_in,
        cost_output_per_mtok=cost_out, latency_class=latency,
    )


def test_cheapest_capable_prefers_a_genuinely_free_local_model():
    catalog = [
        _descriptor("llama3", "Ollama", cost=(0.0, 0.0)),
        _descriptor("gpt-4o-mini", "OpenAI-Compatible", cost=(0.15, 0.60)),
    ]
    assert mc.choose_auto_model_ref(catalog, policy="cheapest-capable") == mc.ModelRef("Ollama", "llama3")


def test_cheapest_capable_never_treats_unknown_cost_as_free():
    # An unpriced cloud model must not look artificially cheaper than a
    # model this build actually knows the price of.
    catalog = [
        _descriptor("mystery-model", "OpenAI-Compatible", cost=(None, None)),
        _descriptor("gpt-4o-mini", "OpenAI-Compatible", cost=(0.15, 0.60)),
    ]
    assert mc.choose_auto_model_ref(catalog, policy="cheapest-capable").model_id == "gpt-4o-mini"


def test_fastest_policy_orders_by_latency_class_unknown_last():
    catalog = [
        _descriptor("slow-model", "OpenAI-Compatible", latency="slow"),
        _descriptor("fast-model", "Ollama", latency="fast"),
        _descriptor("unknown-latency", "Anthropic Claude", latency=""),
    ]
    assert mc.choose_auto_model_ref(catalog, policy="fastest") == mc.ModelRef("Ollama", "fast-model")


def test_best_quality_policy_prefers_the_priciest_known_cost():
    catalog = [
        _descriptor("claude-opus-5", "Anthropic Claude", cost=(15.0, 75.0)),
        _descriptor("claude-haiku", "Anthropic Claude", cost=(0.80, 4.0)),
    ]
    ref = mc.choose_auto_model_ref(catalog, policy="best-quality")
    assert ref == mc.ModelRef("Anthropic Claude", "claude-opus-5")


def test_auto_never_picks_a_model_missing_a_required_capability():
    # THE binary exit criterion for stage 18.4: a vision request must never
    # resolve to a text-only model, regardless of policy.
    catalog = [
        _descriptor("cheap-text-only", "Ollama", cost=(0.0, 0.0), capabilities={"text"}),
        _descriptor("pricier-vision", "OpenAI-Compatible", cost=(5.0, 10.0), capabilities={"text", "vision"}),
    ]
    ref = mc.choose_auto_model_ref(catalog, {"vision"}, policy="cheapest-capable")
    assert ref == mc.ModelRef("OpenAI-Compatible", "pricier-vision")


def test_auto_returns_none_when_nothing_in_the_catalog_is_capable():
    catalog = [_descriptor("text-only", "Ollama", capabilities={"text"})]
    assert mc.choose_auto_model_ref(catalog, {"vision"}) is None


def test_auto_skips_unready_and_unavailable_entries():
    catalog = [
        _descriptor("not-ready", "Ollama", cost=(0.0, 0.0), ready=False),
        _descriptor("not-available", "Ollama", cost=(0.0, 0.0), available=False),
        _descriptor("the-only-usable-one", "OpenAI-Compatible", cost=(5.0, 5.0)),
    ]
    assert mc.choose_auto_model_ref(catalog).model_id == "the-only-usable-one"


def test_resolution_chain_prefers_node_over_branch_over_workspace_over_auto():
    catalog = [_descriptor("auto-pick", "Ollama", cost=(0.0, 0.0))]
    node_ref = mc.ModelRef("Anthropic Claude", "node-pinned")
    branch_ref = mc.ModelRef("Anthropic Claude", "branch-pinned")
    workspace_ref = mc.ModelRef("Anthropic Claude", "workspace-default")

    all_four = mc.resolve_model_ref(
        "task_chat", node_ref=node_ref, branch_ref=branch_ref, workspace_ref=workspace_ref, catalog=catalog,
    )
    assert all_four == mc.ResolvedModel(node_ref, "node override")

    no_node = mc.resolve_model_ref(
        "task_chat", branch_ref=branch_ref, workspace_ref=workspace_ref, catalog=catalog,
    )
    assert no_node == mc.ResolvedModel(branch_ref, "branch override")

    only_workspace = mc.resolve_model_ref("task_chat", workspace_ref=workspace_ref, catalog=catalog)
    assert only_workspace == mc.ResolvedModel(workspace_ref, "workspace default")

    nothing_pinned = mc.resolve_model_ref("task_chat", catalog=catalog)
    assert nothing_pinned == mc.ResolvedModel(mc.ModelRef("Ollama", "auto-pick"), "auto: cheapest-capable")


def test_resolution_chain_returns_none_when_every_rung_is_empty():
    assert mc.resolve_model_ref("task_chat", catalog=()) is None


def test_an_explicit_override_is_never_capability_filtered():
    # A human's (or an inherited human's) explicit pin is trusted even
    # against a capability the catalog says it lacks - only auto enforces
    # the filter (ModelDescriptor.supports' own established posture).
    node_ref = mc.ModelRef("OpenAI-Compatible", "text-only-model")
    resolved = mc.resolve_model_ref(
        "task_chart",  # requires text+code per TASK_REQUIREMENTS
        node_ref=node_ref, catalog=[], required_capabilities={"vision"},
    )
    assert resolved == mc.ResolvedModel(node_ref, "node override")


class _FakeSettingsManager:
    def __init__(self, *, ollama=(), llama_cpp=(), api_catalogs=None):
        self._ollama = list(ollama)
        self._llama_cpp = list(llama_cpp)
        self._api_catalogs = api_catalogs or {}

    def get_ollama_scanned_models(self):
        return list(self._ollama)

    def get_llama_cpp_scanned_models(self):
        return list(self._llama_cpp)

    def get_api_model_catalog(self, provider):
        return list(self._api_catalogs.get(provider, ()))


def test_unified_catalog_aggregates_every_configured_source():
    settings = _FakeSettingsManager(
        ollama=["llama3", "qwen3:8b"],
        llama_cpp=["local-model.gguf"],
        api_catalogs={
            "Anthropic Claude": [{"model_id": "claude-opus-5", "capabilities": ["text"]}],
            "OpenAI-Compatible": [{"model_id": "gpt-4o", "ready": False}],
        },
    )
    catalog = mc.unified_catalog(settings)
    # "descriptor" (never a bare 1-2 letter name) so this reads unambiguously
    # as a ModelDescriptor, not a SceneNode - see
    # tests/test_node_state_migration.py's own _KNOWN_NON_NODE_FIELD_ACCESS_
    # SHAPES entry for this exact file/root pair.
    by_id = {(descriptor.provider, descriptor.model_id): descriptor for descriptor in catalog}

    assert ("Ollama", "llama3") in by_id
    assert ("Ollama", "qwen3:8b") in by_id
    assert ("Llama.cpp", "local-model.gguf") in by_id
    assert by_id[("Anthropic Claude", "claude-opus-5")].capabilities == frozenset({"text"})
    assert by_id[("OpenAI-Compatible", "gpt-4o")].ready is False


def test_unified_catalog_applies_the_supplied_price_lookup():
    settings = _FakeSettingsManager(ollama=["llama3"])
    catalog = mc.unified_catalog(settings, price_lookup=lambda provider, model_id: (0.0, 0.0))
    assert catalog[0].cost_input_per_mtok == 0.0
    assert catalog[0].cost_output_per_mtok == 0.0


def test_unified_catalog_with_no_settings_manager_returns_empty():
    assert mc.unified_catalog(None) == []


# -- layer 2/3: api_provider dispatch actually honoring model_ref -----------


class _FakeOllamaStream:
    def __init__(self, parts):
        self._iter = iter(parts)

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._iter)

    def close(self):
        pass


def _part(content="", done=False):
    return {"message": {"content": content}, "done": done}


class _FakeOllamaChat:
    def __init__(self, streams=None, responses=None):
        self.streams = list(streams or [])
        self.responses = list(responses or [])
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return self.streams.pop(0)
        return self.responses.pop(0)


@pytest.fixture
def ollama_chat(monkeypatch):
    fake = _FakeOllamaChat()
    import ollama

    monkeypatch.setattr(ollama, "chat", fake)
    return fake


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def test_model_ref_bypasses_the_task_keyed_lookup_entirely(monkeypatch, ollama_chat):
    """THE 18.1 exit criterion: chat_stream dispatches on a supplied
    model_ref, not on config.OLLAMA_MODELS[task] - proven by leaving the
    task table completely UNCONFIGURED (the pre-18.1 code would raise "No
    Ollama model configured for task") while the call still succeeds."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "")  # deliberately unconfigured

    ollama_chat.streams = [_FakeOllamaStream([_part(content="hi", done=True)])]

    chunks = []
    response = api_provider.chat_stream(
        config.TASK_CHAT,
        [{"role": "user", "content": "hello"}],
        lambda delta, reset: chunks.append((delta, reset)),
        model_ref=mc.ModelRef("Ollama", "pinned-model:8b"),
    )

    assert response["message"]["content"] == "hi"
    assert ollama_chat.calls[0]["model"] == "pinned-model:8b"


def test_model_ref_can_route_to_ollama_while_session_is_in_api_mode(monkeypatch, ollama_chat):
    """The mixed local+cloud comparison scenario the ADR's context section
    describes: a node/branch override can pin to a local Ollama model even
    though the session's CONFIGURED default is a cloud provider - Ollama
    needs no credentials, so it is always constructible."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_CLIENT", object())
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")

    ollama_chat.streams = [_FakeOllamaStream([_part(content="local reply", done=True)])]

    response = api_provider.chat_stream(
        config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None,
        model_ref=mc.ModelRef("Ollama", "local-model"),
    )
    assert response["message"]["content"] == "local reply"


def test_model_ref_naming_a_different_cloud_provider_than_the_session_raises_actionably(monkeypatch):
    """decision #3's "unresolvable produces an actionable error, not a
    silent wrong-model call" - a branch pinned to Anthropic while the
    session's configured API provider is OpenAI must fail clearly, not
    silently dispatch to OpenAI with Anthropic's model id, and not silently
    fall back to the session default."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_CLIENT", object())
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")

    with pytest.raises(RuntimeError, match="Anthropic Claude"):
        api_provider.chat_stream(
            config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None,
            model_ref=mc.ModelRef("Anthropic Claude", "claude-opus-5"),
        )


def test_chat_blocking_call_also_honors_model_ref(monkeypatch, ollama_chat):
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_TITLE, "")

    ollama_chat.responses = [{"message": {"content": "A Title"}}]

    response = api_provider.chat(
        config.TASK_TITLE, [{"role": "user", "content": "name this"}],
        model_ref=mc.ModelRef("Ollama", "pinned-title-model"),
    )
    assert response["message"]["content"] == "A Title"
    assert ollama_chat.calls[0]["model"] == "pinned-title-model"
