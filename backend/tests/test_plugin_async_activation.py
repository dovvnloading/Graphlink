"""Granted out-of-process workers prepare without blocking live sessions.

Fake workers and threading Events exercise the actual asyncio scheduling,
consent re-check, de-duplication, registration and cleanup paths without
running third-party Python or opening any network connections.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import (
    HostContext, PluginNodeSeed, activate_granted_plugins, discover_plugins,
)
from backend.plugins import register_plugins
from graphlink_settings_store import SettingsManager


def _metadata_only_registry(tmp_path: Path):
    plugins_root = tmp_path / "plugins"
    folder = plugins_root / "deferred_worker"
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(
        '[plugin]\nid = "deferred_worker"\nname = "Deferred Worker"\n'
        'version = "1.0.0"\nsdk_api_version = 1\n'
        'entry_point = "plugin:register"\n\n'
        '[runtime]\nisolation = "out-of-process"\n',
        encoding="utf-8",
    )
    registry = discover_plugins(plugins_root)
    assert "deferred_worker" in registry.manifests
    assert not registry.worker_clients
    return registry


class _FakeWorkerClient:
    def __init__(self):
        self.closed = threading.Event()

    def close(self):
        self.closed.set()


def _install_delayed_preparer(monkeypatch):
    from backend import plugin_sdk

    started = threading.Event()
    release = threading.Event()
    clients: list[_FakeWorkerClient] = []
    invocations: list[str] = []

    def prepare(manifest, plugin_dir, *, on_client_created=None):
        invocations.append(manifest.id)
        started.set()
        release.wait(timeout=5)
        host = HostContext(manifest.id)
        host.register_node_kind(
            "thing",
            lambda *_: PluginNodeSeed(title="Async Thing", content="created"),
        )
        host.register_picker_entry(
            node_kind="thing", name="Deferred Widget",
            description="Initialized asynchronously",
        )
        client = _FakeWorkerClient()
        clients.append(client)
        if on_client_created is not None and not on_client_created(client):
            client.close()
            raise RuntimeError("worker was revoked during preparation")
        return host, client

    monkeypatch.setattr(plugin_sdk, "_discover_out_of_process_plugin", prepare)
    return started, release, clients, invocations


def _bus_and_settings(tmp_path):
    bus = SessionBus("deferred-worker-test")
    document = SceneDocument()
    notifications = NotificationState()
    settings = SettingsManager(tmp_path / "settings" / "session.dat")
    bus.register_topic("scene", document.scene_payload)
    bus.register_topic("notification", notifications.payload)
    return bus, document, notifications, settings


def test_approved_worker_activation_does_not_block_session_start_and_starts_once(
    tmp_path, monkeypatch,
):
    registry = _metadata_only_registry(tmp_path)
    started, release, clients, invocations = _install_delayed_preparer(monkeypatch)
    bus, document, notifications, settings = _bus_and_settings(tmp_path)
    settings.set_plugin_grant("deferred_worker", True)

    async def scenario():
        register_plugins(bus, notifications, document, settings, plugin_registry=registry)
        assert not registry.loaded_plugin_ids
        assert "Deferred Widget" not in registry.picker_entries
        task = registry.pending_worker_activations["deferred_worker"]
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            # Repeated activation attempts must share one pending worker.
            assert activate_granted_plugins(registry, settings) == (task,)
            await asyncio.sleep(0)
            assert not task.done(), "the event loop was blocked by worker startup"
            release.set()
            await asyncio.wait_for(task, timeout=3)
            assert "Deferred Widget" in registry.picker_entries
            assert "deferred_worker" in registry.loaded_plugin_ids
            assert "deferred_worker" in registry.worker_clients
            await asyncio.sleep(0)
        finally:
            release.set()

    asyncio.run(scenario())
    assert invocations == ["deferred_worker"], "a second caller spawned another process"
    assert len(clients) == 1 and not clients[0].closed.is_set()
    clients[0].close()


def test_approval_revoked_during_background_start_drops_worker(tmp_path, monkeypatch):
    registry = _metadata_only_registry(tmp_path)
    started, release, clients, invocations = _install_delayed_preparer(monkeypatch)
    _bus, _doc, _notifications, settings = _bus_and_settings(tmp_path)
    settings.set_plugin_grant("deferred_worker", True)

    async def scenario():
        tasks = activate_granted_plugins(registry, settings)
        assert len(tasks) == 1
        task = tasks[0]
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            settings.set_plugin_grant("deferred_worker", False)
            release.set()
            await asyncio.wait_for(task, timeout=3)
        finally:
            release.set()

    asyncio.run(scenario())
    assert invocations == ["deferred_worker"]
    assert "deferred_worker" not in registry.loaded_plugin_ids
    assert not registry.worker_clients
    assert not registry.picker_entries
    assert clients and clients[0].closed.is_set()


def test_grant_intent_waits_for_worker_off_thread_and_updates_picker(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from backend.session_context import SessionContext, attach_session_context

    registry = _metadata_only_registry(tmp_path)
    started, release, clients, invocations = _install_delayed_preparer(monkeypatch)
    bus, document, notifications, settings = _bus_and_settings(tmp_path)
    dispatcher = Mock()
    attach_session_context(
        bus, SessionContext(agent_dispatcher=dispatcher, canvas_document=document),
    )

    async def scenario():
        register_plugins(bus, notifications, document, settings, plugin_registry=registry)
        assert not registry.pending_worker_activations
        granted = asyncio.create_task(
            bus.dispatch_intent("app-plugins", "setPluginGrant", ["deferred_worker", True])
        )
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            assert not granted.done(), "Settings blocked the event loop on worker startup"
            assert not registry.loaded_plugin_ids
            release.set()
            await asyncio.wait_for(granted, timeout=3)
            assert "Deferred Widget" in registry.picker_entries
            assert "deferred_worker" in registry.worker_clients
        finally:
            release.set()

    asyncio.run(scenario())
    assert invocations == ["deferred_worker"]
    assert dispatcher.invalidate_builder_registry.call_count == 1
    for client in clients:
        client.close()


def test_background_activation_invalidates_builder_registry_after_registration(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from backend.session_context import SessionContext, attach_session_context

    registry = _metadata_only_registry(tmp_path)
    started, release, clients, _invocations = _install_delayed_preparer(monkeypatch)
    bus, document, notifications, settings = _bus_and_settings(tmp_path)
    settings.set_plugin_grant("deferred_worker", True)
    dispatcher = Mock()
    attach_session_context(
        bus,
        SessionContext(agent_dispatcher=dispatcher, canvas_document=document),
    )

    async def scenario():
        register_plugins(bus, notifications, document, settings, plugin_registry=registry)
        task = registry.pending_worker_activations["deferred_worker"]
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            assert dispatcher.invalidate_builder_registry.call_count == 0
            release.set()
            await asyncio.wait_for(task, timeout=3)
            # _finish_background_activation is separate from the worker
            # task; give the event loop a chance to publish UI readiness.
            for _ in range(50):
                if dispatcher.invalidate_builder_registry.called:
                    break
                await asyncio.sleep(0.01)
            assert dispatcher.invalidate_builder_registry.call_count == 1
        finally:
            release.set()

    asyncio.run(scenario())
    for client in clients:
        client.close()


def test_cancelled_activation_closes_worker_returned_by_still_running_thread(tmp_path, monkeypatch):
    registry = _metadata_only_registry(tmp_path)
    started, release, clients, _invocations = _install_delayed_preparer(monkeypatch)
    _bus, _doc, _notifications, settings = _bus_and_settings(tmp_path)
    settings.set_plugin_grant("deferred_worker", True)

    async def scenario():
        task = activate_granted_plugins(registry, settings)[0]
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            task.cancel()
            try:
                await task
                assert False, "cancelled activation must raise CancelledError"
            except asyncio.CancelledError:
                pass
            release.set()
            for _ in range(200):
                if clients and clients[0].closed.is_set():
                    break
                await asyncio.sleep(0.01)
            assert clients and clients[0].closed.is_set(), (
                "cancelled to_thread preparation must close its returned worker"
            )
            assert not registry.worker_clients
        finally:
            release.set()

    asyncio.run(scenario())


def test_load_waits_for_plugin_registration_before_restoring_saved_node(tmp_path, monkeypatch):
    """A background worker must not make persisted plugin nodes disappear."""
    from backend import chat_library, plugin_sdk, session_load, session_save

    registry = _metadata_only_registry(tmp_path)
    started, release, clients, _invocations = _install_delayed_preparer(monkeypatch)
    bus, document, notifications, settings = _bus_and_settings(tmp_path)
    settings.set_plugin_grant("deferred_worker", True)

    # The real restoration pathway uses the shared, metadata-only plugin
    # registry. Patch the factory to that same registry, not to a second
    # fixture path with a different activation state.
    monkeypatch.setattr(plugin_sdk, "discover_plugins", lambda: registry)
    monkeypatch.setattr(session_load, "discover_plugins", lambda: registry)
    monkeypatch.setattr(session_save, "discover_plugins", lambda: registry)
    monkeypatch.setattr(chat_library, "store_for", lambda *_args: None)

    row = {
        "id": 41,
        "title": "Persisted plugin graph",
        "workspace_id": 1,
        "updated_at": "2026-11-13T00:00:00",
        "data": {
            "nodes": [{
                "node_type": "deferred_worker.thing",
                "id": "older-plugin-node",
                "title": "Do not lose this node",
                "content": "irreplaceable graph content",
                "position": {"x": 10, "y": 20},
            }],
            "edges": [],
        },
    }
    monkeypatch.setattr(
        chat_library, "load_chat_snapshot",
        lambda *_args: (row, [], []),
    )

    async def scenario():
        register_plugins(bus, notifications, document, settings, plugin_registry=registry)
        loader = chat_library.make_load_chat(
            bus, tmp_path / "chat.db", document, notifications,
            record_saved=lambda *_args: None,
            last_saved={},
            settings_manager=settings,
        )
        task = asyncio.create_task(loader(41))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            assert not task.done(), "load must wait for registered plugin kinds"
            assert not document.nodes, "a partial restore must not run while startup is pending"
            release.set()
            await asyncio.wait_for(task, timeout=3)
            assert [
                (node.kind, node.title, node.content)
                for node in document.nodes.values()
            ] == [(
                "deferred_worker.thing",
                "Do not lose this node",
                "irreplaceable graph content",
            )]
            assert document.current_chat_id == 41
            assert notifications.msg_type == "success"
        finally:
            release.set()

    asyncio.run(scenario())
    for client in clients:
        client.close()


def test_revoking_a_pending_worker_cancels_registration_and_cleans_up(tmp_path, monkeypatch):
    """A cancelled worker startup must never resurrect a revoked plugin."""
    from backend.plugin_sdk import deactivate_plugin_worker

    registry = _metadata_only_registry(tmp_path)
    started, release, clients, _invocations = _install_delayed_preparer(monkeypatch)
    _bus, _doc, _notifications, settings = _bus_and_settings(tmp_path)
    settings.set_plugin_grant("deferred_worker", True)

    async def scenario():
        task = activate_granted_plugins(registry, settings)[0]
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            settings.set_plugin_grant("deferred_worker", False)
            await asyncio.to_thread(deactivate_plugin_worker, registry, "deferred_worker")
            try:
                await asyncio.wait_for(task, timeout=3)
                assert False, "revocation must cancel the pending activation task"
            except asyncio.CancelledError:
                pass
            release.set()
            for _ in range(200):
                if clients and clients[0].closed.is_set():
                    break
                await asyncio.sleep(0.01)
            assert clients and clients[0].closed.is_set()
            assert "deferred_worker" not in registry.loaded_plugin_ids
            assert "deferred_worker" not in registry.worker_clients
            assert "Deferred Widget" not in registry.picker_entries
        finally:
            release.set()

    asyncio.run(scenario())



def test_cross_session_regrant_waits_for_previous_worker_teardown(tmp_path, monkeypatch):
    """Session B may not start worker #2 until A's worker #1 fully exits."""
    registry = _metadata_only_registry(tmp_path)
    _started, release, clients, invocations = _install_delayed_preparer(monkeypatch)
    bus_a, doc_a, notifications_a, settings = _bus_and_settings(tmp_path)
    bus_b, doc_b, notifications_b, _settings_b = _bus_and_settings(tmp_path)
    register_plugins(bus_a, notifications_a, doc_a, settings, plugin_registry=registry)
    register_plugins(bus_b, notifications_b, doc_b, settings, plugin_registry=registry)
    release.set()

    teardown_started = threading.Event()
    finish_teardown = threading.Event()

    async def scenario():
        await bus_a.dispatch_intent("app-plugins", "setPluginGrant", ["deferred_worker", True])
        first = clients[0]
        close_original = first.close

        def slow_close():
            teardown_started.set()
            assert finish_teardown.wait(timeout=10), "test teardown was never released"
            close_original()

        first.close = slow_close
        revoke = asyncio.create_task(
            bus_a.dispatch_intent("app-plugins", "setPluginGrant", ["deferred_worker", False])
        )
        try:
            assert await asyncio.wait_for(
                asyncio.to_thread(teardown_started.wait, 5), timeout=6,
            )
            assert "deferred_worker" in registry.draining_worker_ids
            assert registry.draining_worker_events["deferred_worker"].is_set() is False

            # Another session reapproves while the first OS teardown blocks.
            approval = asyncio.create_task(
                bus_b.dispatch_intent("app-plugins", "setPluginGrant", ["deferred_worker", True])
            )
            await asyncio.sleep(0.1)
            assert not approval.done(), "approval must wait for old worker to finish"
            assert invocations == ["deferred_worker"], "a duplicate worker started during draining"
        finally:
            finish_teardown.set()
        await asyncio.wait_for(revoke, timeout=10)
        await asyncio.wait_for(approval, timeout=10)
        assert invocations == ["deferred_worker", "deferred_worker"]
        assert registry.loaded_plugin_ids == {"deferred_worker"}
        assert len(registry.worker_clients) == 1
        assert registry.worker_clients["deferred_worker"] is clients[1]
        assert "Deferred Widget" in registry.picker_entries
        assert "deferred_worker" not in registry.draining_worker_ids
        assert not registry.draining_worker_events

    try:
        asyncio.run(scenario())
    finally:
        finish_teardown.set()
        for client in clients:
            client.close()
