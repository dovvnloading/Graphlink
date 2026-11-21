"""Model pricing: strict writes, effective sources, and reactive cost projections."""

from __future__ import annotations

import asyncio

import pytest

from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.settings import register_settings
from backend.token_counter import TokenCounterState
from graphlink_settings_store import SettingsManager


def _setup(tmp_path):
    path = tmp_path / "pricing.dat"
    manager = SettingsManager(path)
    document = SceneDocument()
    counter = TokenCounterState(pricing_overrides_fn=manager.get_pricing_overrides)
    document.reply_cost_estimator = counter.estimate_cost_for
    bus = SessionBus("model-pricing-test")
    bus.register_topic(
        "scene", document.scene_payload,
        patch_builder=document.take_dirty_patch_ops,
        baseline_builder=document.published_scene_payload,
        schema_version=2, min_compatible=2,
    )
    bus.register_topic("token-counter", counter.payload)
    register_settings(bus, manager, document=document)
    return path, manager, document, counter, bus


def _run(bus, name, *args):
    return asyncio.run(bus.dispatch_intent("app-settings", name, list(args)))


def test_get_save_remove_persist_exact_id_overrides_and_effective_prices(tmp_path):
    path, manager, document, _, bus = _setup(tmp_path)
    manager.set_api_models({"task_chat": "gpt-4o-mini"}, "OpenAI-Compatible")
    manager.set_ollama_chat_model("tinyllama:latest")
    manager.set_llama_cpp_chat_model_path("/models/tiny.gguf")
    user = document.add_chat_node(0, 0, "hi", True)
    reply = document.add_chat_node(0, 150, "hello", False, parent_id=user.id)
    reply.state.provider = "OpenAI-Compatible"
    reply.state.model = "gpt-4o-mini"

    before = bus._topics["app-settings"].builder()
    by_model = {(row["provider"], row["modelId"]): row for row in before["effectiveModelPrices"]}
    cloud = by_model["OpenAI-Compatible", "gpt-4o-mini"]
    assert (cloud["inputPerMillion"], cloud["outputPerMillion"], cloud["source"]) == (0.15, 0.6, "built-in")
    for provider, model in (("ollama", "tinyllama:latest"), ("llama.cpp", "/models/tiny.gguf")):
        assert by_model[provider, model]["source"] == "local"
        assert by_model[provider, model]["inputPerMillion"] == 0
        assert by_model[provider, model]["outputPerMillion"] == 0

    assert _run(bus, "getPricingOverrides") == {}
    saved = _run(bus, "setPricingOverrides", {
        " GPT-4O-MINI ": {"input": 2.0, "output": 4.0},
        "custom-exact-id": {"input": 0, "output": 7.5},
    })
    assert saved == {
        "gpt-4o-mini": {"input": 2.0, "output": 4.0},
        "custom-exact-id": {"input": 0.0, "output": 7.5},
    }
    assert SettingsManager(path).get_pricing_overrides() == saved
    payload = bus._topics["app-settings"].builder()
    prices = {(row["provider"], row["modelId"]): row for row in payload["effectiveModelPrices"]}
    assert prices["OpenAI-Compatible", "gpt-4o-mini"]["source"] == "override"
    assert prices["OpenAI-Compatible", "gpt-4o-mini"]["inputPerMillion"] == 2
    assert prices["", "custom-exact-id"]["source"] == "override"
    assert len(payload["pricingOverrides"]) == 2

    _run(bus, "setPricingOverrides", {})
    assert _run(bus, "getPricingOverrides") == {}
    assert SettingsManager(path).get_pricing_overrides() == {}
    again = bus._topics["app-settings"].builder()["effectiveModelPrices"]
    assert next(row for row in again if row["modelId"] == "gpt-4o-mini")["source"] == "built-in"


@pytest.mark.parametrize("bad", [
    None, [], {"": {"input": 1, "output": 1}},
    {"  ": {"input": 1, "output": 1}},
    {"m": {"input": -1, "output": 1}},
    {"m": {"input": 1, "output": -0.001}},
    {"m": {"input": True, "output": 0}},
    {"m": {"input": "1", "output": 0}},
    {"m": {"input": float("inf"), "output": 0}},
    {"m": {"input": float("nan"), "output": 0}},
    {"m": {"input": 0}},
    {"m": {"input": 0, "output": 0, "extra": 1}},
    {"M": {"input": 0, "output": 0}, "m": {"input": 0, "output": 0}},
])
def test_invalid_pricing_is_rejected_atomically(tmp_path, bad):
    _, manager, _, _, bus = _setup(tmp_path)
    baseline = {"known": {"input": 3, "output": 5}}
    _run(bus, "setPricingOverrides", baseline)
    with pytest.raises(ValueError, match="Model|price|Duplicate"):
        _run(bus, "setPricingOverrides", bad)
    assert manager.get_pricing_overrides() == baseline


def test_overrides_refresh_reply_wire_and_topics_without_mutating_usage_or_undo(tmp_path):
    _, _, document, counter, bus = _setup(tmp_path)
    user = document.add_chat_node(0, 0, "input", True)
    reply = document.add_chat_node(0, 120, "response", False, parent_id=user.id)
    reply.state.provider = "OpenAI-Compatible"
    reply.state.model = "gpt-4o-mini"
    reply.state.prompt_tokens = 1_000_000
    reply.state.completion_tokens = 1_000_000
    reply.state.estimated_cost_usd = 0.75
    counter.set_real_usage(1_000_000, 1_000_000, provider="OpenAI-Compatible", model="gpt-4o-mini")
    undo_count = len(document.command_log)

    def cost():
        row = next(row for row in document.scene_payload()["nodes"] if row["id"] == reply.id)
        return row["estimatedCostUsd"]

    assert cost() == 0.75
    assert counter.payload()["estimatedCostUsd"] == 0.75
    asyncio.run(bus.publish("scene"))
    _run(bus, "setPricingOverrides", {"gpt-4o-mini": {"input": 2, "output": 4}})
    assert cost() == 6.0
    assert counter.payload()["estimatedCostUsd"] == 6.0
    assert document.published_scene_payload()["nodes"]
    assert reply.state.estimated_cost_usd == 0.75
    assert (reply.state.prompt_tokens, reply.state.completion_tokens) == (1_000_000, 1_000_000)
    assert len(document.command_log) == undo_count
    _run(bus, "setPricingOverrides", {})
    assert cost() == 0.75
