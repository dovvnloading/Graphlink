"""ADR-014 stage 14.4: scope model + install-time consent + Settings grants.

Proves the stage's own exit criterion literally, not just in prose: "a
plugin acting outside its grant is denied." Four load-bearing claims,
each with its own dedicated test group below:

1. A non-granted third-party (generic-path) plugin's node-creation attempt
   via executePlugin is DENIED - a clear notification fires, no node is
   created, and nothing lands on the undo stack (because nothing happened).
2. Granting that SAME plugin (via SettingsManager.set_plugin_grant,
   simulating the Settings UI's own write path) then lets the identical
   dispatch succeed.
3. A built-in (register_builtin_plugin escape hatch, e.g. "Web Research")
   is completely unaffected by grant state in either direction - it has no
   manifest [scopes] table at all and never consults the grant store.
4. An unknown scope string in a manifest's [scopes].grants is rejected AT
   DISCOVERY with a PluginLoadError, fail-soft (this one plugin is skipped,
   every sibling still loads) - the same posture every other malformed-
   manifest case already gets (backend/tests/test_plugin_sdk.py).
5. The new "invokePluginIntent" dispatch denies an ungranted plugin's
   custom intent the same way, and allows it once granted.

Also covers the surrounding machinery this enforcement depends on:
SettingsManager.get_plugin_grants()/set_plugin_grant() persistence,
PluginManifest.scopes_grants parsing, the "app-plugins" topic's new
"grants" array shape (built-ins absent, one row per distinct third-party
plugin_id), and the new setPluginGrant intent's persist-then-republish
behavior."""

from __future__ import annotations

import asyncio
import textwrap

import pytest

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import (
    PluginRegistrationError,
    discover_plugins,
)
from backend.plugins import plugins_payload, register_plugins
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from graphlink_settings_store import SettingsManager


def _write_grant_test_plugin(
    plugins_root, plugin_id, *, scopes_toml="", picker_name=None, register_body=None,
):
    """Writes one real plugin.toml + plugin.py under plugins_root/plugin_id -
    a minimal node-creation plugin (generic PluginNodeSeed path, same shape
    as plugins/hello_node/), optionally carrying a [scopes] table verbatim
    (or none at all, the "omitted -> frozenset()" case)."""
    plugin_dir = plugins_root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    picker_name = picker_name or f"{plugin_id} Node"
    (plugin_dir / "plugin.toml").write_text(
        textwrap.dedent(f"""\
            [plugin]
            id = "{plugin_id}"
            name = "{plugin_id} Plugin"
            version = "0.1.0"
            sdk_api_version = 1
            entry_point = "plugin:register"
            {scopes_toml}
        """),
        encoding="utf-8",
    )
    if register_body is None:
        register_body = textwrap.dedent(f"""\
            from backend.plugin_sdk import HostContext, PluginNodeSeed


            def _make(document, run_ctx, parent_id):
                return PluginNodeSeed(title="Grant Test Node", content="made it")


            def register(host: HostContext) -> None:
                host.register_node_kind("thing", _make, requires_parent=True)
                host.register_picker_entry(
                    node_kind="thing", name="{picker_name}", description="d",
                    category="More Plugins",
                )
        """)
    (plugin_dir / "plugin.py").write_text(register_body, encoding="utf-8")
    return plugin_dir


def _wired_bus(registry, tmp_path, *, dirname="grant-test-store"):
    settings_manager = SettingsManager(tmp_path / dirname / "session.dat")
    notifications = NotificationState()
    bus = SessionBus("plugin-grants-test")
    bus.register_topic("notification", notifications.payload)
    canvas_document = SceneDocument()
    bus.register_topic("scene", canvas_document.scene_payload)
    register_plugins(bus, notifications, canvas_document, settings_manager, plugin_registry=registry)
    return bus, notifications, canvas_document, settings_manager


# -- 1 & 2: deny-by-default, then allow after a real grant -------------------


