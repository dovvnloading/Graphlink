"""Version resolution is immutable and all legacy step kinds are explicit."""
import pytest

import backend.agents  # noqa: F401 - initialize the ordinary canvas import graph
from backend.domain.model import SceneError
from backend.workflow.registry import StepAdapterRegistry
from backend.workflow.graph_steps import GRAPH_STEP_ADAPTERS, adapter_for_step, model_note_adapter


def test_registry_resolves_only_the_pinned_version():
    first, second = object(), object()
    registry = StepAdapterRegistry((("a", "1", first), ("a", "2", second)))
    assert registry.require("a", "1") is first
    assert registry.require("a", "2") is second
    assert tuple(registry) == (("a", "1"), ("a", "2"))
    with pytest.raises(KeyError):
        registry.require("a", "3")
    with pytest.raises(ValueError, match="Duplicate"):
        StepAdapterRegistry((("a", "1", first), ("a", "1", second)))
    with pytest.raises(ValueError, match="ID and version"):
        StepAdapterRegistry((("a", "", first),))


def test_all_legacy_kinds_are_registered_with_explicit_projection_policy():
    kinds = {"note", "table", "chart", "artifact", "model", "scientific", "tool", "model-note"}
    assert set(GRAPH_STEP_ADAPTERS) == {(kind, "1") for kind in kinds}
    for kind in kinds - {"model-note"}:
        adapter = adapter_for_step({"kind": kind})
        assert adapter.predictable == (kind in {"note", "table", "chart", "artifact"})
        assert adapter.fan_in == (kind in {"scientific", "tool"})
        assert adapter.local_model == (kind == "model")
        assert (adapter.pin_preview is not None) == (kind == "tool")
        assert (adapter.recover_cancelled is not None) == (kind == "scientific")
    assert model_note_adapter() is GRAPH_STEP_ADAPTERS.require("model-note", "1")
    for kind in ("unknown", "model-note"):
        with pytest.raises(SceneError, match="connection changed"):
            adapter_for_step({"kind": kind})
