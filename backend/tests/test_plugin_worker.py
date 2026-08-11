"""ADR-014 stage 14.5: out-of-process third-party plugin execution behind
the host API + guard.

Exit criterion this file proves, literally, with REAL subprocess execution
(never mocked): "a third-party plugin cannot read secrets or exceed its
scopes (test); tools flow to the registry." Sections below, each proving
one load-bearing claim from the stage's own design:

A. [runtime] manifest parsing - the opt-in table, fail-soft on an unknown
   value, byte-identical default for every plugin that omits it.
B. Discovery of an out-of-process plugin spawns a REAL worker subprocess,
   collects its registrations via "get_registrations", and the resulting
   picker entry appears in plugins_payload() exactly like an in-process
   plugin's would.
C. THE empirical secret-containment proof: a fake secret-shaped env var is
   planted on the HOST test process, the shipped plugins/sandboxed_demo
   plugin's node-creation is triggered, and the created node's content -
   built entirely inside the WORKER subprocess - is asserted to contain
   NEITHER the planted value NOR the env var's own name.
D. The EXISTING 14.4 grant gate (SettingsManager.get_plugin_grants(),
   backend/plugins.py's _execute_discovered_plugin) already covers
   out-of-process plugins for free - proven with a real out-of-process
   plugin, the same notification/no-node/no-command shape as 14.4's own
   in-process denial tests.
E. A worker subprocess crash (at registration time, and mid-call) or a
   call that times out produces a clean, typed PluginWorkerError - never a
   hang, never some other uncaught exception type.
F. register_plugin_tools() registers a plugin's custom intent into a real
   ToolRegistry, scoped from its manifest, denied pre-handler on
   insufficient scopes (mirroring backend/tools.py's own scope-deny
   tests), and reaches the plugin's real handler once both the tool's
   scopes AND the install-time Settings grant are satisfied - proven for
   BOTH an in-process and an out-of-process plugin's intent, since
   PluginIntentSpec.handler is the same shape either way.
G. backend/agents.py's builder_tool_registry() really wires
   register_plugin_tools() in, using the real shipped plugins/ root.

Every out-of-process test that spawns a worker closes it in a `finally`
block (via registry.worker_clients.values() or a directly-constructed
PluginWorkerClient) - the SAME "always close what you connect()ed" discipline
backend/tests/test_mcp_client.py's own McpStdioClient tests already use.
"""

from __future__ import annotations

import asyncio
import shutil
import textwrap

import pytest

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import (
    DEFAULT_PLUGINS_ROOT,
    PluginRegistrationError,
    PluginWorkerClient,
    PluginWorkerError,
    discover_plugins,
)
from backend.plugins import plugins_payload, register_plugin_tools, register_plugins
from backend.providers.base import ToolCall
from backend.tools import RunContext, ToolRegistry
from graphlink_settings_store import SettingsManager


def _close_workers(registry) -> None:
    for client in registry.worker_clients.values():
        client.close()


def _write_out_of_process_plugin(
    plugins_root, plugin_id, *, py_body, scopes_toml='[scopes]\ngrants = ["graph.mutate"]',
):
    """Writes one real out-of-process plugin.toml + plugin.py under
    plugins_root/plugin_id - mirrors backend/tests/test_plugin_grants.py's
    own _write_grant_test_plugin, plus the new [runtime] table."""
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

            [runtime]
            isolation = "out-of-process"

            {scopes_toml}
        """),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(py_body, encoding="utf-8")
    return plugin_dir


_SIMPLE_NODE_PY_BODY = textwrap.dedent("""\
    from backend.plugin_sdk import HostContext, PluginNodeSeed


    def _make(document, run_ctx, parent_id):
        parent = document.nodes[parent_id]
        return PluginNodeSeed(title="OOP Node", content=f"branched from {parent.title}")


    def register(host: HostContext) -> None:
        host.register_node_kind("thing", _make, requires_parent=True)
        host.register_picker_entry(
            node_kind="thing", name="OOP Thing", description="d", category="More Plugins",
        )