def test_ungranted_generic_plugin_denies_node_creation_with_a_clear_notification(tmp_path):
    _write_grant_test_plugin(
        tmp_path / "plugins", "denyme", scopes_toml='[scopes]\ngrants = ["graph.mutate"]',
        picker_name="Deny Me Node",
    )
    registry = discover_plugins(tmp_path / "plugins")
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)
    parent = canvas_document.add_node(10, 20, "parent")

    # Never granted - settings_manager.get_plugin_grants() is empty.
    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Deny Me Node", parent.id])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert "Deny Me Node" in notifications.message
    assert "approval" in notifications.message.lower()
    # No node created...
    assert len(canvas_document.nodes) == 1  # only the parent
    # ...and nothing recorded - undo is unaffected because nothing happened.
    assert canvas_document.can_undo() is False


def test_granting_the_same_plugin_then_allows_the_identical_dispatch_to_succeed(tmp_path):
    _write_grant_test_plugin(
        tmp_path / "plugins", "allowme", scopes_toml='[scopes]\ngrants = ["graph.mutate"]',
        picker_name="Allow Me Node",
    )
    registry = discover_plugins(tmp_path / "plugins")
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)
    parent = canvas_document.add_node(10, 20, "parent")

    denied = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Allow Me Node", parent.id])
    )
    assert denied is None
    assert len(canvas_document.nodes) == 1

    # Simulates the Settings UI's own write path.
    settings_manager.set_plugin_grant("allowme", True)

    created = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Allow Me Node", parent.id])
    )

    assert created is not None
    node = canvas_document.nodes[created]
    assert node.kind == "allowme.thing"
    assert canvas_document.can_undo() is True
    # Revoking again must deny the NEXT attempt (not a one-time unlock).
    settings_manager.set_plugin_grant("allowme", False)
    revoked = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Allow Me Node", parent.id])
    )
    assert revoked is None
    assert len(canvas_document.nodes) == 2  # the one real creation above, no more


def test_ungranted_plugin_denies_even_with_no_parent_selected_grant_checked_first(tmp_path):
    # The grant gate runs BEFORE parent validation (cheapest static check
    # first, mirroring ToolRegistry.invoke()) - an ungranted plugin is
    # denied regardless of what parent (if any) was passed.
    _write_grant_test_plugin(
        tmp_path / "plugins", "denyme2", scopes_toml='[scopes]\ngrants = ["graph.mutate"]',
        picker_name="Deny Me Two",
    )
    registry = discover_plugins(tmp_path / "plugins")
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)

    result = asyncio.run(bus.dispatch_intent("app-plugins", "executePlugin", ["Deny Me Two"]))

    assert result is None
    assert notifications.visible is True
    assert "approval" in notifications.message.lower()


def test_a_plugin_with_no_scopes_table_still_requires_an_explicit_grant(tmp_path):
    # Omitting [scopes] entirely -> scopes_grants == frozenset(), which is
    # NOT the same as "trusted" - install-time consent is still required.
    _write_grant_test_plugin(tmp_path / "plugins", "noscopes", picker_name="No Scopes Node")
    registry = discover_plugins(tmp_path / "plugins")
    assert registry.manifests["noscopes"].scopes_grants == frozenset()

    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)
    parent = canvas_document.add_node(10, 20, "parent")

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["No Scopes Node", parent.id])
    )
    assert result is None
    assert len(canvas_document.nodes) == 1


# -- 3: built-ins are completely unaffected by grant state --------------------


def test_builtin_plugin_always_works_regardless_of_grant_state_and_has_no_manifest_scopes(tmp_path):
    registry = discover_plugins()  # the real, shipped plugins/ root
    bus, notifications, canvas_document, settings_manager = _wired_bus(
        registry, tmp_path, dirname="builtin-store",
    )
    parent = canvas_document.add_node(10, 20, "parent")

    # Nothing was ever granted for ANY plugin_id in this fresh store.
    assert settings_manager.get_plugin_grants() == {}

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Web Research", parent.id])
    )

    assert result is not None
    node = canvas_document.nodes[result]
    assert node.kind == "web_research"
    assert notifications.visible is False

    # Built-ins never appear in the grants payload at all (see _plugin_grants
    # _payload's own docstring) - "web_research" is a builtin_actions plugin
    # id, not a picker_entries one.
    payload = plugins_payload(registry, settings_manager)
    granted_plugin_ids = {row["pluginId"] for row in payload["grants"]}
    assert "web_research" not in granted_plugin_ids


