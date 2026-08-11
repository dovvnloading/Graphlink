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
from backend.plugins import register_plugins
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from graphlink_settings_store import SettingsManager


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


def test_picker_name_collision_with_a_reserved_name_is_recorded_not_silently_merged(tmp_path):
    # ADR-014 stage 14.3: the 8 pre-SDK built-ins are now real discovered
    # plugins themselves (plugins/web_research/, plugins/gitlink/, ...), so
    # backend/plugins.py's own register_plugins() no longer calls
    # discover_plugins(builtin_names=...) with a real argument - collisions
    # between a migrated built-in and a third-party plugin are now caught
    # by the SAME picker_entries/builtin_actions collision check any two
    # plugins are checked with (see the two tests below this one). The
    # `builtin_names` mechanism itself stays real, generic SDK surface -
    # exercised here directly with a synthetic reserved-name set, the way
    # any host embedding this SDK could still reserve a name that has no
    # registry entry of its own yet.
    _write_plugin(tmp_path, "colliding_plugin", kind="greet", picker_name="Reserved Name")

    registry = discover_plugins(tmp_path, builtin_names=frozenset({"Reserved Name"}))

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "colliding_plugin"
    assert "Reserved Name" not in registry.picker_entries
    assert "colliding_plugin.greet" not in registry.node_kinds


def test_picker_name_collision_with_a_real_builtin_action_name_is_recorded_not_silently_merged(tmp_path):
    # ADR-014 stage 14.3: a third-party plugin naming itself "Web Research"
    # (a real migrated built-in's name, registered via
    # register_builtin_plugin rather than register_picker_entry) must still
    # be rejected - the two registration mechanisms share one flat name
    # space. Directory name "aaa_colliding" sorts before "web_research" in
    # glob() order, so this plugin is merged FIRST and the real
    # plugins/web_research/ package collides against IT, proving the check
    # is symmetric (either registration order loses to the other).
    _write_plugin(tmp_path, "aaa_colliding", kind="greet", picker_name="Web Research")
    # Copy the real web_research plugin's manifest/module into this same
    # tmp_path root so discover_plugins() sees both in one scan.
    import shutil

    from backend.plugin_sdk import DEFAULT_PLUGINS_ROOT

    shutil.copytree(DEFAULT_PLUGINS_ROOT / "web_research", tmp_path / "web_research")

    registry = discover_plugins(tmp_path)

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "web_research"
    assert registry.picker_entries["Web Research"].plugin_id == "aaa_colliding"
    assert "aaa_colliding.greet" in registry.node_kinds
    assert "Web Research" not in registry.builtin_actions


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


# -- ADR-014 stage 14.3: HostContext.register_builtin_plugin() --------------
#
# The first-party migration escape hatch - see BuiltinActionSpec/
# HostContext.register_builtin_plugin's own docstrings (backend/
# plugin_sdk.py) for the full contract. Declaration-time behavior only
# here; end-to-end dispatch through executePlugin is covered by the
# per-built-in tests in backend/tests/test_plugins.py.


def test_host_context_register_builtin_plugin_stores_a_real_spec():
    host = HostContext("some_plugin")

    def _handler(document, run_ctx, parent_node_id):
        return "unused-in-this-test"

    host.register_builtin_plugin(
        name="Some Action", description="does a thing", category="More Plugins",
        handler=_handler,
    )

    assert len(host._builtin_actions) == 1
    spec = host._builtin_actions["Some Action"]
    assert spec.plugin_id == "some_plugin"
    assert spec.name == "Some Action"
    assert spec.description == "does a thing"
    assert spec.category == "More Plugins"
    assert spec.handler is _handler


def test_host_context_register_builtin_plugin_same_plugin_name_reuse_raises():
    host = HostContext("some_plugin")
    host.register_builtin_plugin(
        name="Dup", description="d", category="More Plugins", handler=lambda d, r, p: None,
    )

    with pytest.raises(PluginRegistrationError):
        host.register_builtin_plugin(
            name="Dup", description="d2", category="More Plugins", handler=lambda d, r, p: None,
        )