""")


def _wired_bus(registry, tmp_path, *, dirname="oop-store"):
    settings_manager = SettingsManager(tmp_path / dirname / "session.dat")
    notifications = NotificationState()
    bus = SessionBus("plugin-worker-test")
    bus.register_topic("notification", notifications.payload)
    canvas_document = SceneDocument()
    bus.register_topic("scene", canvas_document.scene_payload)
    register_plugins(bus, notifications, canvas_document, settings_manager, plugin_registry=registry)
    return bus, notifications, canvas_document, settings_manager


# -- A: [runtime] manifest parsing -------------------------------------------


def test_runtime_isolation_defaults_to_in_process_when_table_omitted():
    from backend.plugin_sdk import _load_manifest
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp) / "noruntime"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "plugin.toml"
        manifest_path.write_text(
            textwrap.dedent("""\
                [plugin]
                id = "noruntime"
                name = "No Runtime"
                version = "0.1.0"
                sdk_api_version = 1
                entry_point = "plugin:register"
            """),
            encoding="utf-8",
        )
        manifest = _load_manifest(manifest_path, plugin_dir)
        assert manifest.runtime_isolation == "in-process"


def test_runtime_isolation_out_of_process_parses():
    from backend.plugin_sdk import _load_manifest
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp) / "oopplugin"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "plugin.toml"
        manifest_path.write_text(
            textwrap.dedent("""\
                [plugin]
                id = "oopplugin"
                name = "OOP Plugin"
                version = "0.1.0"
                sdk_api_version = 1
                entry_point = "plugin:register"

                [runtime]
                isolation = "out-of-process"
            """),
            encoding="utf-8",
        )
        manifest = _load_manifest(manifest_path, plugin_dir)
        assert manifest.runtime_isolation == "out-of-process"


def test_unknown_runtime_isolation_error_raises_at_manifest_load_time_directly():
    from backend.plugin_sdk import _load_manifest
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        plugin_dir = Path(tmp) / "badruntime"
        plugin_dir.mkdir()
        manifest_path = plugin_dir / "plugin.toml"
        manifest_path.write_text(
            textwrap.dedent("""\
                [plugin]
                id = "badruntime"
                name = "Bad Runtime"
                version = "0.1.0"
                sdk_api_version = 1
                entry_point = "plugin:register"

                [runtime]
                isolation = "in-a-sandbox-somewhere"
            """),
            encoding="utf-8",
        )
        with pytest.raises(PluginRegistrationError, match="isolation"):
            _load_manifest(manifest_path, plugin_dir)


def test_unknown_runtime_isolation_is_rejected_at_discovery_fail_soft_per_plugin(tmp_path):
    _write_out_of_process_plugin(
        tmp_path / "plugins", "badruntime2", py_body=_SIMPLE_NODE_PY_BODY,
    )
    # Corrupt the freshly-written manifest's [runtime] table directly.
    manifest_path = tmp_path / "plugins" / "badruntime2" / "plugin.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace(
            'isolation = "out-of-process"', 'isolation = "teleported"'
        ),
        encoding="utf-8",
    )
    good_dir = tmp_path / "plugins" / "goodsibling"
    good_dir.mkdir(parents=True)
    (good_dir / "plugin.toml").write_text(
        textwrap.dedent("""\
            [plugin]
            id = "goodsibling"
            name = "Good Sibling"
            version = "0.1.0"
            sdk_api_version = 1
            entry_point = "plugin:register"
        """),
        encoding="utf-8",
    )
    (good_dir / "plugin.py").write_text(
        "from backend.plugin_sdk import HostContext\n\n\ndef register(host: HostContext) -> None:\n    pass\n",
        encoding="utf-8",
    )

    registry = discover_plugins(tmp_path / "plugins")
    try:
        assert len(registry.load_errors) == 1
        assert registry.load_errors[0].plugin_dir == "badruntime2"
        assert "badruntime2" not in registry.manifests
        assert "goodsibling" in registry.manifests
    finally:
        _close_workers(registry)


# -- B: discovery spawns a real worker + collects registrations -------------


def test_discovery_of_an_out_of_process_plugin_spawns_a_real_worker_and_collects_registrations(tmp_path):
    _write_out_of_process_plugin(tmp_path / "plugins", "oop_basic", py_body=_SIMPLE_NODE_PY_BODY)

    registry = discover_plugins(tmp_path / "plugins")
    try:
        assert registry.load_errors == []
        assert "oop_basic" in registry.worker_clients
        worker = registry.worker_clients["oop_basic"]
        assert worker.is_connected is True
        assert "oop_basic.thing" in registry.node_kinds
        assert "OOP Thing" in registry.picker_entries
        assert registry.picker_entries["OOP Thing"].plugin_id == "oop_basic"
    finally:
        _close_workers(registry)


def test_out_of_process_plugins_picker_entry_appears_in_plugins_payload_like_an_in_process_ones_would(tmp_path):
    _write_out_of_process_plugin(tmp_path / "plugins", "oop_payload", py_body=_SIMPLE_NODE_PY_BODY)
    registry = discover_plugins(tmp_path / "plugins")
    try:
        settings_manager = SettingsManager(tmp_path / "store" / "session.dat")
        payload = plugins_payload(registry, settings_manager)
        grants_row = next(r for r in payload["grants"] if r["pluginId"] == "oop_payload")
        assert grants_row["scopes"] == ["graph.mutate"]
        assert grants_row["granted"] is False

        more_plugins = next(c for c in payload["categories"] if c["name"] == "More Plugins")
        assert any(p["name"] == "OOP Thing" for p in more_plugins["plugins"])
    finally:
        _close_workers(registry)


# -- C: THE empirical secret-containment proof -------------------------------


def test_out_of_process_plugin_worker_cannot_see_a_planted_host_secret_env_var(tmp_path, monkeypatch):
    """THE stage 14.5 exit criterion, proven empirically: plant
    GRAPHLINK_OPENAI_API_KEY=fake-test-secret-value on the HOST test
    process, discover a fresh copy of the REAL shipped
    plugins/sandboxed_demo plugin (a fresh tmp_path copy, so the env var is
    guaranteed to be set BEFORE this specific worker's Popen call, and this
    test never touches the shared, memoized real-repo-root registry any
    other test might have already populated), grant it, create its node,
    and assert the WORKER's own reported view of its environment - which
    crossed the RPC boundary as plain node content - contains neither the
    secret's value nor its name."""
    monkeypatch.setenv("GRAPHLINK_OPENAI_API_KEY", "fake-test-secret-value")

    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()
    shutil.copytree(DEFAULT_PLUGINS_ROOT / "sandboxed_demo", plugins_root / "sandboxed_demo")

    registry = discover_plugins(plugins_root)
    try:
        bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)
        settings_manager.set_plugin_grant("sandboxed_demo", True)
        parent = canvas_document.add_node(10, 20, "parent")

        node_id = asyncio.run(
            bus.dispatch_intent("app-plugins", "executePlugin", ["Sandboxed Env Probe", parent.id])
        )

        assert node_id is not None
        node = canvas_document.nodes[node_id]
        assert "fake-test-secret-value" not in node.content
        assert "GRAPHLINK_OPENAI_API_KEY" not in node.content
        # Sanity check the probe actually ran and reported SOMETHING (proves
        # this isn't a vacuous pass from an empty/failed report) - PATH is
        # allowlisted by graphlink_process_env.safe_subprocess_env() and
        # every real Windows/dev environment has one.
        assert "PATH=" in node.content or "Path=" in node.content
    finally:
        _close_workers(registry)


