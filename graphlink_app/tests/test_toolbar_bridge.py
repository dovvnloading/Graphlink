"""Contract tests for the toolbar island bridge (Phase 6 increment 1) -
absorbs graphlink_window.py's native QToolBar/setup_toolbar() (14 intents).

Every intent Slot is a pure pass-through to a fake window's own method -
nothing about behavior is tested here beyond "the right method got called
with the right args," matching every prior island's own "faithful port"
verification shape. AnchorRect's own duck-typed API (mapToGlobal/mapTo/
width/height/size) is the one genuinely new mechanism, tested directly.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QPoint, QSize

from graphlink_toolbar_bridge import MODE_OPTIONS, AnchorRect, ToolbarBridge


class _FakePinOverlay:
    def __init__(self, visible=False):
        self._visible = visible

    def isVisible(self):
        return self._visible


class _FakeSettingsManager:
    def __init__(self, current_mode="Ollama (Local)"):
        self._current_mode = current_mode

    def get_current_mode(self):
        return self._current_mode


class _FakeScene:
    def __init__(self):
        self.organized = False

    def organize_nodes(self):
        self.organized = True


class _FakeChatView:
    def __init__(self):
        self._scene = _FakeScene()
        self.zoom_calls = []
        self.reset_calls = 0
        self.fit_calls = 0

    def scene(self):
        return self._scene

    def zoom_by(self, factor):
        self.zoom_calls.append(factor)

    def reset_zoom(self):
        self.reset_calls += 1

    def fit_all(self):
        self.fit_calls += 1

    def toggle_overlays_visibility(self, visible):
        self.overlays_visible = visible


class _FakeWindow:
    def __init__(self):
        self.pin_overlay = _FakePinOverlay()
        self.settings_manager = _FakeSettingsManager()
        self.chat_view = _FakeChatView()
        self.calls = []

    def show_library(self):
        self.calls.append("show_library")

    def save_chat(self):
        self.calls.append("save_chat")

    def toggle_pin_overlay(self):
        self.pin_overlay._visible = not self.pin_overlay._visible
        self.calls.append("toggle_pin_overlay")

    def _toggle_plugin_picker(self):
        self.calls.append("_toggle_plugin_picker")

    def on_mode_changed(self, mode_text):
        self.calls.append(("on_mode_changed", mode_text))

    def show_settings(self):
        self.calls.append("show_settings")

    def show_about_dialog(self):
        self.calls.append("show_about_dialog")

    def show_help(self):
        self.calls.append("show_help")


class _RealQObjectHost(QObject):
    def __init__(self):
        super().__init__()
        self.moved_to = None

    def mapToGlobal(self, point):
        return QPoint(point.x() + 100, point.y() + 200)


def _snapshot(bridge: ToolbarBridge) -> dict:
    states = []
    bridge.stateChanged.connect(states.append)
    bridge.ready()
    return json.loads(states[-1])


def test_ready_publishes_pins_checked_mode_options_and_current_mode():
    window = _FakeWindow()
    window.pin_overlay._visible = True
    bridge = ToolbarBridge(window)

    payload = _snapshot(bridge)

    assert payload["pinsChecked"] is True
    assert payload["modeOptions"] == MODE_OPTIONS
    assert payload["currentMode"] == "Ollama (Local)"


def test_pins_checked_reads_pin_overlay_visibility_live_each_publish():
    window = _FakeWindow()
    bridge = ToolbarBridge(window)
    assert _snapshot(bridge)["pinsChecked"] is False

    window.pin_overlay._visible = True

    assert _snapshot(bridge)["pinsChecked"] is True


def test_simple_intents_forward_to_the_window():
    window = _FakeWindow()
    bridge = ToolbarBridge(window)

    bridge.openLibrary()
    bridge.saveChat()
    bridge.togglePlugins()
    bridge.selectMode("API Endpoint")
    bridge.openSettings()
    bridge.openAbout()
    bridge.openHelp()

    assert window.calls == [
        "show_library",
        "save_chat",
        "_toggle_plugin_picker",
        ("on_mode_changed", "API Endpoint"),
        "show_settings",
        "show_about_dialog",
        "show_help",
    ]


def test_toggle_pins_calls_window_and_republishes():
    window = _FakeWindow()
    bridge = ToolbarBridge(window)
    states = []
    bridge.stateChanged.connect(states.append)

    bridge.togglePins()

    assert window.calls == ["toggle_pin_overlay"]
    assert json.loads(states[-1])["pinsChecked"] is True


def test_organize_zoom_reset_fit_all_reach_the_real_chat_view():
    window = _FakeWindow()
    bridge = ToolbarBridge(window)

    bridge.organizeNodes()
    bridge.zoomIn()
    bridge.zoomOut()
    bridge.resetZoom()
    bridge.fitAll()

    assert window.chat_view.scene().organized is True
    assert window.chat_view.zoom_calls == [1.1, 0.9]
    assert window.chat_view.reset_calls == 1
    assert window.chat_view.fit_calls == 1


def test_toggle_controls_forwards_the_bool_to_chat_view():
    window = _FakeWindow()
    bridge = ToolbarBridge(window)

    bridge.toggleControls(True)

    assert window.chat_view.overlays_visible is True


class TestAnchorRect:
    def test_duck_types_the_qwidget_subset_show_for_anchor_needs(self):
        anchor = AnchorRect(QPoint(500, 300), QSize(80, 24))

        assert anchor.width() == 80
        assert anchor.height() == 24
        assert anchor.size() == QSize(80, 24)
        assert anchor.mapToGlobal(QPoint(0, 0)) == QPoint(500, 300)
        assert anchor.mapToGlobal(QPoint(10, 5)) == QPoint(510, 305)

    def test_map_to_composes_through_a_real_target_widget(self):
        anchor = AnchorRect(QPoint(500, 300), QSize(80, 24))

        class _FakeTarget:
            def mapFromGlobal(self, point):
                return QPoint(point.x() - 500, point.y() - 300)

        result = anchor.mapTo(_FakeTarget(), QPoint(10, 5))

        assert result == QPoint(10, 5)


class TestReportAnchorRect:
    def test_stores_a_real_screen_position_composed_through_the_host(self):
        host = _RealQObjectHost()
        window = _FakeWindow()
        bridge = ToolbarBridge(window)
        bridge.setParent(host)

        bridge.reportAnchorRect("pins", 10, 20, 60, 30)
        anchor = bridge.get_anchor("pins")

        assert anchor.mapToGlobal(QPoint(0, 0)) == QPoint(110, 220)
        assert anchor.width() == 60
        assert anchor.height() == 30

    def test_get_anchor_falls_back_to_the_window_when_never_reported(self):
        window = _FakeWindow()
        bridge = ToolbarBridge(window)

        assert bridge.get_anchor("settings") is window

    def test_report_anchor_rect_without_a_parent_host_does_not_raise(self):
        window = _FakeWindow()
        bridge = ToolbarBridge(window)

        bridge.reportAnchorRect("help", 1, 2, 3, 4)  # must not raise

        assert bridge.get_anchor("help") is window
