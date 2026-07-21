"""Contract tests for the plugin-picker island bridge (Phase 6 increment 3)
- absorbs PluginFlyoutPanel (native Qt.WindowType.Popup, deleted this
increment).

Wraps a FAKE plugin portal exposing exactly the surface the real
PluginPortal already has (get_plugin_categories()/execute_plugin()) -
nothing about plugin discovery or execution itself is under test here, only
that this bridge forwards to it correctly and strips the non-serializable
`callback`/`category`/`icon` fields, exactly as PluginFlyoutPanel's own
_build_category_buttons()/set_current_category() used to read the same
dicts directly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject

from graphlink_plugin_picker_bridge import (
    PLUGIN_PICKER_MAX_HEIGHT,
    PLUGIN_PICKER_MIN_HEIGHT,
    PluginPickerBridge,
)


class _FakeHost(QObject):
    def __init__(self):
        super().__init__()
        self.visible = True

    def setVisible(self, visible):
        self.visible = visible


class _FakePluginPortal:
    def __init__(self, categories=None):
        self._categories = categories if categories is not None else []
        self.executed = []

    def get_plugin_categories(self):
        return self._categories

    def execute_plugin(self, plugin_name):
        self.executed.append(plugin_name)


_CATEGORIES = [
    {
        "name": "Branch Foundations",
        "description": "Core branch scaffolding.",
        "icon": "fa5s.layer-group",
        "plugins": [
            {
                "name": "System Prompt",
                "description": "Adds a special node to override the default system prompt.",
                "callback": lambda: None,
                "category": "Branch Foundations",
                "icon": "fa5s.sliders-h",
            },
        ],
    },
    {
        "name": "Build & Execution",
        "description": "Code generation and execution tools.",
        "icon": "fa5s.code",
        "plugins": [
            {
                "name": "Py-Coder",
                "description": "Opens a Python execution environment.",
                "callback": lambda: None,
                "category": "Build & Execution",
                "icon": "fa5s.laptop-code",
            },
        ],
    },
]


def _snapshot(bridge: PluginPickerBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    import json

    return json.loads(states[-1])


def test_ready_publishes_the_current_categories():
    portal = _FakePluginPortal(_CATEGORIES)
    bridge = PluginPickerBridge(portal)

    payload = _snapshot(bridge)

    assert [category["name"] for category in payload["categories"]] == [
        "Branch Foundations",
        "Build & Execution",
    ]


def test_categories_strip_callback_and_only_keep_name_and_description():
    portal = _FakePluginPortal(_CATEGORIES)
    bridge = PluginPickerBridge(portal)

    payload = _snapshot(bridge)

    plugin = payload["categories"][0]["plugins"][0]
    assert set(plugin.keys()) == {"name", "description"}
    assert plugin["name"] == "System Prompt"


def test_category_dicts_only_keep_name_and_description_and_plugins():
    portal = _FakePluginPortal(_CATEGORIES)
    bridge = PluginPickerBridge(portal)

    payload = _snapshot(bridge)

    assert set(payload["categories"][0].keys()) == {"name", "description", "plugins"}


def test_empty_categories_publishes_an_empty_list():
    portal = _FakePluginPortal([])
    bridge = PluginPickerBridge(portal)

    payload = _snapshot(bridge)

    assert payload["categories"] == []


def test_execute_plugin_dispatches_to_the_real_plugin_portal():
    portal = _FakePluginPortal(_CATEGORIES)
    bridge = PluginPickerBridge(portal)

    bridge.executePlugin("Py-Coder")

    assert portal.executed == ["Py-Coder"]


def test_execute_plugin_with_a_blank_name_does_not_call_execute_plugin():
    portal = _FakePluginPortal(_CATEGORIES)
    bridge = PluginPickerBridge(portal)

    bridge.executePlugin("   ")

    assert portal.executed == []


def test_execute_plugin_always_hides_the_host_via_close():
    portal = _FakePluginPortal(_CATEGORIES)
    bridge = PluginPickerBridge(portal)
    host = _FakeHost()
    bridge.setParent(host)

    bridge.executePlugin("Py-Coder")

    assert host.visible is False


def test_execute_plugin_with_a_blank_name_still_closes_the_host():
    portal = _FakePluginPortal(_CATEGORIES)
    bridge = PluginPickerBridge(portal)
    host = _FakeHost()
    bridge.setParent(host)

    bridge.executePlugin("")

    assert host.visible is False


def test_resize_bounds_to_min_and_max_height():
    bridge = PluginPickerBridge(_FakePluginPortal())
    heights = []
    bridge.heightRequested.connect(heights.append)

    bridge.resize(1)
    bridge.resize(9999)

    assert heights == [PLUGIN_PICKER_MIN_HEIGHT, PLUGIN_PICKER_MAX_HEIGHT]


def test_close_hides_the_host():
    bridge = PluginPickerBridge(_FakePluginPortal())
    host = _FakeHost()
    bridge.setParent(host)

    bridge.close()

    assert host.visible is False
