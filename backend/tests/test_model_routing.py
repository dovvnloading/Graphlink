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
    def __init__(self, *, ollama=(), llama_cpp=(), api_catalogs=None, auto_policy=mc.AUTO_POLICY_CHEAPEST_CAPABLE):
        self._ollama = list(ollama)
        self._llama_cpp = list(llama_cpp)
        self._api_catalogs = api_catalogs or {}
        self._auto_policy = auto_policy

    def get_ollama_scanned_models(self):
        return list(self._ollama)

    def get_llama_cpp_scanned_models(self):
        return list(self._llama_cpp)

    def get_api_model_catalog(self, provider):
        return list(self._api_catalogs.get(provider, ()))

    # ADR-018 stage 18.4: read by _auto_fallback_model_ref (api_provider.py)
    # - get_pricing_overrides feeds the SAME price_lookup unified_catalog
    # applies everywhere else, so a real backend.token_counter.price_per_mtok
    # call always succeeds against this fake.
    def get_auto_model_policy(self):
        return self._auto_policy

    def get_pricing_overrides(self):
        return {}


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


# -- stage 18.4: the auto-fallback rung, wired into LIVE dispatch -----------
#
# 18.1 already proves choose_auto_model_ref/resolve_model_ref are correct as
# pure functions (including the capability-filter invariant). What was still
# untested before this stage: that api_provider.chat()/chat_stream() ever
# actually CALL those functions on the real "no model configured" path, with
# a real SettingsManager-shaped catalog and a real
# backend.token_counter.price_per_mtok price_lookup - not just that the
# functions themselves behave when invoked directly by a test.


def test_auto_fallback_fires_when_the_ollama_task_lookup_is_empty(monkeypatch, ollama_chat):
    """THE 18.4 exit criterion for the local branch: a task with NOTHING
    configured in config.OLLAMA_MODELS (the pre-18.4 code raises "No Ollama
    model configured") now dispatches anyway when a settings_manager with a
    scanned model is supplied."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "")  # deliberately unconfigured

    settings = _FakeSettingsManager(ollama=["auto-picked:8b"])
    ollama_chat.streams = [_FakeOllamaStream([_part(content="auto reply", done=True)])]

    response = api_provider.chat_stream(
        config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None,
        settings_manager=settings,
    )
    assert response["message"]["content"] == "auto reply"
    assert ollama_chat.calls[0]["model"] == "auto-picked:8b"


def test_auto_fallback_fires_for_the_blocking_ollama_call_too(monkeypatch, ollama_chat):
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_TITLE, "")

    settings = _FakeSettingsManager(ollama=["auto-title-model"])
    ollama_chat.responses = [{"message": {"content": "Auto Title"}}]

    response = api_provider.chat(
        config.TASK_TITLE, [{"role": "user", "content": "name this"}],
        settings_manager=settings,
    )
    assert response["message"]["content"] == "Auto Title"
    assert ollama_chat.calls[0]["model"] == "auto-title-model"


def test_without_a_settings_manager_the_original_no_model_error_is_unchanged(monkeypatch):
    """Backward-compat pin: every pre-18.4 caller (nothing threads
    settings_manager) must keep raising the exact original message - the
    auto rung is additive, never a silent behavior change for callers that
    don't opt in."""
    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "")

    with pytest.raises(ValueError, match="No Ollama model configured for task"):
        api_provider.chat_stream(config.TASK_CHAT, [{"role": "user", "content": "hi"}], lambda d, r: None)

    with pytest.raises(ValueError, match="No Ollama model configured for task"):
        api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}])


def test_auto_fallback_fires_for_the_api_mode_branch_and_honors_the_persisted_policy(monkeypatch):
    """The API-mode sibling of the Ollama tests above, combined with the
    18.4 setting itself: two OpenAI catalog entries with real, DIFFERENT
    known prices (via the real backend.token_counter pricing table, not a
    stub) - "cheapest-capable" must pick the cheap one, "best-quality" must
    pick the priciest KNOWN-cost one. Proves both that the auto rung fires
    on the API-mode "no api_model configured" branch and that
    SettingsManager.get_auto_model_policy is actually consulted, not just
    unified_catalog's aggregation."""
    import types

    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, None)  # deliberately unconfigured

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
        )

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(api_provider, "API_CLIENT", client)

    catalog = {
        "OpenAI-Compatible": [
            {"model_id": "gpt-4o-mini", "capabilities": ["text"]},   # cheap, known price
            {"model_id": "gpt-4o", "capabilities": ["text"]},        # pricier, known price
        ],
    }

    cheap_first = _FakeSettingsManager(api_catalogs=catalog, auto_policy=mc.AUTO_POLICY_CHEAPEST_CAPABLE)
    api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}], settings_manager=cheap_first)
    assert captured["model"] == "gpt-4o-mini"

    captured.clear()
    quality_first = _FakeSettingsManager(api_catalogs=catalog, auto_policy=mc.AUTO_POLICY_BEST_QUALITY)
    api_provider.chat(config.TASK_CHAT, [{"role": "user", "content": "hi"}], settings_manager=quality_first)
    assert captured["model"] == "gpt-4o"


