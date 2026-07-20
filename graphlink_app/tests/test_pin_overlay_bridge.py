"""Contract tests for the pin-overlay island bridge (Phase 5 increment 1)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog

import graphlink_pin_overlay_bridge as bridge_module
from graphlink_navigation_pins import NavigationPinRecord, NavigationPinStore
from graphlink_pin_overlay_bridge import PinOverlayBridge


class _FakePin:
    def __init__(self, pin_id, title, note=""):
        self.pin_id = pin_id
        self.title = title
        self.note = note
        self._scene = None

    def scene(self):
        return self._scene


class _FakeScene(QObject):
    selectionChanged = Signal()

    def __init__(self):
        super().__init__()
        self.pin_store = NavigationPinStore()
        self._pins_by_id = {}
        self._selected = []

    def add_pin(self, pin_id, title, note=""):
        self.pin_store.add(pin_id=pin_id, title=title, note=note, x=0.0, y=0.0)
        pin = _FakePin(pin_id, title, note)
        pin._scene = self
        self._pins_by_id[pin_id] = pin
        return pin

    def selectedItems(self):
        return self._selected

    def _navigation_pin_item(self, pin_id):
        return self._pins_by_id.get(pin_id)


class _FakeChatView:
    def __init__(self, scene):
        self._scene = scene

    def scene(self):
        return self._scene

    def mapToScene(self, point):
        return point

    def viewport(self):
        class _Viewport:
            def rect(self):
                class _Rect:
                    def center(self):
                        return "center"
                return _Rect()
        return _Viewport()


class _FakeController:
    def __init__(self, scene):
        self._scene = scene
        self.created = []
        self.updated = []
        self.removed = []
        self.focused = []

    def create_at(self, position, **kwargs):
        pin_id = f"new-{len(self.created)}"
        pin = self._scene.add_pin(pin_id, "Waypoint")
        self.created.append(pin_id)
        return pin

    def update(self, pin, *, title=None, note=None):
        self.updated.append((pin.pin_id, title, note))
        self._scene.pin_store.update(pin.pin_id, title=title, note=note)

    def remove(self, pin_id_or_pin):
        pin_id = getattr(pin_id_or_pin, "pin_id", pin_id_or_pin)
        self.removed.append(pin_id)
        self._scene.pin_store.remove(pin_id)

    def focus(self, pin):
        self.focused.append(pin.pin_id)


class _FakeParent(QObject):
    def __init__(self):
        super().__init__()
        self.visible = True

    def setVisible(self, visible):
        self.visible = visible


class _FakeEditor:
    def __init__(self, accepted, title="Edited", note="a note"):
        self._accepted = accepted
        self._title = title
        self._note = note

    def exec(self):
        return QDialog.DialogCode.Accepted if self._accepted else QDialog.DialogCode.Rejected

    def values(self):
        return self._title, self._note


def _states(bridge):
    payloads = []
    bridge.stateChanged.connect(lambda p: payloads.append(json.loads(p)))
    return payloads


def _make():
    scene = _FakeScene()
    view = _FakeChatView(scene)
    controller = _FakeController(scene)
    bridge = PinOverlayBridge(view, controller)
    return bridge, scene, controller


class TestReady:
    def test_publishes_rows_from_the_pin_store(self):
        bridge, scene, _controller = _make()
        scene.add_pin("p1", "First", "a note")
        payloads = _states(bridge)

        bridge.ready()

        assert payloads[-1]["rows"] == [{"id": "p1", "title": "First", "note": "a note"}]
        assert payloads[-1]["selectedPinId"] is None


class TestStoreEventsRepublish:
    def test_adding_a_pin_directly_to_the_store_republishes(self):
        bridge, scene, _controller = _make()
        payloads = _states(bridge)

        scene.add_pin("p1", "First")

        assert payloads[-1]["rows"] == [{"id": "p1", "title": "First", "note": ""}]

    def test_removing_a_pin_republishes(self):
        bridge, scene, _controller = _make()
        scene.add_pin("p1", "First")
        payloads = _states(bridge)

        scene.pin_store.remove("p1")

        assert payloads[-1]["rows"] == []


class TestSelectionSync:
    def test_selecting_a_canvas_pin_publishes_its_id(self):
        bridge, scene, _controller = _make()
        pin = scene.add_pin("p1", "First")
        scene._selected = [pin]
        payloads = _states(bridge)

        scene.selectionChanged.emit()

        assert payloads[-1]["selectedPinId"] == "p1"

    def test_clearing_selection_publishes_none(self):
        bridge, scene, _controller = _make()
        pin = scene.add_pin("p1", "First")
        scene._selected = [pin]
        scene.selectionChanged.emit()
        scene._selected = []
        payloads = _states(bridge)

        scene.selectionChanged.emit()

        assert payloads[-1]["selectedPinId"] is None

    def test_ignores_non_pin_selected_items(self):
        bridge, scene, _controller = _make()
        scene._selected = [object()]
        payloads = _states(bridge)

        scene.selectionChanged.emit()

        assert payloads == []  # no change from None -> None, no publish

    def test_unchanged_selection_does_not_republish(self):
        bridge, scene, _controller = _make()
        pin = scene.add_pin("p1", "First")
        scene._selected = [pin]
        scene.selectionChanged.emit()
        payloads = _states(bridge)

        scene.selectionChanged.emit()  # same pin still selected

        assert payloads == []


class TestSelectPin:
    def test_selecting_a_known_pin_focuses_it_via_the_controller(self):
        bridge, scene, controller = _make()
        scene.add_pin("p1", "First")

        bridge.selectPin("p1")

        assert controller.focused == ["p1"]

    def test_selecting_an_unknown_pin_does_not_raise(self):
        bridge, _scene, controller = _make()

        bridge.selectPin("does-not-exist")

        assert controller.focused == []


class TestDeletePin:
    def test_deletes_without_any_confirmation_matching_legacy(self):
        bridge, scene, controller = _make()
        scene.add_pin("p1", "First")

        bridge.deletePin("p1")

        assert controller.removed == ["p1"]
        assert scene.pin_store.get("p1") is None


class TestCreatePin:
    def test_accepted_editor_commits_the_new_pin(self, monkeypatch):
        bridge, scene, controller = _make()
        monkeypatch.setattr(
            bridge_module, "NavigationPinEditor", lambda *a, **kw: _FakeEditor(accepted=True, title="Named", note="a note")
        )

        # Calls the deferred handler directly rather than createPin() +
        # QTimer.singleShot(0, ...) - keeps the test synchronous and avoids a
        # queued timer firing unexpectedly later in the same test session.
        bridge._perform_create_pin()

        assert len(controller.created) == 1
        assert controller.updated[-1][1:] == ("Named", "a note")
        assert controller.removed == []

    def test_cancelled_editor_removes_the_just_created_pin(self, monkeypatch):
        bridge, scene, controller = _make()
        monkeypatch.setattr(
            bridge_module, "NavigationPinEditor", lambda *a, **kw: _FakeEditor(accepted=False)
        )

        bridge._perform_create_pin()

        assert len(controller.created) == 1
        assert controller.removed == controller.created  # create-then-remove-on-cancel


class TestEditPin:
    def test_accepted_editor_commits_the_update(self, monkeypatch):
        bridge, scene, controller = _make()
        scene.add_pin("p1", "First")
        monkeypatch.setattr(
            bridge_module, "NavigationPinEditor", lambda *a, **kw: _FakeEditor(accepted=True, title="Renamed", note="n")
        )

        bridge._perform_edit_pin("p1")

        assert controller.updated == [("p1", "Renamed", "n")]

    def test_cancelled_editor_does_not_update(self, monkeypatch):
        bridge, scene, controller = _make()
        scene.add_pin("p1", "First")
        monkeypatch.setattr(
            bridge_module, "NavigationPinEditor", lambda *a, **kw: _FakeEditor(accepted=False)
        )

        bridge._perform_edit_pin("p1")

        assert controller.updated == []

    def test_editing_an_unknown_pin_does_not_raise(self):
        bridge, _scene, _controller = _make()

        bridge._perform_edit_pin("does-not-exist")  # must not raise


class TestResize:
    def test_bounds_to_the_min_max_range_and_emits_once_per_distinct_value(self):
        bridge, _scene, _controller = _make()
        heights = []
        bridge.heightRequested.connect(heights.append)

        bridge.resize(10)   # below min -> clamped
        bridge.resize(10)   # same bounded value -> no re-emit
        bridge.resize(9999)  # above max -> clamped

        assert heights == [bridge_module.PIN_OVERLAY_MIN_HEIGHT, bridge_module.PIN_OVERLAY_MAX_HEIGHT]


class TestClose:
    def test_hides_the_parent_host(self):
        bridge, _scene, _controller = _make()
        parent = _FakeParent()
        bridge.setParent(parent)

        bridge.close()

        assert parent.visible is False

    def test_is_a_no_op_without_a_parent(self):
        bridge, _scene, _controller = _make()

        bridge.close()  # must not raise


class TestDisposeIsIdempotent:
    def test_publish_is_a_no_op_after_dispose(self):
        bridge, scene, _controller = _make()
        payloads = _states(bridge)

        bridge.dispose()
        scene.add_pin("p1", "First")

        assert payloads == []
        assert bridge.disposed is True

    def test_store_events_after_dispose_do_not_republish(self):
        bridge, scene, _controller = _make()
        bridge.dispose()
        payloads = _states(bridge)

        scene.add_pin("p1", "First")

        assert payloads == []
