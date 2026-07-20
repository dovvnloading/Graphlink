"""Desktop-side state bridge for the pin-overlay island.

Phase 5 increment 1 - list/select/create/delete parity for the legacy
PinOverlay panel, wrapping the SAME NavigationPinsController/
NavigationPinStore every path already used (nothing moves). Filtering is pure
client-side (matching ChatLibraryDialog's own precedent) - Python always
publishes the full row list.

Pin creation/editing still pops the legacy, unchanged NavigationPinEditor
modal in THIS increment - deferred one event-loop tick via
QTimer.singleShot(0, ...) out of the QWebChannel slot invocation before
opening it, the same caution command-palette's executeCommand and
ChatLibraryBridge's loadChat/newChat already take for callbacks that pop
dialogs. Phase 5 increment 2 replaces this exec() choreography with an async
draft-intent flow; this bridge's shape is deliberately NOT pre-built for that
here, to keep this increment's platform-risk-proving scope tight.

Subscribes directly to NavigationPinStore's already-existing granular
added/updated/removed/reset events (see graphlink_navigation_pins.py) and to
scene.selectionChanged for canvas <-> list selection sync - both are real,
already-published Python-side mechanisms this bridge only forwards, not
anything new invented for this migration.
"""

from __future__ import annotations

from functools import partial
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QDialog

from graphlink_island_bridge import IslandBridge
from graphlink_widgets.pins import NavigationPinEditor

# Old widget: BASE_WIDTH=400, resize(BASE_WIDTH, MIN_HEIGHT) up to MAX_HEIGHT
# via _resize_for_content(). The web host negotiates the same range via
# apply_requested_height, driven by React measuring its own rendered height.
PIN_OVERLAY_MIN_HEIGHT = 276
PIN_OVERLAY_MAX_HEIGHT = 560


class PinOverlayBridge(IslandBridge, QObject):
    stateChanged = Signal(str)
    heightRequested = Signal(int)  # Qt-only side channel; see NotificationBridge's identical field

    def __init__(self, chat_view, controller, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._chat_view = chat_view
        self._controller = controller
        self._selected_pin_id: str | None = None
        self._last_height = 0
        scene = self._scene()
        scene.pin_store.subscribe(self._store_changed)
        scene.selectionChanged.connect(self._sync_selection)

    def _scene(self):
        return self._chat_view.scene()

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        rows = [
            {"id": record.pin_id, "title": record.title, "note": record.note}
            for record in self._scene().pin_store.records
        ]
        return {"rows": rows, "selectedPinId": self._selected_pin_id}

    def _on_dispose(self) -> None:
        scene = self._scene()
        scene.pin_store.unsubscribe(self._store_changed)
        try:
            scene.selectionChanged.disconnect(self._sync_selection)
        except (RuntimeError, TypeError):
            pass

    def _store_changed(self, event, payload) -> None:
        self.publish()

    def _sync_selection(self) -> None:
        selected = next(
            (item for item in self._scene().selectedItems() if hasattr(item, "pin_id")),
            None,
        )
        pin_id = selected.pin_id if selected is not None else None
        if pin_id == self._selected_pin_id:
            return
        self._selected_pin_id = pin_id
        self.publish()

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def selectPin(self, pin_id: str):
        pin = self._scene()._navigation_pin_item(pin_id)
        if pin is not None:
            self._controller.focus(pin)

    @Slot(str)
    def deletePin(self, pin_id: str):
        # No confirmation here, on purpose - the legacy remove_pin() never
        # confirmed either (checked directly: no QMessageBox anywhere in its
        # call path), so this is a faithful port, not a regression.
        self._controller.remove(pin_id)

    @Slot()
    def createPin(self):
        QTimer.singleShot(0, self._perform_create_pin)

    @Slot(str)
    def editPin(self, pin_id: str):
        QTimer.singleShot(0, partial(self._perform_edit_pin, pin_id))

    def _perform_create_pin(self):
        if self.disposed:
            return
        view = self._chat_view
        center = view.mapToScene(view.viewport().rect().center())
        pin = self._controller.create_at(center)
        if self._open_editor(pin, creating=True) is False:
            self._controller.remove(pin)

    def _perform_edit_pin(self, pin_id: str):
        if self.disposed:
            return
        pin = self._scene()._navigation_pin_item(pin_id)
        if pin is not None:
            self._open_editor(pin, creating=False)

    def _open_editor(self, pin, *, creating: bool):
        editor = NavigationPinEditor(pin.title, pin.note, self.parent(), creating=creating)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return False if creating else None
        title, note = editor.values()
        self._controller.update(pin, title=title, note=note)
        return True

    @Slot(int)
    def resize(self, height: int):
        bounded = max(PIN_OVERLAY_MIN_HEIGHT, min(PIN_OVERLAY_MAX_HEIGHT, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)

    @Slot()
    def close(self):
        """Hides the host directly (setVisible(False), not .close()) - see
        graphlink_pin_overlay_web.py's module docstring for why this host
        never goes through a native closeEvent. self.parent() is the
        PinOverlayHost (set by WebIslandHost.__init__'s own bridge.
        setParent(self))."""
        parent = self.parent()
        if parent is not None and hasattr(parent, "setVisible"):
            parent.setVisible(False)
