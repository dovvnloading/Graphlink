"""Contract tests for the pin-overlay island bridge (Phase 5 increments 1-2).

Phase 5 increment 2's controller (NavigationPinsController) is the real,
already-tested-elsewhere class now - draft-tracking behavior itself is
covered by tests/test_navigation_pins.py (if present) or directly by these
bridge tests via the real controller wired to a fake scene, not a fake
controller, since the whole point of increment 2 is that the CONTROLLER
(not the bridge) owns begin_draft_pin/commit_draft/discard_draft.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtCore import QObject, Signal

import graphlink_pin_overlay_bridge as bridge_module
from graphlink_navigation_pins import NavigationPinsController, NavigationPinStore
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

    def add_navigation_pin(self, pos, title=None, note="", pin_id=None, anchor_item_id=None):
        # Real NavigationPinsController.create_at() calls scene.add_navigation_pin
        # (the real graphlink_scene.py signature) - mirrored minimally here.
        pin_id = pin_id or f"new-{len(self._pins_by_id)}"
        if title is None or not str(title).strip():
            title = f"Waypoint {len(self.pin_store.records) + 1}"
        return self.add_pin(pin_id, title, note)

    def update_navigation_pin(self, pin, *, title=None, note=None):
        changes = {}
        if title is not None:
            changes["title"] = title
        if note is not None:
            changes["note"] = note
        if changes:
            record = self.pin_store.update(pin.pin_id, **changes)
            pin.title, pin.note = record.title, record.note
            return record
        return self.pin_store.get(pin.pin_id)

    def remove_navigation_pin(self, pin_or_id):
        pin_id = getattr(pin_or_id, "pin_id", pin_or_id)
        removed = self.pin_store.remove(pin_id)
        self._pins_by_id.pop(pin_id, None)
        return removed


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


class _FakeParent(QObject):
    def __init__(self):
        super().__init__()
        self.visible = True

    def setVisible(self, visible):
        self.visible = visible


def _states(bridge):
    payloads = []
    bridge.stateChanged.connect(lambda p: payloads.append(json.loads(p)))
    return payloads


def _make():
    scene = _FakeScene()
    view = _FakeChatView(scene)
    controller = NavigationPinsController(scene, view)
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
        assert payloads[-1]["draft"] is None
        assert payloads[-1]["error"] is None


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
        focused = []
        controller.focus = lambda pin: focused.append(pin.pin_id)

        bridge.selectPin("p1")

        assert focused == ["p1"]

    def test_selecting_an_unknown_pin_does_not_raise(self):
        bridge, _scene, _controller = _make()

        bridge.selectPin("does-not-exist")


class TestDeletePin:
    def test_deletes_without_any_confirmation_matching_legacy(self):
        bridge, scene, _controller = _make()
        scene.add_pin("p1", "First")

        bridge.deletePin("p1")

        assert scene.pin_store.get("p1") is None


class TestCreatePin:
    def test_begins_a_draft_synchronously_no_native_modal_no_timer(self):
        bridge, scene, controller = _make()
        payloads = _states(bridge)

        bridge.createPin()

        # Synchronous - no QTimer.singleShot deferral needed since nothing
        # blocking happens anymore (the whole point of increment 2).
        assert len(scene.pin_store.records) == 1
        draft = payloads[-1]["draft"]
        assert draft is not None
        assert draft["isNew"] is True
        assert draft["pinId"] == scene.pin_store.records[0].pin_id


class TestEditPin:
    def test_begins_a_draft_for_an_existing_pin_prefilled_with_its_current_values(self):
        bridge, scene, _controller = _make()
        scene.add_pin("p1", "First", "a note")
        payloads = _states(bridge)

        bridge.editPin("p1")

        draft = payloads[-1]["draft"]
        assert draft == {"pinId": "p1", "title": "First", "note": "a note", "isNew": False}

    def test_editing_an_unknown_pin_does_not_raise_and_does_not_begin_a_draft(self):
        bridge, _scene, controller = _make()
        payloads = _states(bridge)

        bridge.editPin("does-not-exist")

        assert payloads == []
        assert controller.draft is None


class TestCommitDraft:
    def test_committing_a_new_pin_draft_applies_the_title_and_note(self):
        bridge, scene, _controller = _make()
        bridge.createPin()
        pin_id = scene.pin_store.records[0].pin_id
        payloads = _states(bridge)

        bridge.commitDraft("Named", "a real note")

        assert scene.pin_store.get(pin_id).title == "Named"
        assert scene.pin_store.get(pin_id).note == "a real note"
        assert payloads[-1]["draft"] is None

    def test_committing_an_existing_pin_edit_applies_the_update(self):
        bridge, scene, _controller = _make()
        scene.add_pin("p1", "First")
        bridge.editPin("p1")

        bridge.commitDraft("Renamed", "n")

        assert scene.pin_store.get("p1").title == "Renamed"
        assert scene.pin_store.get("p1").note == "n"

    def test_a_validation_failure_surfaces_as_error_and_keeps_the_draft_active(self):
        bridge, scene, controller = _make()
        bridge.createPin()
        pin_id = scene.pin_store.records[0].pin_id
        payloads = _states(bridge)

        bridge.commitDraft("", "")  # empty title fails NavigationPinRecord validation

        assert payloads[-1]["error"] is not None
        assert controller.draft is not None  # stays active for a corrected retry
        assert scene.pin_store.get(pin_id).title != ""  # unchanged, not committed


class TestDiscardDraft:
    def test_discarding_a_new_pin_draft_removes_it_create_then_remove_on_cancel(self):
        bridge, scene, _controller = _make()
        bridge.createPin()
        pin_id = scene.pin_store.records[0].pin_id

        bridge.discardDraft()

        assert scene.pin_store.get(pin_id) is None

    def test_discarding_an_existing_pin_edit_leaves_it_unchanged(self):
        bridge, scene, _controller = _make()
        scene.add_pin("p1", "First", "orig note")
        bridge.editPin("p1")

        bridge.discardDraft()

        assert scene.pin_store.get("p1").title == "First"
        assert scene.pin_store.get("p1").note == "orig note"

    def test_clears_a_pending_error(self):
        bridge, scene, controller = _make()
        bridge.createPin()
        bridge.commitDraft("", "")  # sets an error, draft stays active
        payloads = _states(bridge)

        bridge.discardDraft()

        assert payloads[-1]["error"] is None


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

    def test_discards_a_pending_new_pin_draft_before_hiding(self):
        bridge, scene, controller = _make()
        parent = _FakeParent()
        bridge.setParent(parent)
        bridge.createPin()
        pin_id = scene.pin_store.records[0].pin_id

        bridge.close()

        assert scene.pin_store.get(pin_id) is None
        assert controller.draft is None
        assert parent.visible is False

    def test_leaves_an_in_progress_edit_of_an_existing_pin_unchanged(self):
        bridge, scene, _controller = _make()
        scene.add_pin("p1", "First")
        bridge.editPin("p1")

        bridge.close()

        assert scene.pin_store.get("p1").title == "First"


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
