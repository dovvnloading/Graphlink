"""Regression tests: a slow consented plugin worker must not block the ASGI loop.

Only synthetic, bounded worker responses are used. These tests also exercise
the new await boundary's grant/parent validity checks before scene mutation.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import (
    NodeKindSpec, PickerEntrySpec, PluginIntentSpec, PluginRegistry,
)
from backend.plugins import (
    _execute_discovered_plugin,
    _invoke_discovered_plugin_intent,
    _make_plugin_tool_handler,
)
from backend.providers.base import ToolCall
from backend.tools import RunContext
from graphlink_settings_store import SettingsManager


class _DelayedWorker:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.requests: list[tuple[str, dict]] = []

    def call(self, method: str, params: dict) -> dict:
        self.requests.append((method, params))
        self.started.set()
        self.release.wait(timeout=3)
        if method == "invoke_factory":
            return {"title": "From Worker", "content": "safe output", "state": {"value": "one"}}
        return {"result": "worker action finished"}


def _setup(tmp_path):
    registry = PluginRegistry()
    worker = _DelayedWorker()
    registry.worker_clients["fake"] = worker  # synthetic transport

    def should_never_run_in_process(*_args):
        pytest.fail("out-of-process factory must not run synchronously on the loop")

    registry.node_kinds["fake.thing"] = NodeKindSpec(
        plugin_id="fake", kind="fake.thing", factory=should_never_run_in_process,
        requires_parent=True,
    )
    registry.picker_entries["Fake Picker"] = PickerEntrySpec(
        plugin_id="fake", name="Fake Picker", description="d",
        category="More Plugins", node_kind="fake.thing",
    )

    def worker_intent(_document, _ctx):
        return worker.call("invoke_intent", {"name": "answer", "args": {}})["result"]

    registry.intents.append(PluginIntentSpec(plugin_id="fake", name="answer", handler=worker_intent))
    settings = SettingsManager(tmp_path / "settings" / "session.dat")
    settings.set_plugin_grant("fake", True)
    document = SceneDocument()
    parent = document.add_node(10, 20, "parent")
    notifications = NotificationState()
    bus = SessionBus("async-plugin-worker-test")
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", document.scene_payload)
    return registry, worker, settings, document, parent, notifications, bus


def _factory_call(setup):
    registry, worker, settings, document, parent, notifications, bus = setup
    return _execute_discovered_plugin(
        bus, notifications, document, registry, settings, "Fake Picker", parent.id,
    )


def test_slow_worker_factory_does_not_block_event_loop_and_still_creates_node(tmp_path):
    setup = _setup(tmp_path)
    registry, worker, _settings, document, parent, _notifications, _bus = setup

    async def scenario():
        task = asyncio.create_task(_factory_call(setup))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(worker.started.wait, 2), timeout=3)
            assert not task.done(), "the event loop was blocked until the worker completed"
            assert len(document.nodes) == 1
            worker.release.set()
            node_id = await asyncio.wait_for(task, timeout=3)
            assert node_id is not None
            assert document.nodes[node_id].content == "safe output"
            assert any(
                edge.source == parent.id and edge.target == node_id
                for edge in document.edges.values()
            )
            assert registry.node_kinds["fake.thing"].factory is not None
        finally:
            worker.release.set()

    asyncio.run(scenario())
    assert worker.requests[0][0] == "invoke_factory"
    assert worker.requests[0][1]["parent_snapshot"]["id"] == parent.id


@pytest.mark.parametrize("change", ["edit-parent", "remove-parent", "revoke-grant", "switch-graph"])
def test_worker_reply_is_dropped_after_parent_or_consent_change(tmp_path, change):
    setup = _setup(tmp_path)
    _registry, worker, settings, document, parent, notifications, _bus = setup

    async def scenario():
        task = asyncio.create_task(_factory_call(setup))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(worker.started.wait, 2), timeout=3)
            assert not task.done()
            if change == "edit-parent":
                parent.title = "edited while worker was busy"
            elif change == "remove-parent":
                document.nodes.pop(parent.id)
            elif change == "revoke-grant":
                settings.set_plugin_grant("fake", False)
            else:
                document.current_workspace_id = 98765
            worker.release.set()
            result = await asyncio.wait_for(task, timeout=3)
            assert result is None
            assert "discarded" in notifications.message
            assert all(node.kind != "fake.thing" for node in document.nodes.values())
        finally:
            worker.release.set()

    asyncio.run(scenario())


def test_slow_worker_intent_does_not_block_bus_or_builder_tool(tmp_path):
    setup = _setup(tmp_path)
    registry, worker, settings, document, _parent, notifications, bus = setup

    async def check(operation):
        worker.started.clear()
        worker.release.clear()
        task = asyncio.create_task(operation())
        try:
            assert await asyncio.wait_for(asyncio.to_thread(worker.started.wait, 2), timeout=3)
            assert not task.done(), "plugin intent blocked the event loop"
            worker.release.set()
            return await asyncio.wait_for(task, timeout=3)
        finally:
            worker.release.set()

    async def scenario():
        result = await check(lambda: _invoke_discovered_plugin_intent(
            bus, notifications, document, registry, settings, "fake", "answer",
        ))
        assert result == "worker action finished"

        handler = _make_plugin_tool_handler(registry, settings, document, "fake", "answer")
        result = await check(lambda: handler(
            ToolCall(id="test", name="plugin:fake:answer", arguments={}),
            RunContext(granted_scopes=frozenset()),
        ))
        assert result.is_error is False
        assert "worker action finished" in result.content

    asyncio.run(scenario())
