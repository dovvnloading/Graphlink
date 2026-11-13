"""Security regression tests: plugin consent must precede executable loading.

Reading plugin.toml is inert. Neither an in-process import nor an isolated
worker's startup may execute user-installed code before a persisted grant.
SDK tests that need eager fixture loading opt in explicitly elsewhere.
"""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import activate_granted_plugins, discover_plugins
from backend.plugins import plugins_payload, register_plugins
from graphlink_settings_store import SettingsManager


def _write_extension(root: Path, name: str, *, out_of_process: bool = False) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    isolation = '[runtime]\nisolation = "out-of-process"\n' if out_of_process else ""
    (plugin_dir / "plugin.toml").write_text(
        textwrap.dedent(
            f"""\
            [plugin]
            id = "{name}"
            name = "Consent Test Extension"
            version = "1.0.0"
            sdk_api_version = 1
            entry_point = "plugin:register"

            [scopes]
            grants = ["graph.mutate"]

            {isolation}
            """
        ),
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(
        textwrap.dedent(
            """\
            from pathlib import Path
            from backend.plugin_sdk import PluginNodeSeed

            marker = Path(__file__).with_name("executed.log")
            with marker.open("a", encoding="utf-8") as log:
                log.write("imported\\n")

            def _make(document, run_ctx, parent_id):
                return PluginNodeSeed(title="After consent", content="safe")

            def register(host):
                host.register_node_kind("thing", _make, requires_parent=True)
                host.register_picker_entry(
                    node_kind="thing", name="Consent Widget",
                    description="Available after approval", category="More Plugins",
                )
            """
        ),
        encoding="utf-8",
    )
    return plugin_dir / "executed.log"


def test_unapproved_in_process_extension_is_metadata_only_until_grant(tmp_path):
    root = tmp_path / "plugins"
    marker = _write_extension(root, "consent_widget")
    settings = SettingsManager(tmp_path / "settings" / "session.dat")

    registry = discover_plugins(root)
    assert "consent_widget" in registry.manifests
    assert "consent_widget" not in registry.loaded_plugin_ids
    assert not registry.picker_entries
    assert not marker.exists(), "metadata discovery imported unapproved Python"

    grants = plugins_payload(registry, settings)["grants"]
    assert len(grants) == 1
    assert grants[0]["pluginId"] == "consent_widget"
    assert grants[0]["granted"] is False
    assert grants[0]["scopes"] == ["graph.mutate"]

    activate_granted_plugins(registry, settings)
    assert not marker.exists(), "activation without consent executed code"

    settings.set_plugin_grant("consent_widget", True)
    activate_granted_plugins(registry, settings)
    assert marker.read_text(encoding="utf-8") == "imported\n"
    assert "Consent Widget" in registry.picker_entries

    activate_granted_plugins(registry, settings)
    assert marker.read_text(encoding="utf-8") == "imported\n"
    assert discover_plugins(root) is registry


def test_grant_intent_loads_extension_and_updates_picker_without_restart(tmp_path):
    root = tmp_path / "plugins"
    marker = _write_extension(root, "consent_during_session")
    settings = SettingsManager(tmp_path / "settings" / "session.dat")
    registry = discover_plugins(root)

    bus = SessionBus("security-consent-test")
    document = SceneDocument()
    notifications = NotificationState()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    register_plugins(bus, notifications, document, settings, plugin_registry=registry)
    assert not marker.exists()

    asyncio.run(
        bus.dispatch_intent(
            "app-plugins", "setPluginGrant", ["consent_during_session", True],
        )
    )
    assert settings.get_plugin_grants()["consent_during_session"] is True
    assert marker.read_text(encoding="utf-8") == "imported\n"
    assert "Consent Widget" in registry.picker_entries
    assert any(
        row["name"] == "Consent Widget"
        for category in plugins_payload(registry, settings)["categories"]
        for row in category["plugins"]
    )


def test_unapproved_out_of_process_extension_does_not_start_worker(tmp_path):
    root = tmp_path / "plugins"
    marker = _write_extension(root, "consent_worker", out_of_process=True)
    settings = SettingsManager(tmp_path / "settings" / "session.dat")
    registry = discover_plugins(root)

    assert "consent_worker" in registry.manifests
    assert not registry.worker_clients
    assert not marker.exists(), "worker ran before consent"

    activate_granted_plugins(registry, settings)
    assert not registry.worker_clients
    assert not marker.exists()

    settings.set_plugin_grant("consent_worker", True)
    try:
        activate_granted_plugins(registry, settings)
        assert "consent_worker" in registry.worker_clients
        assert marker.read_text(encoding="utf-8") == "imported\n"
        assert "Consent Widget" in registry.picker_entries
    finally:
        for worker in registry.worker_clients.values():
            worker.close()

def test_granting_a_plugin_keeps_the_session_loop_responsive(tmp_path, monkeypatch):
    """A slow consented worker registration must not block unrelated intents."""
    import threading

    import backend.plugin_sdk as plugin_sdk_module

    root = tmp_path / "plugins"
    marker = _write_extension(root, "async_consent_worker", out_of_process=True)
    settings = SettingsManager(tmp_path / "settings" / "session.dat")
    registry = discover_plugins(root)
    bus = SessionBus("async-activation-test")
    document = SceneDocument()
    notifications = NotificationState()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    register_plugins(bus, notifications, document, settings, plugin_registry=registry)

    started = threading.Event()
    proceed = threading.Event()
    original_prepare = plugin_sdk_module._discover_out_of_process_plugin

    def delayed_preparation(manifest, plugin_dir, *, on_client_created=None):
        started.set()
        if not proceed.wait(timeout=5):
            raise AssertionError("test setup did not release worker activation")
        return original_prepare(manifest, plugin_dir, on_client_created=on_client_created)

    monkeypatch.setattr(plugin_sdk_module, "_discover_out_of_process_plugin", delayed_preparation)

    async def ping():
        return "other intent still responsive"

    bus.register_intent("notification", "ping", ping)

    async def run():
        grant = asyncio.create_task(
            bus.dispatch_intent(
                "app-plugins", "setPluginGrant", ["async_consent_worker", True],
            )
        )
        try:
            # The worker's activation runs in a background thread, while
            # another WebSocket intent remains immediately dispatchable.
            assert await asyncio.wait_for(
                asyncio.to_thread(started.wait, 2), timeout=3,
            )
            assert not grant.done()
            assert await asyncio.wait_for(
                bus.dispatch_intent("notification", "ping", []), timeout=1,
            ) == "other intent still responsive"
        finally:
            proceed.set()
        await asyncio.wait_for(grant, timeout=10)

    try:
        asyncio.run(run())
        assert registry.loaded_plugin_ids == {"async_consent_worker"}
        assert marker.read_text(encoding="utf-8") == "imported\n"
        assert "Consent Widget" in registry.picker_entries
    finally:
        proceed.set()
        for worker in registry.worker_clients.values():
            worker.close()


def test_parallel_grant_revoke_intents_preserve_final_consent(tmp_path, monkeypatch):
    """Serializing grants prevents late async registration from racing revoke."""
    import threading

    import backend.plugin_sdk as plugin_sdk_module

    root = tmp_path / "plugins"
    _write_extension(root, "ordered_consent", out_of_process=True)
    settings = SettingsManager(tmp_path / "settings" / "session.dat")
    registry = discover_plugins(root)
    bus = SessionBus("ordered-activation-test")
    document = SceneDocument()
    notifications = NotificationState()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    register_plugins(bus, notifications, document, settings, plugin_registry=registry)

    started = threading.Event()
    proceed = threading.Event()
    original_prepare = plugin_sdk_module._discover_out_of_process_plugin

    def delayed_preparation(manifest, plugin_dir, *, on_client_created=None):
        started.set()
        if not proceed.wait(timeout=5):
            raise AssertionError("timed out waiting for release")
        return original_prepare(manifest, plugin_dir, on_client_created=on_client_created)

    monkeypatch.setattr(plugin_sdk_module, "_discover_out_of_process_plugin", delayed_preparation)

    async def run():
        grant = asyncio.create_task(
            bus.dispatch_intent("app-plugins", "setPluginGrant", ["ordered_consent", True]),
        )
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            revoke = asyncio.create_task(
                bus.dispatch_intent("app-plugins", "setPluginGrant", ["ordered_consent", False]),
            )
            # A same-session revoke must now PREEMPT the slow approval,
            # without waiting for its to_thread registration RPC to finish.
            await asyncio.wait_for(revoke, timeout=3)
            assert settings.get_plugin_grants()["ordered_consent"] is False
            assert "ordered_consent" not in registry.loaded_plugin_ids
        finally:
            proceed.set()
        await asyncio.wait_for(grant, timeout=10)

    try:
        asyncio.run(run())
        assert settings.get_plugin_grants()["ordered_consent"] is False
    finally:
        proceed.set()
        for worker in registry.worker_clients.values():
            worker.close()

def test_revoking_out_of_process_plugin_terminates_worker_and_allows_fresh_regrant(tmp_path):
    """Disabling a resident worker must revoke its OS process, not just its UI grant."""
    root = tmp_path / "plugins"
    marker = _write_extension(root, "revoked_worker", out_of_process=True)
    settings = SettingsManager(tmp_path / "settings" / "session.dat")
    registry = discover_plugins(root)
    bus = SessionBus("revoke-runtime-test")
    document = SceneDocument()
    notifications = NotificationState()
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    register_plugins(bus, notifications, document, settings, plugin_registry=registry)

    async def run():
        await bus.dispatch_intent(
            "app-plugins", "setPluginGrant", ["revoked_worker", True],
        )
        worker = registry.worker_clients["revoked_worker"]
        running_process = worker._process
        assert running_process is not None
        assert running_process.poll() is None
        assert "Consent Widget" in registry.picker_entries

        await bus.dispatch_intent(
            "app-plugins", "setPluginGrant", ["revoked_worker", False],
        )
        assert not worker.is_connected
        assert running_process.poll() is not None, "revoked OS worker remained alive"
        assert "revoked_worker" not in registry.worker_clients
        assert "revoked_worker" not in registry.loaded_plugin_ids
        assert "Consent Widget" not in registry.picker_entries
        assert "revoked_worker.thing" not in registry.node_kinds
        assert "revoked_worker" in registry.manifests
        assert not settings.get_plugin_grants()["revoked_worker"]
        grants = plugins_payload(registry, settings)["grants"]
        assert any(r["pluginId"] == "revoked_worker" and not r["granted"] for r in grants)

        # Revocation preserves the metadata, but not the executable
        # registrations. Re-approval must spawn exactly one fresh worker.
        await bus.dispatch_intent(
            "app-plugins", "setPluginGrant", ["revoked_worker", True],
        )
        restarted = registry.worker_clients["revoked_worker"]
        assert restarted is not worker
        assert restarted.is_connected
        assert "Consent Widget" in registry.picker_entries
        assert registry.loaded_plugin_ids == {"revoked_worker"}
        assert marker.read_text(encoding="utf-8") == "imported\nimported\n"

    try:
        asyncio.run(run())
    finally:
        for worker in registry.worker_clients.values():
            worker.close()


def test_revoking_in_process_plugin_does_not_claim_to_unload_python(tmp_path):
    """Import-time Python cannot be reversed; only runtime entry points are gated."""
    from backend.plugin_sdk import deactivate_plugin_worker

    root = tmp_path / "plugins"
    marker = _write_extension(root, "in_process_revoke", out_of_process=False)
    settings = SettingsManager(tmp_path / "settings" / "session.dat")
    registry = discover_plugins(root)
    settings.set_plugin_grant("in_process_revoke", True)
    activate_granted_plugins(registry, settings)
    assert marker.read_text(encoding="utf-8") == "imported\n"

    settings.set_plugin_grant("in_process_revoke", False)
    assert deactivate_plugin_worker(registry, "in_process_revoke") == ()
    assert "in_process_revoke" in registry.loaded_plugin_ids
    assert "Consent Widget" in registry.picker_entries