# -- D: the EXISTING 14.4 grant gate already covers out-of-process plugins --


def test_ungranted_out_of_process_plugin_denies_node_creation_same_shape_as_in_process(tmp_path):
    _write_out_of_process_plugin(tmp_path / "plugins", "oop_deny", py_body=_SIMPLE_NODE_PY_BODY)
    registry = discover_plugins(tmp_path / "plugins")
    try:
        bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)
        parent = canvas_document.add_node(10, 20, "parent")

        result = asyncio.run(
            bus.dispatch_intent("app-plugins", "executePlugin", ["OOP Thing", parent.id])
        )

        assert result is None
        assert notifications.visible is True
        assert notifications.msg_type == "warning"
        assert "approval" in notifications.message.lower()
        assert len(canvas_document.nodes) == 1  # only the parent
        assert canvas_document.can_undo() is False
    finally:
        _close_workers(registry)


def test_granting_an_out_of_process_plugin_then_allows_node_creation_to_succeed(tmp_path):
    _write_out_of_process_plugin(tmp_path / "plugins", "oop_allow", py_body=_SIMPLE_NODE_PY_BODY)
    registry = discover_plugins(tmp_path / "plugins")
    try:
        bus, notifications, canvas_document, settings_manager = _wired_bus(registry, tmp_path)
        parent = canvas_document.add_node(10, 20, "parent")

        denied = asyncio.run(
            bus.dispatch_intent("app-plugins", "executePlugin", ["OOP Thing", parent.id])
        )
        assert denied is None

        settings_manager.set_plugin_grant("oop_allow", True)

        created = asyncio.run(
            bus.dispatch_intent("app-plugins", "executePlugin", ["OOP Thing", parent.id])
        )
        assert created is not None
        node = canvas_document.nodes[created]
        assert node.kind == "oop_allow.thing"
        assert node.content == "branched from parent"
        assert canvas_document.can_undo() is True
    finally:
        _close_workers(registry)