def test_auto_fallback_never_dispatches_a_capability_incapable_model_live(monkeypatch):
    """The live-dispatch counterpart to choose_auto_model_ref's own pure-
    function capability test above: task_chart requires {text, code}
    (TASK_REQUIREMENTS) - a catalog where the cheaper candidate lacks
    "code" must still result in the code-capable model actually being
    constructed and called, never the cheaper incapable one."""
    import types

    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHART, None)

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
        )

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(api_provider, "API_CLIENT", client)

    settings = _FakeSettingsManager(api_catalogs={
        "OpenAI-Compatible": [
            # Cheaper (unknown price sorts last under cheapest-capable, but
            # even a KNOWN cheap price must lose here - it lacks "code").
            {"model_id": "gpt-4o-mini", "capabilities": ["text"]},
            {"model_id": "gpt-4o", "capabilities": ["text", "code"]},
        ],
    })

    api_provider.chat(config.TASK_CHART, [{"role": "user", "content": "build a chart"}], settings_manager=settings)
    assert captured["model"] == "gpt-4o"


def test_auto_fallback_never_crosses_to_a_cheaper_model_from_a_different_cloud_provider(monkeypatch):
    """Reuses _provider_for_model_ref's own single-live-cloud-credential
    posture (18.1): even though the persisted catalog has a much cheaper
    Anthropic entry, the session's live client is OpenAI - the auto rung
    must never resolve to a ref _provider_for_model_ref would then reject,
    so it falls through to the OpenAI catalog entry, not the cheaper
    Anthropic one."""
    import types

    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, None)

    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
        )

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(api_provider, "API_CLIENT", client)

    settings = _FakeSettingsManager(api_catalogs={
        "Anthropic Claude": [{"model_id": "claude-haiku", "capabilities": ["text"]}],  # far cheaper
        "OpenAI-Compatible": [{"model_id": "gpt-4o", "capabilities": ["text"]}],
    })

    response = api_provider.chat(
        config.TASK_CHAT, [{"role": "user", "content": "hi"}], settings_manager=settings,
    )
    assert response["message"]["content"] == "ok"
    assert captured["model"] == "gpt-4o"


# -- stage 18.5: fallback chains with visible substitution ------------------
#
# The 18.4 tests above prove the auto rung fires when NOTHING is configured.
# This section proves the DIFFERENT scenario stage 18.5 targets: something
# IS configured and working, but fails at request time - "Ollama-down falls
# back and says so" (the ADR's own literal exit criterion). Exercised from
# the cloud-down/local-fallback direction (API mode fails, Ollama - always
# constructible - is the fallback) since it reuses the existing
# ollama_chat/_fake_openai_client fixtures without needing to fake
# llama.cpp's SDK too; the reverse direction (Ollama down, cloud/llama.cpp
# fallback) is the SAME code path with the exclude_provider argument
# flipped, not a distinct branch.


def _raising_openai_client(exc: Exception):
    import types

    def create(**kwargs):
        raise exc

    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def test_fallback_fires_for_a_fallback_enabled_task_when_the_configured_provider_is_down(
    monkeypatch, ollama_chat, no_backoff,
):
    """THE 18.5 exit criterion: task_title (naming - fallback-enabled by
    default) is fully CONFIGURED for OpenAI, but the client is down
    (connection refused, retried and exhausted exactly like ADR-006
    section 6 already does for same-provider transport blips) - the reply
    still succeeds, via Ollama, and on_fallback is told about it."""
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_TITLE, "gpt-4o-mini")  # genuinely configured
    monkeypatch.setattr(
        api_provider, "API_CLIENT", _raising_openai_client(ConnectionError("Connection refused")),
    )

    settings = _FakeSettingsManager(ollama=["fallback-model:8b"])
    ollama_chat.responses = [{"message": {"content": "A Title"}}]

    fallback_calls = []
    response = api_provider.chat(
        config.TASK_TITLE, [{"role": "user", "content": "name this"}],
        settings_manager=settings,
        on_fallback=lambda failed_provider, ref, exc: fallback_calls.append((failed_provider, ref, exc)),
    )

    assert response["message"]["content"] == "A Title"
    assert ollama_chat.calls[0]["model"] == "fallback-model:8b"
    assert len(fallback_calls) == 1
    failed_provider, ref, exc = fallback_calls[0]
    assert failed_provider == config.API_PROVIDER_OPENAI
    assert ref == mc.ModelRef("Ollama", "fallback-model:8b")
    assert isinstance(exc, ConnectionError)