# -- 3b: revoking a grant actually stops serialize/deserialize hooks --------
# -- from running (finding 2) ------------------------------------------------
#
# BEFORE this fix, backend/plugins.py's register_plugins wired a plugin's
# HostContext.register_node_kind(..., serialize=...) hook into
# SceneDocument.plugin_node_serializers UNCONDITIONALLY - unlike node
# creation/invokePluginIntent/register_plugin_tools' handler (all three
# already check settings_manager.get_plugin_grants() before running a
# plugin's own code), so revoking a plugin's grant in Settings did NOT stop
# its serialize/deserialize code from continuing to run on every live-wire
# scene publish, save, and load. These three tests prove each of the three
# now-gated call sites independently.


def _stateful_grant_py_body(picker_name: str) -> str:
    return textwrap.dedent(f"""\
        from dataclasses import dataclass

        from backend.domain.node_states import NodeState
        from backend.plugin_sdk import HostContext, PluginNodeSeed


        @dataclass
        class WidgetState(NodeState):
            clicks: int = 0


        def _make(document, run_ctx, parent_id):
            return PluginNodeSeed(title="Widget", content="", state=WidgetState(clicks=3))


        def _serialize(node):
            return {{"clicks": node.state.clicks}}


        def _deserialize(data):
            return WidgetState(clicks=int(data.get("clicks", 0)))


        def register(host: HostContext) -> None:
            host.register_node_kind(
                "widget", _make, requires_parent=True, serialize=_serialize, deserialize=_deserialize,
            )
            host.register_picker_entry(
                node_kind="widget", name="{picker_name}", description="d", category="More Plugins",
            )
    """)


def test_revoking_a_plugins_grant_stops_its_live_wire_serializer_from_running(tmp_path):
    _write_grant_test_plugin(
        tmp_path / "plugins", "statefulgrant1", register_body=_stateful_grant_py_body("Grant Widget 1"),
    )
    registry = discover_plugins(tmp_path / "plugins")
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)
    settings_manager.set_plugin_grant("statefulgrant1", True)
    parent = canvas_document.add_node(10, 20, "parent")

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Grant Widget 1", parent.id])
    )
    assert node_id is not None

    # Granted: the live wire carries the plugin's own custom state.
    wire = canvas_document.scene_payload()
    node_wire = next(n for n in wire["nodes"] if n["id"] == node_id)
    assert node_wire["pluginState"] == {"clicks": "3"}

    # Revoke - the SAME live node, no re-registration, no session restart.
    settings_manager.set_plugin_grant("statefulgrant1", False)

    wire2 = canvas_document.scene_payload()
    node_wire2 = next(n for n in wire2["nodes"] if n["id"] == node_id)
    # Degrades to {} (the same "no plugin state to show" shape a plugin
    # with no serializer at all produces) - proving the wrapper re-checks
    # the CURRENT grant on every publish, not just at registration time.
    assert node_wire2["pluginState"] == {}


def test_ungranted_plugins_serializer_is_skipped_on_save_when_settings_manager_is_passed(tmp_path):
    _write_grant_test_plugin(
        tmp_path / "plugins", "statefulgrant2", register_body=_stateful_grant_py_body("Grant Widget 2"),
    )
    registry = discover_plugins(tmp_path / "plugins")
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)
    settings_manager.set_plugin_grant("statefulgrant2", True)
    parent = canvas_document.add_chat_node(10, 20, "parent", is_user=False)

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Grant Widget 2", parent.id])
    )
    assert node_id is not None

    # Revoke BEFORE saving.
    settings_manager.set_plugin_grant("statefulgrant2", False)

    chat_data = build_chat_data(canvas_document, plugin_registry=registry, settings_manager=settings_manager)
    saved_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "statefulgrant2.widget")
    # The node's universal title/content still saves; plugin_state is
    # withheld because the grant was revoked before this save.
    assert saved_payload["title"] == "Widget"
    assert "plugin_state" not in saved_payload

    # Backward-compat sanity: WITHOUT settings_manager (this parameter's
    # own default, and every pre-fix call site), the exact same document
    # still saves its plugin_state - preserving prior ungated behavior for
    # any caller that doesn't opt in.
    chat_data_ungated = build_chat_data(canvas_document, plugin_registry=registry)
    saved_ungated = next(n for n in chat_data_ungated["nodes"] if n["node_type"] == "statefulgrant2.widget")
    assert saved_ungated["plugin_state"] == {"clicks": 3}