# -- E: worker crash / malformed response is handled cleanly ----------------


_CRASH_ON_REGISTER_PY_BODY = textwrap.dedent("""\
    from backend.plugin_sdk import HostContext


    def register(host: HostContext) -> None:
        raise RuntimeError("this plugin's register() call always fails")
""")


def test_a_worker_that_crashes_during_register_produces_a_discovery_time_load_error_not_a_hang(tmp_path):
    _write_out_of_process_plugin(
        tmp_path / "plugins", "oop_crash_register", py_body=_CRASH_ON_REGISTER_PY_BODY,
    )
    good_dir = tmp_path / "plugins" / "oop_good_sibling"
    _write_out_of_process_plugin(
        tmp_path / "plugins", "oop_good_sibling", py_body=_SIMPLE_NODE_PY_BODY,
    )

    registry = discover_plugins(tmp_path / "plugins")
    try:
        assert len(registry.load_errors) == 1
        assert registry.load_errors[0].plugin_dir == "oop_crash_register"
        assert "oop_crash_register" not in registry.manifests
        # A crashed worker must never be left resident for a plugin that
        # never finished loading.
        assert "oop_crash_register" not in registry.worker_clients
        # The sibling plugin's OWN worker is completely unaffected.
        assert "oop_good_sibling" in registry.manifests
        assert "oop_good_sibling" in registry.worker_clients
    finally:
        _close_workers(registry)


_CRASH_MID_CALL_PY_BODY = textwrap.dedent("""\
    import os

    from backend.plugin_sdk import HostContext, PluginNodeSeed


    def _make(document, run_ctx, parent_id):
        os._exit(1)  # a genuine process crash - NOT a caught Exception


    def register(host: HostContext) -> None:
        host.register_node_kind("thing", _make, requires_parent=True)
        host.register_picker_entry(
            node_kind="thing", name="Crash Thing", description="d", category="More Plugins",
        )
""")


def test_a_worker_that_crashes_mid_call_raises_a_clean_plugin_worker_error_not_a_hang(tmp_path):
    plugin_dir = _write_out_of_process_plugin(
        tmp_path / "plugins", "oop_crash_mid_call", py_body=_CRASH_MID_CALL_PY_BODY,
    )
    worker = PluginWorkerClient(plugin_id="oop_crash_mid_call", source_dir=plugin_dir, timeout=10.0)
    try:
        worker.connect()
        response = worker.call("get_registrations", {})
        assert response["node_kinds"] == [{"local_kind": "thing", "requires_parent": True}]

        with pytest.raises(PluginWorkerError, match="closed its output unexpectedly"):
            worker.call(
                "invoke_factory",
                {"kind": "thing", "parent_snapshot": {"id": "p1", "title": "Parent", "content": "", "kind": "chat"}},
            )
    finally:
        worker.close()


_SLOW_INTENT_PY_BODY = textwrap.dedent("""\
    import time

    from backend.plugin_sdk import HostContext


    def _slow(document, run_ctx):
        time.sleep(5)
        return "too slow"


    def register(host: HostContext) -> None:
        host.register_intent("slow", _slow)
""")


def test_a_call_that_exceeds_the_configured_timeout_raises_a_clean_plugin_worker_error(tmp_path):
    plugin_dir = _write_out_of_process_plugin(
        tmp_path / "plugins", "oop_slow", py_body=_SLOW_INTENT_PY_BODY,
    )
    # Default timeout for connect + the fast "get_registrations" call
    # (interpreter cold-start alone can take longer than a tight deadline on
    # a loaded CI box) - `timeout` is a plain instance attribute, so it is
    # deliberately tightened AFTER the worker is already up and responsive,
    # isolating what this test actually wants to prove (a genuinely slow
    # CALL times out cleanly) from unrelated process-startup variance.
    worker = PluginWorkerClient(plugin_id="oop_slow", source_dir=plugin_dir)
    try:
        worker.connect()
        worker.call("get_registrations", {})  # a fast call succeeds first
        worker.timeout = 0.5

        with pytest.raises(PluginWorkerError, match="timed out"):
            worker.call("invoke_intent", {"name": "slow", "args": {}})
    finally:
        worker.close()


