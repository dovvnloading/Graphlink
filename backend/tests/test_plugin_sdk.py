"""ADR-014 stage 14.1: Plugin SDK tests - manifest format, discovery, and
the host API v1 (backend/plugin_sdk.py), plus the generic executePlugin
fallback wired into backend/plugins.py.

Every discovery test below uses a tmp_path-derived plugins_root, a FRESH
key in discover_plugins' own module-level memoization cache
(_REGISTRY_CACHE, keyed by resolved path) - this never touches the shared
cache entry for the real repo plugins/ directory, and needs no
conftest.py-style real-data-dir guard (discovery never reads/writes
~/.graphlink)."""

import asyncio
import textwrap

import pytest

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import (
    HostContext,
    PluginLoadError,
    PluginRegistrationError,
    discover_plugins,
)
from backend.plugins import _PLUGINS, register_plugins


def _write_plugin(
    plugins_root,
    plugin_id,
    *,
    id_field=None,
    sdk_api_version=1,
    entry_point="plugin:register",
    kind="greet",
    picker_name="Test Greet",
    category="More Plugins",
    py_body=None,
    toml_body=None,
    dir_name=None,
):
    """Writes one real plugin.toml + plugin.py under plugins_root/dir_name
    (defaulting dir_name to plugin_id). id_field lets a test deliberately
    write an [plugin].id that diverges from the directory name."""
    plugin_dir = plugins_root / (dir_name or plugin_id)
    plugin_dir.mkdir(parents=True, exist_ok=True)

    if toml_body is None:
        toml_body = textwrap.dedent(f"""\
            [plugin]
            id = "{id_field if id_field is not None else plugin_id}"
            name = "Test Plugin {plugin_id}"
            version = "0.1.0"
            sdk_api_version = {sdk_api_version}
            entry_point = "{entry_point}"
        """)
    (plugin_dir / "plugin.toml").write_text(toml_body, encoding="utf-8")

    if py_body is None:
        py_body = textwrap.dedent(f"""\
            from backend.canvas import SceneDocument
            from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginRunContext


            def _make(document, run_ctx, parent_id):
                return PluginNodeSeed(
                    title="Greeting",
                    content="hello from " + run_ctx.plugin_id,
                )


            def register(host: HostContext) -> None:
                host.register_node_kind("{kind}", _make, requires_parent=True)
                host.register_picker_entry(
                    node_kind="{kind}",
                    name="{picker_name}",
                    description="a test plugin",
                    category="{category}",
                )
        """)
    (plugin_dir / "plugin.py").write_text(py_body, encoding="utf-8")
    return plugin_dir


# -- discovery: the happy path -----------------------------------------------


def test_discover_plugins_finds_imports_and_populates_the_registry(tmp_path):
    _write_plugin(tmp_path, "acme_greeter", kind="greet", picker_name="Acme Greeter")

    registry = discover_plugins(tmp_path)

    assert registry.load_errors == []
    assert "acme_greeter.greet" in registry.node_kinds
    assert registry.node_kinds["acme_greeter.greet"].plugin_id == "acme_greeter"
    assert registry.node_kinds["acme_greeter.greet"].requires_parent is True
    assert "Acme Greeter" in registry.picker_entries
    entry = registry.picker_entries["Acme Greeter"]
    assert entry.plugin_id == "acme_greeter"
    assert entry.node_kind == "acme_greeter.greet"


# -- malformed manifests: skipped, per-plugin, others unaffected ------------


def test_malformed_manifest_missing_plugin_table_is_skipped_good_sibling_still_loads(tmp_path):
    _write_plugin(
        tmp_path, "broken_one", dir_name="broken_one",
        toml_body="[frontend]\nview = \"generic\"\n",
    )
    _write_plugin(tmp_path, "good_one", kind="greet", picker_name="Good One Entry")

    registry = discover_plugins(tmp_path)

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "broken_one"
    assert isinstance(registry.load_errors[0], PluginLoadError)
    assert "good_one.greet" in registry.node_kinds
    assert "Good One Entry" in registry.picker_entries
    assert not any(kind.startswith("broken_one.") for kind in registry.node_kinds)


def test_malformed_manifest_id_mismatch_with_directory_name_is_skipped(tmp_path):
    _write_plugin(tmp_path, "dir_name_one", id_field="a_totally_different_id")
    _write_plugin(tmp_path, "good_two", kind="greet", picker_name="Good Two Entry")

    registry = discover_plugins(tmp_path)

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "dir_name_one"
    assert "good_two.greet" in registry.node_kinds
    assert "Good Two Entry" in registry.picker_entries


def test_malformed_manifest_sdk_api_version_out_of_range_is_skipped(tmp_path):
    _write_plugin(tmp_path, "future_plugin", sdk_api_version=999)
    _write_plugin(tmp_path, "good_three", kind="greet", picker_name="Good Three Entry")

    registry = discover_plugins(tmp_path)

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "future_plugin"
    assert "good_three.greet" in registry.node_kinds
    assert "Good Three Entry" in registry.picker_entries


# -- picker-name collisions: raised/recorded, never silently overwritten ---


def test_picker_name_collision_with_a_builtin_name_is_recorded_not_silently_merged(tmp_path):
    builtin_name = _PLUGINS[0][0]  # "System Prompt" today - any real built-in name works
    _write_plugin(tmp_path, "colliding_plugin", kind="greet", picker_name=builtin_name)

    registry = discover_plugins(tmp_path, builtin_names=frozenset(p[0] for p in _PLUGINS))

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "colliding_plugin"
    assert builtin_name not in registry.picker_entries
    assert "colliding_plugin.greet" not in registry.node_kinds