def test_fallback_fires_for_chat_stream_too(monkeypatch, ollama_chat, no_backoff):
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_WEB_VALIDATE, "gpt-4o-mini")
    monkeypatch.setattr(
        api_provider, "API_CLIENT", _raising_openai_client(ConnectionError("Connection refused")),
    )

    settings = _FakeSettingsManager(ollama=["fallback-model:8b"])
    ollama_chat.streams = [_FakeOllamaStream([_part(content="validated", done=True)])]

    chunks = []
    fallback_calls = []
    response = api_provider.chat_stream(
        config.TASK_WEB_VALIDATE, [{"role": "user", "content": "assess this"}],
        lambda d, r: chunks.append((d, r)),
        settings_manager=settings,
        on_fallback=lambda failed_provider, ref, exc: fallback_calls.append((failed_provider, ref)),
    )

    assert response["message"]["content"] == "validated"
    assert ollama_chat.calls[0]["model"] == "fallback-model:8b"
    assert fallback_calls == [(config.API_PROVIDER_OPENAI, mc.ModelRef("Ollama", "fallback-model:8b"))]
    # The primary (failing) attempt never reached on_chunk - only the
    # fallback attempt's real delta arrives.
    assert chunks == [("validated", False)]


def test_correctness_sensitive_tasks_never_fall_back(monkeypatch, no_backoff):
    """task_chat is NOT in FALLBACK_ENABLED_TASKS - "off by default for
    correctness-sensitive tasks" per the ADR's own decision #4. The same
    down-provider setup as the tests above must surface the real error,
    never silently swap to a different model the user never asked for."""
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_CHAT, "gpt-4o-mini")
    monkeypatch.setattr(
        api_provider, "API_CLIENT", _raising_openai_client(ConnectionError("Connection refused")),
    )

    settings = _FakeSettingsManager(ollama=["fallback-model:8b"])
    fallback_calls = []

    with pytest.raises(ConnectionError):
        api_provider.chat(
            config.TASK_CHAT, [{"role": "user", "content": "hi"}],
            settings_manager=settings,
            on_fallback=lambda *args: fallback_calls.append(args),
        )

    assert fallback_calls == []


def test_fallback_never_fires_once_the_stream_has_delivered_real_text(monkeypatch, no_backoff):
    """The streaming-specific guard: chat_stream's own module docstring
    already establishes transport retry is legal ONLY before anything
    reaches on_chunk - stage 18.5 extends that invariant to the cross-model
    fallback attempt too. A provider that streams some real text and THEN
    fails must surface the failure, never silently start a second reply
    from a different model (which would look like corrupted/duplicated
    output to the user)."""
    monkeypatch.setattr(api_provider, "chat_stream", _REAL_CHAT_STREAM)
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_TITLE, "gpt-4o-mini")

    import types

    def _text_chunk(content):
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=content), finish_reason=None)],
        )

    class _PartialThenFailStream:
        def __init__(self):
            self._delivered = False

        def __iter__(self):
            return self

        def __next__(self):
            if not self._delivered:
                self._delivered = True
                return _text_chunk("partial")
            raise ConnectionError("Connection refused mid-stream")

        def close(self):
            pass

    def create(**kwargs):
        assert kwargs.get("stream") is True
        return _PartialThenFailStream()

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(api_provider, "API_CLIENT", client)

    settings = _FakeSettingsManager(ollama=["fallback-model:8b"])
    fallback_calls = []
    chunks = []

    with pytest.raises(ConnectionError):
        api_provider.chat_stream(
            config.TASK_TITLE, [{"role": "user", "content": "name this"}], lambda d, r: chunks.append((d, r)),
            settings_manager=settings,
            on_fallback=lambda *args: fallback_calls.append(args),
        )

    assert chunks == [("partial", False)]
    assert fallback_calls == []


def test_fallback_never_fires_on_cancellation(monkeypatch, no_backoff):
    monkeypatch.setattr(api_provider, "USE_API_MODE", True)
    monkeypatch.setattr(api_provider, "API_PROVIDER_TYPE", config.API_PROVIDER_OPENAI)
    monkeypatch.setattr(api_provider, "API_KEY", "sk-fake")
    monkeypatch.setitem(api_provider.API_MODELS, config.TASK_TITLE, "gpt-4o-mini")
    monkeypatch.setattr(
        api_provider, "API_CLIENT", _raising_openai_client(api_provider.RequestCancelledError("Request cancelled.")),
    )

    settings = _FakeSettingsManager(ollama=["fallback-model:8b"])
    fallback_calls = []

    with pytest.raises(api_provider.RequestCancelledError):
        api_provider.chat(
            config.TASK_TITLE, [{"role": "user", "content": "name this"}],
            settings_manager=settings,
            on_fallback=lambda *args: fallback_calls.append(args),
        )

    assert fallback_calls == []


