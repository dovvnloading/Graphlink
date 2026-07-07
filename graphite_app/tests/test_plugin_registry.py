"""Tests for the Phase 1 plugin registry (graphite_plugin_portal.PLUGIN_REGISTRY).

doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md documents that plugin metadata is hand-copied in
several independent places across the app (PluginPortal's _register_plugin calls,
graphite_plugin_quality_gate.py's _node_label, graphite_plugin_workflow.py's
WORKFLOW_PLUGIN_ICONS, graphite_window_actions.py's _seed_plugin_prompt isinstance
chain) and that this drift already caused a live bug (Workflow's allowlist silently
omitted Code Review Agent). PLUGIN_REGISTRY is meant to become the single source of
truth those should eventually read from - these tests cross-check it against the
existing hand-maintained lists so registry/reality drift is caught immediately
instead of being discovered by a future audit.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphite_plugins.graphite_plugin_portal import (
    PLUGIN_REGISTRY,
    PluginPortal,
    get_display_name_for_node,
    get_plugin_spec,
)

_WINDOW_ACTIONS_SOURCE = (Path(__file__).resolve().parents[1] / "graphite_window_actions.py").read_text(encoding="utf-8")


def _seed_plugin_prompt_body():
    match = re.search(
        r"def _seed_plugin_prompt\(self, node, seed_prompt\):\n(.*?)(?:\n    def |\Z)",
        _WINDOW_ACTIONS_SOURCE,
        re.DOTALL,
    )
    assert match, "_seed_plugin_prompt method not found in graphite_window_actions.py"
    return match.group(1)


def test_registry_has_thirteen_entries():
    assert len(PLUGIN_REGISTRY) == 13


def test_registry_names_match_the_live_portal_registration():
    # PluginPortal._discover_plugins() doesn't touch main_window during __init__, so
    # this is safe to instantiate without a real window for a metadata-only check.
    portal = PluginPortal(main_window=None)
    portal_names = sorted(p["name"] for p in portal.get_plugins())
    registry_names = sorted(spec.display_name for spec in PLUGIN_REGISTRY.values())
    assert portal_names == registry_names


def test_get_plugin_spec_returns_the_registered_spec():
    spec = get_plugin_spec("gitlink")
    assert spec is not None
    assert spec.display_name == "Gitlink"


def test_get_plugin_spec_returns_none_for_unknown_key():
    assert get_plugin_spec("does-not-exist") is None


def test_get_display_name_for_node_uses_registered_name():
    spec = get_plugin_spec("code_review")
    assert get_display_name_for_node(spec.node_cls) == "Code Review Agent"


def test_get_display_name_for_node_falls_back_to_class_name():
    class UnregisteredNode:
        pass

    assert get_display_name_for_node(UnregisteredNode) == "UnregisteredNode"


def test_seedable_specs_are_all_handled_by_seed_plugin_prompt():
    body = _seed_plugin_prompt_body()
    for spec in PLUGIN_REGISTRY.values():
        if not spec.seedable or spec.node_cls is None:
            continue
        pattern = rf"isinstance\(node,\s*{spec.node_cls.__name__}\)"
        assert re.search(pattern, body), (
            f"{spec.key!r} is marked seedable=True but _seed_plugin_prompt has no "
            f"isinstance(node, {spec.node_cls.__name__}) branch - seeding it would "
            f"silently do nothing."
        )


def test_non_seedable_node_specs_are_not_handled_by_seed_plugin_prompt():
    body = _seed_plugin_prompt_body()
    for spec in PLUGIN_REGISTRY.values():
        if spec.seedable or spec.node_cls is None:
            continue
        pattern = rf"isinstance\(node,\s*{spec.node_cls.__name__}\)"
        assert not re.search(pattern, body), (
            f"{spec.key!r} is marked seedable=False but _seed_plugin_prompt already "
            f"handles {spec.node_cls.__name__} - the registry's seedable flag is stale "
            f"and should be flipped to True."
        )