def test_builtin_action_name_collision_between_two_plugins_is_recorded_first_wins(tmp_path):
    # Two synthetic plugins each calling register_builtin_plugin (NOT
    # register_picker_entry) with the same name - directory names chosen so
    # sorted glob() ordering is deterministic.
    py_body_a = "from backend.plugin_sdk import HostContext\n\n\ndef register(host: HostContext) -> None:\n    host.register_builtin_plugin(name='Shared Builtin', description='a', category='More Plugins', handler=lambda d, r, p: None)\n"
    py_body_b = "from backend.plugin_sdk import HostContext\n\n\ndef register(host: HostContext) -> None:\n    host.register_builtin_plugin(name='Shared Builtin', description='b', category='More Plugins', handler=lambda d, r, p: None)\n"
    _write_plugin(tmp_path, "plugin_a", dir_name="plugin_a", py_body=py_body_a, toml_body=None)
    _write_plugin(tmp_path, "plugin_b", dir_name="plugin_b", py_body=py_body_b, toml_body=None)

    registry = discover_plugins(tmp_path)

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "plugin_b"
    assert registry.builtin_actions["Shared Builtin"].plugin_id == "plugin_a"


def test_builtin_action_name_collision_with_a_picker_entry_name_is_recorded(tmp_path):
    # A register_builtin_plugin name colliding against an ALREADY-merged
    # register_picker_entry name (from a different plugin) must be
    # rejected too - one flat name space across both mechanisms.
    py_body_builtin = "from backend.plugin_sdk import HostContext\n\n\ndef register(host: HostContext) -> None:\n    host.register_builtin_plugin(name='Shared Name', description='b', category='More Plugins', handler=lambda d, r, p: None)\n"
    _write_plugin(tmp_path, "plugin_a", dir_name="plugin_a", kind="greet", picker_name="Shared Name")
    _write_plugin(tmp_path, "plugin_b", dir_name="plugin_b", py_body=py_body_builtin, toml_body=None)

    registry = discover_plugins(tmp_path)

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "plugin_b"
    assert registry.picker_entries["Shared Name"].plugin_id == "plugin_a"
    assert "Shared Name" not in registry.builtin_actions


def test_resolve_builtin_action_returns_none_for_unknown_name(tmp_path):
    py_body = (
        "from backend.plugin_sdk import HostContext\n\n\n"
        "def register(host: HostContext) -> None:\n"
        "    host.register_builtin_plugin(\n"
        "        name='Real', description='d', category='More Plugins',\n"
        "        handler=lambda d, r, p: None,\n"
        "    )\n"
    )
    _write_plugin(tmp_path, "some_plugin", py_body=py_body, toml_body=None)

    registry = discover_plugins(tmp_path)

    resolved = registry.resolve_builtin_action("Real")
    assert resolved is not None
    assert resolved.plugin_id == "some_plugin"
    assert registry.resolve_builtin_action("Nope") is None


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


def _make_wired_bus(plugin_registry, tmp_path):
    # ADR-014 stage 14.4: register_plugins now requires a real
    # SettingsManager (the deny-by-default plugin-grant store) and every
    # non-built-in plugin dispatch is now grant-gated - every plugin_id this
    # registry knows about (both node_kinds' and picker_entries' plugin_id,
    # covering a kind registered but not yet exposed via a picker entry too)
    # is pre-granted here so the pre-existing tests in this file keep
    # proving the mechanism they were written for (discovery, dispatch,
    # persistence, undo, live-wire) rather than incidentally re-proving the
    # grant gate on every single call site. The grant gate itself gets its
    # own dedicated tests further down this file.
    settings_manager = SettingsManager(tmp_path / "session.dat")
    plugin_ids = {spec.plugin_id for spec in plugin_registry.node_kinds.values()} | {
        entry.plugin_id for entry in plugin_registry.picker_entries.values()
    }
    for plugin_id in plugin_ids:
        settings_manager.set_plugin_grant(plugin_id, True)

    bus = SessionBus("plugin-sdk-e2e-test")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    canvas_document = SceneDocument()
    bus.register_topic("scene", canvas_document.scene_payload)
    register_plugins(
        bus, notifications, canvas_document, settings_manager, plugin_registry=plugin_registry,
    )
    return bus, notifications, canvas_document