def test_invoke_factory_with_an_unknown_kind_returns_a_clean_error_not_a_crash(tmp_path):
    plugin_dir = _write_out_of_process_plugin(
        tmp_path / "plugins", "oop_unknown_kind", py_body=_SIMPLE_NODE_PY_BODY,
    )
    worker = PluginWorkerClient(plugin_id="oop_unknown_kind", source_dir=plugin_dir)
    try:
        worker.connect()
        worker.call("get_registrations", {})

        with pytest.raises(PluginWorkerError, match="unknown node kind"):
            worker.call(
                "invoke_factory",
                {"kind": "not_a_real_kind", "parent_snapshot": {"id": "p1", "title": "P", "content": "", "kind": "x"}},
            )
        # The worker itself is still alive and usable after a clean,
        # caught, per-request error - one bad call does not take down the
        # whole resident process.
        assert worker.is_connected is True
    finally:
        worker.close()


def test_calling_before_connect_raises_a_clean_plugin_worker_error(tmp_path):
    plugin_dir = _write_out_of_process_plugin(
        tmp_path / "plugins", "oop_not_connected", py_body=_SIMPLE_NODE_PY_BODY,
    )
    worker = PluginWorkerClient(plugin_id="oop_not_connected", source_dir=plugin_dir)
    with pytest.raises(PluginWorkerError):
        worker.call("get_registrations", {})


def test_worker_client_close_is_idempotent(tmp_path):
    plugin_dir = _write_out_of_process_plugin(
        tmp_path / "plugins", "oop_close_twice", py_body=_SIMPLE_NODE_PY_BODY,
    )
    worker = PluginWorkerClient(plugin_id="oop_close_twice", source_dir=plugin_dir)
    worker.connect()
    worker.close()
    worker.close()  # must not raise


# -- F: register_plugin_tools() flows plugin intents to the ToolRegistry ----


_INTENT_PY_BODY = textwrap.dedent("""\
    from backend.plugin_sdk import HostContext


    def _do_thing(document, run_ctx):
        return f"did-the-thing:{run_ctx.plugin_id}"


    def register(host: HostContext) -> None:
        host.register_intent("do_thing", _do_thing)
""")


def _run(coro):
    return asyncio.run(coro)


def test_register_plugin_tools_registers_a_namespaced_tool_with_the_manifests_scopes(tmp_path):
    _write_out_of_process_plugin(tmp_path / "plugins", "tool_plug", py_body=_INTENT_PY_BODY)
    registry = discover_plugins(tmp_path / "plugins")
    try:
        settings_manager = SettingsManager(tmp_path / "store" / "session.dat")
        canvas_document = SceneDocument()
        tool_registry = ToolRegistry()

        registered = register_plugin_tools(tool_registry, registry, settings_manager, canvas_document)

        assert registered == ("plugin:tool_plug:do_thing",)
        assert tool_registry.scopes_for("plugin:tool_plug:do_thing") == frozenset({"graph.mutate"})
    finally:
        _close_workers(registry)


def test_register_plugin_tools_denies_insufficient_scopes_before_the_handler_runs(tmp_path):
    _write_out_of_process_plugin(tmp_path / "plugins", "tool_plug2", py_body=_INTENT_PY_BODY)
    registry = discover_plugins(tmp_path / "plugins")
    try:
        settings_manager = SettingsManager(tmp_path / "store" / "session.dat")
        settings_manager.set_plugin_grant("tool_plug2", True)  # granted, but scope is still missing
        canvas_document = SceneDocument()
        tool_registry = ToolRegistry()
        register_plugin_tools(tool_registry, registry, settings_manager, canvas_document)

        prompted = []

        async def request_approval(call):
            prompted.append(call)
            return True

        ctx = RunContext(granted_scopes=frozenset(), request_approval=request_approval)  # no graph.mutate
        call = ToolCall(id="1", name="plugin:tool_plug2:do_thing", arguments={})
        result = _run(tool_registry.invoke(call, ctx))

        assert result.is_error is True
        assert "scope" in result.content.lower()
        assert prompted == []  # denied before any approval prompt, let alone the handler
    finally:
        _close_workers(registry)