# -- review-fix regressions --------------------------------------------------


def test_unified_catalog_reduces_llama_cpp_scanned_paths_to_a_basename():
    """Review-fix regression: SettingsManager.get_llama_cpp_scanned_models()
    returns FULL scanned paths (confirmed by backend/tests/test_settings.py's
    own test_scan_llama_cpp_system_persists_results_and_reports_done, which
    asserts a raw "C:/models/a.gguf" path), but _provider_for_model_ref's
    llama.cpp branch only ever accepts a model_id matching the BASENAME of a
    configured path - the same convention describe_active_model already
    uses. Before this fix, unified_catalog stored the raw scanned path
    verbatim, so any auto/fallback pick landing on a llama.cpp candidate
    would be unconditionally rejected by _provider_for_model_ref."""
    settings = _FakeSettingsManager(llama_cpp=["C:/models/local-model.gguf"])
    catalog = mc.unified_catalog(settings)
    assert catalog[0].provider == "Llama.cpp"
    assert catalog[0].model_id == "local-model.gguf"


def test_auto_fallback_can_actually_dispatch_to_a_scanned_llama_cpp_model(monkeypatch):
    """Live-dispatch counterpart to the pure-function test above: the
    catalog's basename-reduced llama.cpp candidate must not just LOOK
    right, it must actually be constructible by _provider_for_model_ref and
    complete a real (faked) request."""
    from backend.providers import llama_cpp_provider as lp

    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_CHAT, "")  # deliberately unconfigured
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "chat_model_path", "C:/models/local-model.gguf")

    class FakeLlamaClient:
        def create_chat_completion(self, messages=None, **kwargs):
            return {"choices": [{"message": {"content": "llama answer"}}]}

    monkeypatch.setattr(lp, "_get_llama_cpp_client", lambda task, s: FakeLlamaClient())

    settings = _FakeSettingsManager(llama_cpp=["C:/models/local-model.gguf"])
    response = api_provider.chat(
        config.TASK_CHAT, [{"role": "user", "content": "hi"}], settings_manager=settings,
    )
    assert response["message"]["content"] == "llama answer"


def test_auto_pick_recursion_preserves_settings_manager_for_a_further_fallback(monkeypatch, no_backoff):
    """Review-fix regression: when NOTHING is configured for a fallback-
    enabled task (task_title), chat()'s "no model configured" branch
    recurses into itself with an auto-picked model_ref (18.4). Before this
    fix, that recursive call silently dropped settings_manager, so if the
    auto-picked model then ALSO failed, 18.5's fallback-on-failure could
    never fire - the ONLY population of requests this would ever affect
    (auto-picked because nothing was configured) is exactly the same
    population FALLBACK_ENABLED_TASKS targets. Ollama (alphabetically
    first, so the auto-pick's own tie-break lands here) is down for every
    attempt; llama.cpp is the only OTHER configured provider, so a
    surviving fallback can only mean settings_manager reached the SECOND
    dispatch too."""
    from backend.providers import llama_cpp_provider as lp

    monkeypatch.setattr(api_provider, "USE_API_MODE", False)
    monkeypatch.setattr(api_provider, "LOCAL_PROVIDER_TYPE", config.LOCAL_PROVIDER_OLLAMA)
    monkeypatch.setitem(config.OLLAMA_MODELS, config.TASK_TITLE, "")  # deliberately unconfigured
    monkeypatch.setitem(api_provider.LLAMA_CPP_SETTINGS, "chat_model_path", "C:/models/fallback.gguf")

    import ollama
    monkeypatch.setattr(ollama, "chat", lambda **kwargs: (_ for _ in ()).throw(ConnectionError("Connection refused")))

    class FakeLlamaClient:
        def create_chat_completion(self, messages=None, **kwargs):
            return {"choices": [{"message": {"content": "llama fallback answered"}}]}

    monkeypatch.setattr(lp, "_get_llama_cpp_client", lambda task, s: FakeLlamaClient())

    settings = _FakeSettingsManager(ollama=["auto-picked-ollama"], llama_cpp=["C:/models/fallback.gguf"])
    fallback_calls = []
    response = api_provider.chat(
        config.TASK_TITLE, [{"role": "user", "content": "name this"}],
        settings_manager=settings,
        on_fallback=lambda *args: fallback_calls.append(args),
    )

    assert response["message"]["content"] == "llama fallback answered"
    # on_fallback itself is a KNOWN, documented gap for this specific
    # compound scenario (see the two "settings_manager re-included" review-
    # fix comments in api_provider.py's recursive auto-pick branches): the
    # wrapper that owns on_fallback already popped it before ever calling
    # into this recursive path, so it is out of scope here - the important,
    # PREVIOUSLY-BROKEN thing this test pins is that the reply still
    # succeeds at all.
    assert fallback_calls == []
