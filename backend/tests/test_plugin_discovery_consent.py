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
