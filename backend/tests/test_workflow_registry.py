"""Version resolution is immutable and all legacy step kinds are explicit."""
import pytest

import backend.agents  # noqa: F401 - initialize the ordinary canvas import graph
from backend.domain.model import SceneError
from backend.workflow.contracts import TextValue, WorkflowStepSpec, freeze_config
from backend.workflow.registry import StepAdapterRegistry, builtin_registry
from backend.workflow.graph_steps import GRAPH_STEP_ADAPTERS, adapter_for_step, model_note_adapter


def test_registry_resolves_only_the_pinned_version():
    first, second = object(), object()
    source = StepAdapterRegistry()
    source.register("a", "1", first)
    registry = source.with_entries((("a", "2", second),))
    assert registry.resolve("a", "1") is first
    assert registry.resolve("a", "2") is second
    assert registry.keys() == (("a", "1"), ("a", "2"))
    assert source.keys() == (("a", "1"),)
    with pytest.raises(KeyError, match="unknown adapter"):
        registry.resolve("a", "3")
    with pytest.raises(ValueError, match="already registered"):
        source.with_entries((("a", "1", second),))
    with pytest.raises(ValueError, match="nonempty"):
        source.with_entries((("a", "", first),))
    with pytest.raises(TypeError, match="frozen"):
        registry.register("a", "3", first)


def test_all_legacy_kinds_are_registered_with_explicit_projection_policy():
    kinds = {"note", "table", "chart", "artifact", "model", "scientific", "tool", "model-note"}
    assert set(GRAPH_STEP_ADAPTERS.keys()) == {
        (kind, "1") for kind in kinds
    } | set(builtin_registry().keys())
    for kind in kinds - {"model-note"}:
        adapter = adapter_for_step({"kind": kind})
        assert adapter.predictable == (kind in {"note", "table", "chart", "artifact"})
        assert adapter.fan_in == (kind in {"scientific", "tool"})
        assert adapter.local_model == (kind == "model")
        assert (adapter.pin_preview is not None) == (kind == "tool")
        assert (adapter.recover_cancelled is not None) == (kind == "scientific")
    assert model_note_adapter() is GRAPH_STEP_ADAPTERS.resolve("model-note", "1")
    for kind in ("unknown", "model-note", "transform.template"):
        with pytest.raises(SceneError, match="connection changed"):
            adapter_for_step({"kind": kind})

def test_combined_registry_preserves_typed_transform_preview():
    registry = GRAPH_STEP_ADAPTERS
    config = freeze_config({"template": "Hi {name}"})
    descriptor = registry.describe("transform.template", "1", config)
    adapter = registry.resolve("transform.template", "1")
    spec = WorkflowStepSpec("step", descriptor, config, ())
    prepared = adapter.prepare(spec, {"name": TextValue("Ada")})
    assert adapter.preview(prepared) == {"text": {"kind": "text", "characters": 6}}
    with pytest.raises(TypeError, match="no typed workflow descriptor"):
        registry.describe("note", "1", freeze_config({}))
    assert ("note", "1") not in builtin_registry().keys()