def test_ungranted_plugins_deserializer_is_skipped_on_load_when_settings_manager_is_passed(tmp_path):
    _write_grant_test_plugin(
        tmp_path / "plugins", "statefulgrant3", register_body=_stateful_grant_py_body("Grant Widget 3"),
    )
    registry = discover_plugins(tmp_path / "plugins")
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)
    settings_manager.set_plugin_grant("statefulgrant3", True)
    parent = canvas_document.add_chat_node(10, 20, "parent", is_user=False)

    node_id = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Grant Widget 3", parent.id])
    )
    assert node_id is not None

    # Save while GRANTED, so plugin_state genuinely made it into the file.
    chat_data = build_chat_data(canvas_document, plugin_registry=registry, settings_manager=settings_manager)
    notes_data = chat_data.pop("notes_data")
    pins_data = chat_data.pop("pins_data")
    saved_payload = next(n for n in chat_data["nodes"] if n["node_type"] == "statefulgrant3.widget")
    assert saved_payload["plugin_state"] == {"clicks": 3}

    # Revoke BEFORE reloading.
    settings_manager.set_plugin_grant("statefulgrant3", False)

    doc2 = SceneDocument()
    restore_chat_into_document(
        doc2, {"data": chat_data}, notes_data, pins_data,
        plugin_registry=registry, settings_manager=settings_manager,
    )
    reloaded = next(n for n in doc2.nodes.values() if n.kind == "statefulgrant3.widget")
    # The node itself still round-trips (title/content survive) - only its
    # custom deserialize()-produced state is withheld because the grant was
    # revoked before this reload.
    assert reloaded.title == "Widget"
    assert reloaded.state is None


# -- 4: unknown scope string rejected at discovery, fail-soft ----------------


def test_unknown_scope_in_manifest_is_rejected_at_discovery_fail_soft_per_plugin(tmp_path):
    _write_grant_test_plugin(
        tmp_path / "plugins", "badscope",
        scopes_toml='[scopes]\ngrants = ["not.a.real.scope"]',
        picker_name="Bad Scope Node",
    )
    _write_grant_test_plugin(
        tmp_path / "plugins", "goodsibling",
        scopes_toml='[scopes]\ngrants = ["graph.mutate"]',
        picker_name="Good Sibling Node",
    )

    registry = discover_plugins(tmp_path / "plugins")

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "badscope"
    assert "not.a.real.scope" in registry.load_errors[0].message
    assert "badscope" not in registry.manifests
    assert "Bad Scope Node" not in registry.picker_entries
    # The sibling plugin is completely unaffected - a bad manifest never
    # takes down anyone else's discovery.
    assert "goodsibling" in registry.manifests
    assert "Good Sibling Node" in registry.picker_entries


# -- 4b: malformed [scopes].grants TYPE (not a list-of-strings at all) -------
#
# A distinct code path from "unknown scope string" above: this is the
# isinstance/type-shape guard (backend/plugin_sdk.py's `if not isinstance(
# raw_grants, list) or not all(isinstance(g, str) for g in raw_grants)`),
# not the "known scope set" membership check - a malformed TOML value (a
# bare string instead of an array, or an array holding a non-string
# element) never even reaches the KNOWN_SCOPES comparison.