def test_register_plugin_tools_with_sufficient_scopes_but_no_settings_grant_is_denied(tmp_path):
    _write_out_of_process_plugin(tmp_path / "plugins", "tool_plug3", py_body=_INTENT_PY_BODY)
    registry = discover_plugins(tmp_path / "plugins")
    try:
        settings_manager = SettingsManager(tmp_path / "store" / "session.dat")  # never granted
        canvas_document = SceneDocument()
        tool_registry = ToolRegistry()
        register_plugin_tools(tool_registry, registry, settings_manager, canvas_document)

        async def request_approval(call):
            return True

        ctx = RunContext(granted_scopes=frozenset({"graph.mutate"}), request_approval=request_approval)
        call = ToolCall(id="1", name="plugin:tool_plug3:do_thing", arguments={})
        result = _run(tool_registry.invoke(call, ctx))

        assert result.is_error is True
        assert "approval" in result.content.lower()
    finally:
        _close_workers(registry)


def test_register_plugin_tools_with_scopes_and_grant_reaches_the_out_of_process_plugins_real_handler(tmp_path):
    """THE full round trip: ToolRegistry.invoke() -> the install-time-grant
    re-check -> the RPC-backed wrapper closure -> the REAL out-of-process
    plugin's own _do_thing, running inside its worker subprocess - and its
    real string return value makes it all the way back as ToolResult.content."""
    _write_out_of_process_plugin(tmp_path / "plugins", "tool_plug4", py_body=_INTENT_PY_BODY)
    registry = discover_plugins(tmp_path / "plugins")
    try:
        settings_manager = SettingsManager(tmp_path / "store" / "session.dat")
        settings_manager.set_plugin_grant("tool_plug4", True)
        canvas_document = SceneDocument()
        tool_registry = ToolRegistry()
        register_plugin_tools(tool_registry, registry, settings_manager, canvas_document)

        prompted = []

        async def request_approval(call):
            prompted.append(call)
            return True

        ctx = RunContext(granted_scopes=frozenset({"graph.mutate"}), request_approval=request_approval)
        call = ToolCall(id="1", name="plugin:tool_plug4:do_thing", arguments={})
        result = _run(tool_registry.invoke(call, ctx))

        assert result.is_error is False
        assert result.content == "did-the-thing:tool_plug4"
        assert len(prompted) == 1  # approval="always" - the approval callback WAS consulted
    finally:
        _close_workers(registry)


def test_register_plugin_tools_works_for_an_in_process_plugins_intent_too(tmp_path):
    """The SAME registry/handler machinery, for an IN-PROCESS plugin's
    intent (no [runtime] table at all) - proves PluginIntentSpec.handler's
    origin (direct function vs. RPC-backed wrapper) is genuinely opaque to
    register_plugin_tools."""
    plugin_dir = tmp_path / "plugins" / "inproc_tool_plug"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.toml").write_text(
        textwrap.dedent("""\
            [plugin]
            id = "inproc_tool_plug"
            name = "In-Process Tool Plug"
            version = "0.1.0"
            sdk_api_version = 1
            entry_point = "plugin:register"

            [scopes]
            grants = ["graph.mutate"]
        """),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(_INTENT_PY_BODY, encoding="utf-8")

    registry = discover_plugins(tmp_path / "plugins")
    try:
        assert "inproc_tool_plug" not in registry.worker_clients  # no worker for an in-process plugin
        settings_manager = SettingsManager(tmp_path / "store" / "session.dat")
        settings_manager.set_plugin_grant("inproc_tool_plug", True)
        canvas_document = SceneDocument()
        tool_registry = ToolRegistry()
        register_plugin_tools(tool_registry, registry, settings_manager, canvas_document)

        async def request_approval(call):
            return True

        ctx = RunContext(granted_scopes=frozenset({"graph.mutate"}), request_approval=request_approval)
        call = ToolCall(id="1", name="plugin:inproc_tool_plug:do_thing", arguments={})
        result = _run(tool_registry.invoke(call, ctx))

        assert result.content == "did-the-thing:inproc_tool_plug"
    finally:
        _close_workers(registry)


# -- G: backend/agents.py's builder_tool_registry() really wires it in ------


def test_builder_tool_registry_includes_the_real_shipped_sandboxed_demo_plugin_tool(tmp_path):
    from backend.agents import AgentDispatcher

    settings_manager = SettingsManager(tmp_path / "session.dat")
    dispatcher = AgentDispatcher(settings_manager)
    document = SceneDocument()

    registry = dispatcher.builder_tool_registry(document)

    names = [spec.name for spec in registry.specs()]
    assert "plugin:sandboxed_demo:ping" in names
    assert registry.scopes_for("plugin:sandboxed_demo:ping") == frozenset({"graph.mutate"})