def test_picker_name_collision_between_two_plugins_is_recorded_first_wins_not_overwritten(tmp_path):
    # Directory names are chosen so sorted glob() ordering is deterministic:
    # "plugin_a" is imported before "plugin_b".
    _write_plugin(tmp_path, "plugin_a", dir_name="plugin_a", kind="greet", picker_name="Shared Name")
    _write_plugin(tmp_path, "plugin_b", dir_name="plugin_b", kind="greet", picker_name="Shared Name")

    registry = discover_plugins(tmp_path)

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "plugin_b"
    assert registry.picker_entries["Shared Name"].plugin_id == "plugin_a"
    assert "plugin_a.greet" in registry.node_kinds
    assert "plugin_b.greet" not in registry.node_kinds


# -- requires_parent=False: rejected at REGISTRATION time -------------------


def test_requires_parent_false_raises_plugin_registration_error_at_registration_time():
    host = HostContext("some_plugin")

    def _factory(document, run_ctx, parent_id):
        raise AssertionError("factory must never be called - registration itself must fail")

    with pytest.raises(PluginRegistrationError):
        host.register_node_kind("floating", _factory, requires_parent=False)
    assert "some_plugin.floating" not in host._node_kinds


# -- HostContext.register_intent(): declaration-time behavior only ----------
#
# ADR-014 stage 14.1 deviation (see backend/plugin_sdk.py's own module and
# HostContext.register_intent docstrings): live SessionBus activation of a
# plugin-declared intent is deferred - it collides with tests/
# test_undo_classification_gate.py's hard-locked requirement that every
# backend/ register_intent() call use a source-literal (topic, intent) pair,
# which a dynamically plugin-declared name can never satisfy by construction.
# The declaration API itself is still real and tested here.


def test_host_context_register_intent_stores_a_real_spec():
    host = HostContext("intent_plugin")
    handler = lambda document, run_ctx, *args: None  # noqa: E731

    host.register_intent("do_thing", handler, args_schema=None)

    assert len(host._intents) == 1
    spec = host._intents[0]
    assert spec.plugin_id == "intent_plugin"
    assert spec.name == "do_thing"
    assert spec.handler is handler


# -- discovery memoization ----------------------------------------------------


def test_discover_plugins_is_memoized_by_resolved_path(tmp_path):
    _write_plugin(tmp_path, "memo_plugin", kind="greet", picker_name="Memo Entry")

    first = discover_plugins(tmp_path)
    # Mutate the manifest on disk AFTER the first call - a second call that
    # actually rescanned would either pick up the new picker name or blow up
    # on the now-different sdk_api_version. A truly memoized call returns
    # the exact same object, untouched by this mutation.
    _write_plugin(tmp_path, "memo_plugin", kind="greet", picker_name="Mutated Entry Should Not Appear")

    second = discover_plugins(tmp_path)

    assert second is first
    assert "Memo Entry" in second.picker_entries
    assert "Mutated Entry Should Not Appear" not in second.picker_entries


# -- end-to-end through register_plugins + execute_plugin -------------------


def _make_wired_bus(plugin_registry):
    bus = SessionBus("plugin-sdk-e2e-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    canvas_document = SceneDocument()
    bus.register_topic("scene", canvas_document.scene_payload)
    register_plugins(bus, notifications, canvas_document, plugin_registry=plugin_registry)
    return bus, notifications, canvas_document


def test_execute_plugin_end_to_end_creates_a_real_node_with_content_and_parent_edge(tmp_path):
    _write_plugin(tmp_path, "e2e_plugin", kind="thing", picker_name="E2E Thing")
    registry = discover_plugins(tmp_path)

    bus, notifications, canvas_document = _make_wired_bus(registry)
    parent = canvas_document.add_node(10, 20, "parent")

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["E2E Thing", parent.id])
    )

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == "e2e_plugin.thing"
    assert node.content == "hello from e2e_plugin"
    assert any(
        e.source == parent.id and e.target == node.id for e in canvas_document.edges.values()
    )
    assert notifications.visible is False, "success is not a deferral - no notification fires"


def test_execute_plugin_requires_a_valid_parent_for_a_discovered_plugin(tmp_path):
    _write_plugin(tmp_path, "needs_parent_plugin", kind="thing", picker_name="Needs Parent Thing")
    registry = discover_plugins(tmp_path)
    bus, notifications, canvas_document = _make_wired_bus(registry)

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Needs Parent Thing"]))

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert not any(n.kind == "needs_parent_plugin.thing" for n in canvas_document.nodes.values())


# -- undo: a discovered plugin's node creation goes through the SAME -------
# -- record_command/undo stack every built-in plugin already uses ----------


def test_undo_reverts_a_plugin_created_node_and_its_parent_edge(tmp_path):
    _write_plugin(tmp_path, "undo_plugin", kind="thing", picker_name="Undo Thing")
    registry = discover_plugins(tmp_path)

    bus, notifications, canvas_document = _make_wired_bus(registry)
    parent = canvas_document.add_node(10, 20, "parent")

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Undo Thing", parent.id])
    )
    assert result in canvas_document.nodes
    assert canvas_document.can_undo()

    canvas_document.undo()

    assert result not in canvas_document.nodes
    assert not any(
        e.source == parent.id and e.target == result for e in canvas_document.edges.values()
    )
    # The parent itself must survive the undo - only the plugin's own
    # command_type ("plugin:undo_plugin.thing") should have been reversed.
    assert parent.id in canvas_document.nodes