def test_grants_value_that_is_a_bare_string_not_a_list_is_rejected_at_discovery_fail_soft_per_plugin(tmp_path):
    _write_grant_test_plugin(
        tmp_path / "plugins", "badgrantstype",
        # TOML-valid, but [scopes].grants must be an array - a bare string
        # is exactly the "not isinstance(raw_grants, list)" branch.
        scopes_toml='[scopes]\ngrants = "graph.mutate"',
        picker_name="Bad Grants Type Node",
    )
    _write_grant_test_plugin(
        tmp_path / "plugins", "goodsibling2",
        scopes_toml='[scopes]\ngrants = ["graph.mutate"]',
        picker_name="Good Sibling Two Node",
    )

    registry = discover_plugins(tmp_path / "plugins")

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "badgrantstype"
    assert "must be a list of strings" in registry.load_errors[0].message
    assert "badgrantstype" not in registry.manifests
    assert "Bad Grants Type Node" not in registry.picker_entries
    # The sibling plugin is completely unaffected.
    assert "goodsibling2" in registry.manifests
    assert "Good Sibling Two Node" in registry.picker_entries


def test_grants_list_containing_a_non_string_element_is_rejected_at_discovery_fail_soft_per_plugin(tmp_path):
    _write_grant_test_plugin(
        tmp_path / "plugins", "badgrantselement",
        # A syntactically real TOML array, but one element is an integer,
        # not a string - "not all(isinstance(g, str) for g in raw_grants)".
        scopes_toml="[scopes]\ngrants = [\"graph.mutate\", 42]",
        picker_name="Bad Grants Element Node",
    )

    registry = discover_plugins(tmp_path / "plugins")

    assert len(registry.load_errors) == 1
    assert registry.load_errors[0].plugin_dir == "badgrantselement"
    assert "must be a list of strings" in registry.load_errors[0].message
    assert "badgrantselement" not in registry.manifests
    assert "Bad Grants Element Node" not in registry.picker_entries


def test_grants_value_that_is_a_bare_string_raises_at_manifest_load_time_directly():
    from backend.plugin_sdk import _load_manifest
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp) / "badgrantstype"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.toml").write_text(
            textwrap.dedent("""\
                [plugin]
                id = "badgrantstype"
                name = "Bad Grants Type Plugin"
                version = "0.1.0"
                sdk_api_version = 1
                entry_point = "plugin:register"

                [scopes]
                grants = "graph.mutate"
            """),
            encoding="utf-8",
        )
        with pytest.raises(PluginRegistrationError, match="must be a list of strings"):
            _load_manifest(plugin_dir / "plugin.toml", plugin_dir)


def test_unknown_scope_error_raises_at_manifest_load_time_directly():
    from backend.plugin_sdk import _load_manifest
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp) / "badscope"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "plugin.toml"
        manifest_path.write_text(
            textwrap.dedent("""\
                [plugin]
                id = "badscope"
                name = "Bad Scope"
                version = "0.1.0"
                sdk_api_version = 1
                entry_point = "plugin:register"

                [scopes]
                grants = ["totally.unknown"]
            """),
            encoding="utf-8",
        )
        with pytest.raises(PluginRegistrationError, match="unknown scope"):
            _load_manifest(manifest_path, plugin_dir)


def test_scopes_grants_parses_a_valid_subset_of_known_scopes():
    from backend.plugin_sdk import _load_manifest
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp) / "goodscope"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "plugin.toml"
        manifest_path.write_text(
            textwrap.dedent("""\
                [plugin]
                id = "goodscope"
                name = "Good Scope"
                version = "0.1.0"
                sdk_api_version = 1
                entry_point = "plugin:register"

                [scopes]
                grants = ["graph.mutate", "net.fetch"]
            """),
            encoding="utf-8",
        )
        manifest = _load_manifest(manifest_path, plugin_dir)
        assert manifest.scopes_grants == frozenset({"graph.mutate", "net.fetch"})