def test_execute_plugin_end_to_end_creates_a_real_node_with_content_and_parent_edge(tmp_path):
    _write_plugin(tmp_path, "e2e_plugin", kind="thing", picker_name="E2E Thing")
    registry = discover_plugins(tmp_path)

    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
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
    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)

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

    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
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


# -- ADR-014 stage 14.2: generic node persistence/serialization -------------
#
# Every test below deliberately uses a FRESH tmp_path plugin, never known to
# session_save.py/session_load.py's own source code, to prove the mechanism
# is genuinely generic - not a special case for plugins/hello_node/ or
# plugins/counter_node/ (which get their own, separate, real-plugin-
# integration coverage further down). No test in this section adds a single
# line to session_save.py/session_load.py to pass - that IS the proof that
# a future plugin needs zero edits there.

# A real NodeState subclass + real serialize/deserialize hooks, written out
# as a plugin.py body string so _write_plugin can place it under a tmp_path
# plugin directory exactly like a real third-party plugin would ship it.
_STATEFUL_PY_BODY = textwrap.dedent("""\
    from dataclasses import dataclass

    from backend.domain.node_states import NodeState
    from backend.plugin_sdk import HostContext, PluginNodeSeed, PluginRunContext


    @dataclass
    class WidgetState(NodeState):
        clicks: int = 0
        label: str = ""


    def _make(document, run_ctx, parent_id):
        return PluginNodeSeed(
            title="Widget", content="", state=WidgetState(clicks=3, label="initial"),
        )


    def _serialize(node):
        return {"clicks": node.state.clicks, "label": node.state.label}


    def _deserialize(data):
        return WidgetState(clicks=int(data.get("clicks", 0)), label=str(data.get("label", "")))


    def register(host: HostContext) -> None:
        host.register_node_kind(
            "widget", _make, requires_parent=True,
            serialize=_serialize, deserialize=_deserialize,
        )
        host.register_picker_entry(
            node_kind="widget", name="Stateful Widget",
            description="a test plugin with real NodeState", category="More Plugins",
        )
""")


def _round_trip_via_files(document, plugin_registry):
    """Mirrors test_session_save.py's own _round_trip helper exactly, plus
    the plugin_registry threading this stage adds - build_chat_data's
    output, popped/re-nested exactly the way backend/chat_library.py's own
    saveChat/loadChat intents do it, fed straight back through
    restore_chat_into_document."""
    chat_data = build_chat_data(document, plugin_registry=plugin_registry)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    doc2 = SceneDocument()
    restore_chat_into_document(doc2, {"data": chat_data}, notes_data, pins_data, plugin_registry=plugin_registry)
    return chat_data, doc2


def test_plugin_node_with_no_serializer_round_trips_title_and_content_only(tmp_path):
    # _write_plugin's default py_body registers a factory with NO `state=`
    # at all - the "no opt-in" baseline case. The parent is a real "chat"
    # node (add_chat_node), not add_node's bare "placeholder" kind - a
    # placeholder was never a persisted kind even before ADR-014 (it isn't
    # in session_save.py's own _REGULAR_KINDS), so it would vanish on
    # reload regardless of anything this stage changes.
    _write_plugin(tmp_path, "no_state_plugin", kind="thing", picker_name="No State Thing")
    registry = discover_plugins(tmp_path)
    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
    parent = canvas_document.add_chat_node(10, 20, "parent", is_user=False)

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["No State Thing", parent.id])
    )
    assert node_id is not None

    chat_data, reloaded = _round_trip_via_files(canvas_document, registry)

    saved_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "no_state_plugin.thing")
    assert saved_payload["title"] == "Greeting"
    assert saved_payload["content"] == "hello from no_state_plugin"
    assert "plugin_state" not in saved_payload

    assert len(reloaded.nodes) == 2
    plugin_node = next(n for n in reloaded.nodes.values() if n.kind == "no_state_plugin.thing")
    assert plugin_node.title == "Greeting"
    assert plugin_node.content == "hello from no_state_plugin"
    assert plugin_node.state is None
    reloaded_parent = next(n for n in reloaded.nodes.values() if n.id != plugin_node.id)
    assert reloaded_parent.kind == "chat"
    assert any(
        e.source == reloaded_parent.id and e.target == plugin_node.id
        for e in reloaded.edges.values()
    )


