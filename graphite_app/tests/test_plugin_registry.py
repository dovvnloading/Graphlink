"""Tests for the Phase 1 plugin registry (graphite_plugin_portal.PLUGIN_REGISTRY).

doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md documents that plugin metadata is hand-copied in
several independent places across the app (PluginPortal's _register_plugin calls,
graphite_plugin_quality_gate.py's _node_label, graphite_plugin_workflow.py's
WORKFLOW_PLUGIN_ICONS) and that this drift already caused a live bug (Workflow's
allowlist silently omitted Code Review Agent). PLUGIN_REGISTRY is meant to become the
single source of truth those should eventually read from - these tests cross-check it
against the existing hand-maintained lists so registry/reality drift is caught
immediately instead of being discovered by a future audit.

Note: graphite_window_actions.py's former isinstance-chain dispatcher was replaced in
Phase 2 by each node class implementing seed_prompt(text) itself (see
tests/test_seed_prompt_protocol.py for the behavioral coverage of that). The
seedable-flag-vs-reality cross-check below now asserts against that protocol directly
instead of scraping the old isinstance chain out of the dispatcher's source.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphite_plugins.graphite_plugin_portal import (
    PLUGIN_REGISTRY,
    PluginPortal,
    get_display_name_for_node,
    get_plugin_spec,
)


def test_registry_has_eight_entries():
    assert len(PLUGIN_REGISTRY) == 8


def test_registry_names_match_the_live_portal_registration():
    # PluginPortal._discover_plugins() doesn't touch main_window during __init__, so
    # this is safe to instantiate without a real window for a metadata-only check.
    portal = PluginPortal(main_window=None)
    portal_names = sorted(p["name"] for p in portal.get_plugins())
    registry_names = sorted(spec.display_name for spec in PLUGIN_REGISTRY.values())
    assert portal_names == registry_names


def test_registry_descriptions_match_the_live_portal_registration():
    # Description text is hand-duplicated between PLUGIN_REGISTRY and
    # PluginPortal._discover_plugins() (see doc/ARCHITECTURE_REVIEW_FINDINGS.md #61) -
    # this doesn't fix that duplication, but it does catch the two copies drifting out
    # of sync, which is exactly what happened to Execution Sandbox's description before
    # #16 was fixed (both copies had to be updated by hand to stay honest about the
    # sandbox not isolating from the OS).
    portal = PluginPortal(main_window=None)
    portal_descriptions_by_name = {p["name"]: p["description"] for p in portal.get_plugins()}
    for spec in PLUGIN_REGISTRY.values():
        assert portal_descriptions_by_name[spec.display_name] == spec.description


def test_execution_sandbox_description_does_not_oversell_containment():
    # See doc/ARCHITECTURE_REVIEW_FINDINGS.md #16: the description used to just say
    # "isolated virtualenv" with no caveat, which a user could easily read as stronger
    # containment than a venv actually provides. The approval dialog shown before every
    # run has always been honest about this (_handle_code_sandbox_approval_request);
    # the picker-level description should say the same thing, not oversell it.
    spec = get_plugin_spec("code_sandbox")
    assert "not the operating system" in spec.description


def test_get_plugin_spec_returns_the_registered_spec():
    spec = get_plugin_spec("gitlink")
    assert spec is not None
    assert spec.display_name == "Gitlink"


def test_get_plugin_spec_returns_none_for_unknown_key():
    assert get_plugin_spec("does-not-exist") is None


def test_get_display_name_for_node_uses_registered_name():
    spec = get_plugin_spec("conversation")
    assert get_display_name_for_node(spec.node_cls) == "Conversation Node"


def test_get_display_name_for_node_falls_back_to_class_name():
    class UnregisteredNode:
        pass

    assert get_display_name_for_node(UnregisteredNode) == "UnregisteredNode"


def test_seedable_specs_implement_seed_prompt():
    for spec in PLUGIN_REGISTRY.values():
        if not spec.seedable or spec.node_cls is None:
            continue
        assert callable(getattr(spec.node_cls, "seed_prompt", None)), (
            f"{spec.key!r} is marked seedable=True but {spec.node_cls.__name__} has no "
            f"seed_prompt() method - instantiate_seeded_plugin would silently do nothing."
        )


def test_non_seedable_node_specs_do_not_implement_seed_prompt():
    for spec in PLUGIN_REGISTRY.values():
        if spec.seedable or spec.node_cls is None:
            continue
        assert getattr(spec.node_cls, "seed_prompt", None) is None, (
            f"{spec.key!r} is marked seedable=False but {spec.node_cls.__name__} already "
            f"implements seed_prompt() - the registry's seedable flag is stale and should "
            f"be flipped to True."
        )