def test_scopes_grants_defaults_to_empty_frozenset_when_table_omitted():
    from backend.plugin_sdk import _load_manifest
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp) / "noscope"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "plugin.toml"
        manifest_path.write_text(
            textwrap.dedent("""\
                [plugin]
                id = "noscope"
                name = "No Scope"
                version = "0.1.0"
                sdk_api_version = 1
                entry_point = "plugin:register"
            """),
            encoding="utf-8",
        )
        manifest = _load_manifest(manifest_path, plugin_dir)
        assert manifest.scopes_grants == frozenset()


# -- 5: invokePluginIntent denies/allows a custom plugin intent -------------


def _write_intent_plugin(plugins_root, plugin_id):
    """A plugin declaring ONE custom intent via HostContext.register_intent -
    the mechanism 14.1 left unwired, closed by this stage's invokePluginIntent
    chokepoint. `_do_thing` returns a plugin_id-derived string, proof enough
    that it (and not some other plugin's handler) actually ran."""
    plugin_dir = plugins_root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        textwrap.dedent(f"""\
            [plugin]
            id = "{plugin_id}"
            name = "{plugin_id} Plugin"
            version = "0.1.0"
            sdk_api_version = 1
            entry_point = "plugin:register"

            [scopes]
            grants = ["graph.mutate"]
        """),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        textwrap.dedent("""\
            from backend.plugin_sdk import HostContext


            def _do_thing(document, run_ctx):
                return f"did-the-thing:{run_ctx.plugin_id}"


            def register(host: HostContext) -> None:
                host.register_intent("do_thing", _do_thing)
        """),
        encoding="utf-8",
    )
    return plugin_dir


def test_invoke_plugin_intent_denies_an_ungranted_plugins_custom_intent(tmp_path):
    _write_intent_plugin(tmp_path / "plugins", "intentplug")
    registry = discover_plugins(tmp_path / "plugins")
    assert len(registry.intents) == 1
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "invokePluginIntent", ["intentplug", "do_thing"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert "approval" in notifications.message.lower()


def test_invoke_plugin_intent_allows_the_same_dispatch_once_granted(tmp_path):
    _write_intent_plugin(tmp_path / "plugins", "intentplug2")
    registry = discover_plugins(tmp_path / "plugins")
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)

    denied = asyncio.run(
        bus.dispatch_intent("app-plugins", "invokePluginIntent", ["intentplug2", "do_thing"])
    )
    assert denied is None

    settings_manager.set_plugin_grant("intentplug2", True)

    allowed = asyncio.run(
        bus.dispatch_intent("app-plugins", "invokePluginIntent", ["intentplug2", "do_thing"])
    )
    assert allowed == "did-the-thing:intentplug2"


def test_invoke_plugin_intent_shows_a_warning_for_an_unknown_plugin_or_intent_name(tmp_path):
    registry = discover_plugins(tmp_path / "empty-plugins")
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)

    result = asyncio.run(
        bus.dispatch_intent("app-plugins", "invokePluginIntent", ["ghost_plugin", "ghost_intent"])
    )

    assert result is None
    assert notifications.visible is True
    assert notifications.msg_type == "warning"
    assert "Unknown plugin intent" in notifications.message


# -- SettingsManager.get_plugin_grants()/set_plugin_grant() persistence -----


def test_get_plugin_grants_defaults_to_empty_dict_on_a_fresh_store(tmp_path):
    manager = SettingsManager(tmp_path / "session.dat")
    assert manager.get_plugin_grants() == {}


def test_set_plugin_grant_persists_and_round_trips_through_a_reload(tmp_path):
    state_file = tmp_path / "session.dat"
    manager = SettingsManager(state_file)
    manager.set_plugin_grant("hello_node", True)

    reloaded = SettingsManager(state_file)
    assert reloaded.get_plugin_grants() == {"hello_node": True}


def test_set_plugin_grant_touches_only_the_named_plugin_leaving_others_untouched(tmp_path):
    manager = SettingsManager(tmp_path / "session.dat")
    manager.set_plugin_grant("plugin_a", True)
    manager.set_plugin_grant("plugin_b", True)
    manager.set_plugin_grant("plugin_a", False)

    grants = manager.get_plugin_grants()
    assert grants["plugin_a"] is False
    assert grants["plugin_b"] is True