def test_plugin_node_with_real_state_round_trips_custom_fields_through_save_and_reload(tmp_path):
    _write_plugin(
        tmp_path, "stateful_plugin", py_body=_STATEFUL_PY_BODY, kind="widget",
        picker_name="Stateful Widget",
    )
    registry = discover_plugins(tmp_path)
    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
    parent = canvas_document.add_chat_node(10, 20, "parent", is_user=False)

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Stateful Widget", parent.id])
    )
    assert node_id is not None
    # A real LIVE edit after creation, not just the factory's initial
    # value - proves the round-trip reflects the node's actual current
    # state, not merely what _make_tally-equivalent seeded it with.
    canvas_document.nodes[node_id].state.clicks = 7
    canvas_document.nodes[node_id].state.label = "edited"

    chat_data, reloaded = _round_trip_via_files(canvas_document, registry)

    saved_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "stateful_plugin.widget")
    assert saved_payload["plugin_state"] == {"clicks": 7, "label": "edited"}

    assert len(reloaded.nodes) == 2
    plugin_node = next(n for n in reloaded.nodes.values() if n.kind == "stateful_plugin.widget")
    assert plugin_node.title == "Widget"
    assert plugin_node.state.clicks == 7
    assert plugin_node.state.label == "edited"
    reloaded_parent = next(n for n in reloaded.nodes.values() if n.id != plugin_node.id)
    assert reloaded_parent.kind == "chat"
    assert any(
        e.source == reloaded_parent.id and e.target == plugin_node.id
        for e in reloaded.edges.values()
    )


def test_plugin_node_whose_kind_is_no_longer_registered_is_dropped_not_crashed(tmp_path):
    # A plugin removed/renamed between save and load - the same "unrecognized
    # node_type -> skip, never crash" posture session_load.py already applies
    # to every other kind (this module's own docstring documents 5 kinds
    # R5-closeout deleted with the exact same tolerant behavior).
    _write_plugin(tmp_path, "temporary_plugin", kind="thing", picker_name="Temporary Thing")
    registry = discover_plugins(tmp_path)
    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
    parent = canvas_document.add_chat_node(10, 20, "parent", is_user=False)
    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Temporary Thing", parent.id])
    )
    assert node_id is not None

    chat_data = build_chat_data(canvas_document, plugin_registry=registry)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    empty_registry = discover_plugins(tmp_path / "does_not_exist")

    doc2 = SceneDocument()
    restore_chat_into_document(
        doc2, {"data": chat_data}, notes_data, pins_data, plugin_registry=empty_registry,
    )

    assert not any(n.kind == "temporary_plugin.thing" for n in doc2.nodes.values())
    # The parent node itself (a plain built-in "chat" node, unaffected by
    # the missing plugin) must still be there - one dropped node is not a
    # failed load.
    assert any(n.kind == "chat" for n in doc2.nodes.values())


def test_live_wire_scene_payload_includes_plugin_state_when_serializer_registered(tmp_path):
    # ADR-014 stage 14.2's OTHER half - the live WS wire, not save/reload.
    # register_plugins populates SceneDocument.plugin_node_serializers from
    # the SAME registry _round_trip_via_files' save path reads its
    # serialize hooks from - one function, two call sites.
    _write_plugin(
        tmp_path, "stateful_plugin", py_body=_STATEFUL_PY_BODY, kind="widget",
        picker_name="Stateful Widget",
    )
    registry = discover_plugins(tmp_path)
    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
    parent = canvas_document.add_node(10, 20, "parent")

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Stateful Widget", parent.id])
    )

    wire = canvas_document.scene_payload()
    node_wire = next(n for n in wire["nodes"] if n["id"] == node_id)
    # Coerced to str on the wire (SceneNodeRow.pluginState is dict[str, str]
    # - see contracts/graphlink_scene_payload.py's own comment) even though
    # `clicks` is a real int in memory and in the save file.
    assert node_wire["pluginState"] == {"clicks": "3", "label": "initial"}

    parent_wire = next(n for n in wire["nodes"] if n["id"] == parent.id)
    assert parent_wire["pluginState"] == {}