def test_a_plugin_id_with_no_stored_entry_reads_as_not_granted_via_caller_default(tmp_path):
    manager = SettingsManager(tmp_path / "session.dat")
    manager.set_plugin_grant("known_plugin", True)

    # get_plugin_grants() itself does not synthesize a default for
    # "unknown_plugin" - the caller's own .get(plugin_id, False) does, same
    # as every real call site in backend/plugins.py.
    assert manager.get_plugin_grants().get("unknown_plugin", False) is False


def test_set_plugin_grant_with_a_blank_plugin_id_is_a_no_op(tmp_path):
    manager = SettingsManager(tmp_path / "session.dat")
    manager.set_plugin_grant("", True)
    manager.set_plugin_grant("   ", True)
    assert manager.get_plugin_grants() == {}


# -- setPluginGrant intent: persist + republish ------------------------------


def test_set_plugin_grant_intent_persists_and_republishes_the_app_plugins_topic(tmp_path):
    _write_grant_test_plugin(
        tmp_path / "plugins", "livegrant", scopes_toml='[scopes]\ngrants = ["graph.mutate"]',
        picker_name="Live Grant Node",
    )
    registry = discover_plugins(tmp_path / "plugins")
    bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)

    class Recorder:
        def __init__(self):
            self.messages = []

        async def send_json(self, data):
            self.messages.append(data)

    recorder = Recorder()
    bus.attach(recorder)

    asyncio.run(
        bus.dispatch_intent("app-plugins", "setPluginGrant", ["livegrant", True])
    )

    assert settings_manager.get_plugin_grants() == {"livegrant": True}
    # Re-published so any observer (picker, Settings UI) sees the new state
    # immediately.
    published = [m for m in recorder.messages if m.get("topic") == "app-plugins"]
    assert published
    grants_row = next(r for r in published[-1]["payload"]["grants"] if r["pluginId"] == "livegrant")
    assert grants_row["granted"] is True

    # Now the grant is real - the SAME plugin can create a node.
    parent = canvas_document.add_node(10, 20, "parent")
    created = asyncio.run(
        bus.dispatch_intent("app-plugins", "executePlugin", ["Live Grant Node", parent.id])
    )
    assert created is not None


# -- "grants" payload shape: one row per distinct plugin_id, built-ins absent


def test_grants_payload_has_one_row_per_distinct_plugin_id_not_per_picker_entry(tmp_path):
    # A single plugin registering TWO picker entries under one plugin_id
    # must still produce exactly ONE grants row.
    plugin_dir = tmp_path / "plugins" / "multientry"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        textwrap.dedent("""\
            [plugin]
            id = "multientry"
            name = "Multi Entry Plugin"
            version = "0.1.0"
            sdk_api_version = 1
            entry_point = "plugin:register"

            [scopes]
            grants = ["graph.mutate"]
        """),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        textwrap.dedent("""\
            from backend.plugin_sdk import HostContext, PluginNodeSeed


            def _make_a(document, run_ctx, parent_id):
                return PluginNodeSeed(title="A", content="")


            def _make_b(document, run_ctx, parent_id):
                return PluginNodeSeed(title="B", content="")


            def register(host: HostContext) -> None:
                host.register_node_kind("kind_a", _make_a, requires_parent=True)
                host.register_node_kind("kind_b", _make_b, requires_parent=True)
                host.register_picker_entry(
                    node_kind="kind_a", name="Multi A", description="d", category="More Plugins",
                )
                host.register_picker_entry(
                    node_kind="kind_b", name="Multi B", description="d", category="More Plugins",
                )
        """),
        encoding="utf-8",
    )
    registry = discover_plugins(tmp_path / "plugins")
    settings_manager = SettingsManager(tmp_path / "store" / "session.dat")

    payload = plugins_payload(registry, settings_manager)
    rows = [r for r in payload["grants"] if r["pluginId"] == "multientry"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Multi Entry Plugin"
    assert rows[0]["scopes"] == ["graph.mutate"]
    assert rows[0]["granted"] is False