def test_live_wire_plugin_state_is_empty_for_a_plugin_kind_with_no_serializer(tmp_path):
    _write_plugin(tmp_path, "no_state_plugin", kind="thing", picker_name="No State Thing")
    registry = discover_plugins(tmp_path)
    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
    parent = canvas_document.add_node(10, 20, "parent")

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["No State Thing", parent.id])
    )

    wire = canvas_document.scene_payload()
    node_wire = next(n for n in wire["nodes"] if n["id"] == node_id)
    assert node_wire["pluginState"] == {}


def test_a_plugin_serializer_that_raises_degrades_to_empty_plugin_state_on_the_wire(tmp_path):
    raising_body = textwrap.dedent("""\
        from backend.plugin_sdk import HostContext, PluginNodeSeed


        def _make(document, run_ctx, parent_id):
            return PluginNodeSeed(title="Boom", content="")


        def _serialize(node):
            raise RuntimeError("this plugin's serializer is broken")


        def register(host: HostContext) -> None:
            host.register_node_kind("boom", _make, requires_parent=True, serialize=_serialize)
            host.register_picker_entry(
                node_kind="boom", name="Boom Thing", description="raises", category="More Plugins",
            )
    """)
    _write_plugin(tmp_path, "raising_plugin", py_body=raising_body, kind="boom", picker_name="Boom Thing")
    registry = discover_plugins(tmp_path)
    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
    parent = canvas_document.add_node(10, 20, "parent")

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Boom Thing", parent.id])
    )
    assert node_id is not None

    # Live wire: a raising serializer degrades to {}, never crashes the
    # whole scene publish.
    wire = canvas_document.scene_payload()
    node_wire = next(n for n in wire["nodes"] if n["id"] == node_id)
    assert node_wire["pluginState"] == {}

    # Save file: same degrade-not-crash posture - the node's universal
    # title/content still saves, just without plugin_state.
    chat_data = build_chat_data(canvas_document, plugin_registry=registry)
    saved_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "raising_plugin.boom")
    assert saved_payload["title"] == "Boom"
    assert "plugin_state" not in saved_payload


# -- Integration coverage using the REAL shipped demo plugins ---------------
#
# The tests above prove the MECHANISM is generic against an arbitrary,
# never-before-seen plugin. These two prove the actual shipped
# plugins/hello_node/ and plugins/counter_node/ (real discover_plugins(),
# no tmp_path override) round-trip correctly through the real default
# discovery root - the production configuration, not just the test harness.


def test_real_hello_node_plugin_round_trips_through_save_and_reload(tmp_path):
    registry = discover_plugins()
    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
    parent = canvas_document.add_chat_node(10, 20, "parent", is_user=False)

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Hello Node", parent.id])
    )
    assert node_id is not None

    _chat_data, reloaded = _round_trip_via_files(canvas_document, registry)

    plugin_node = next(n for n in reloaded.nodes.values() if n.kind == "hello_node.hello_note")
    assert plugin_node.title == "Hello Node"
    assert "hello_node" in plugin_node.content
    assert plugin_node.state is None


def test_real_counter_node_plugin_round_trips_its_custom_state_through_save_and_reload(tmp_path):
    registry = discover_plugins()
    bus, notifications, canvas_document = _make_wired_bus(registry, tmp_path)
    parent = canvas_document.add_chat_node(10, 20, "parent", is_user=False)

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Counter Node", parent.id])
    )
    assert node_id is not None
    canvas_document.nodes[node_id].state.count = 42

    _chat_data, reloaded = _round_trip_via_files(canvas_document, registry)

    plugin_node = next(n for n in reloaded.nodes.values() if n.kind == "counter_node.tally")
    assert plugin_node.title == "Counter"
    assert plugin_node.state.count == 42
    assert plugin_node.state.label == "from counter_node"
