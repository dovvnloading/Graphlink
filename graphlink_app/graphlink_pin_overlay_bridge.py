"""Desktop-side state bridge for the pin-overlay island.

Phase 5 increment 1 built list/select/create/delete parity for the legacy
PinOverlay panel, wrapping the SAME NavigationPinsController/
NavigationPinStore every path already used (nothing moves). Filtering is pure
client-side (matching ChatLibraryDialog's own precedent) - Python always
publishes the full row list.

Phase 5 increment 2 (this revision): pin creation/editing no longer pops the
legacy NavigationPinEditor modal at all. createPin()/editPin() begin an async
draft via NavigationPinsController.begin_draft_pin() (synchronous now - no
QTimer.singleShot deferral needed, since nothing here blocks the Qt event
loop the way a modal .exec() did) and the state payload's `draft` field tells
React to render an in-panel editor view instead of the list.
commitDraft(title, note)/discardDraft() end it. See
graphlink_navigation_pins.py's own docstrings for the full create-then-
remove-on-cancel-preserved-but-now-async rationale.

Subscribes directly to NavigationPinStore's already-existing granular
added/updated/removed/reset events (see graphlink_navigation_pins.py) and to
scene.selectionChanged for canvas <-> list selection sync - both are real,
already-published Python-side mechanisms this bridge only forwards, not
anything new invented for this migration.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_island_bridge import IslandBridge
from graphlink_navigation_pins import NavigationPinValidationError

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
        self._error: str | None = None
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
        draft_record = self._controller.draft
        draft = None
        if draft_record is not None:
            draft = {
                "pinId": draft_record.pin_id,
                "title": draft_record.title,
                "note": draft_record.note,
                "isNew": self._controller.draft_is_new,
            }
        return {
            "rows": rows,
            "selectedPinId": self._selected_pin_id,
            "draft": draft,
            "error": self._error,
        }

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
        view = self._chat_view
        center = view.mapToScene(view.viewport().rect().center())
        self._error = None
        self._controller.begin_draft_pin(position=center)
        self.publish()

    @Slot(str)
    def editPin(self, pin_id: str):
        pin = self._scene()._navigation_pin_item(pin_id)
        if pin is None:
            return
        self._error = None
        self._controller.begin_draft_pin(pin=pin)
        self.publish()

    @Slot(str, str)
    def commitDraft(self, title: str, note: str):
        """The caller (App.tsx) already validates client-side before calling
        this, matching the legacy dialog's own inline checks - a validation
        failure here is defense in depth, surfaced via `error` rather than
        raising through the QWebChannel slot boundary. The draft stays
        active on failure (see NavigationPinsController.commit_draft's own
        docstring) so a corrected retry still targets the same pin."""
        try:
            self._controller.commit_draft(title=title, note=note)
            self._error = None
        except NavigationPinValidationError as exc:
            self._error = str(exc)
        self.publish()

    @Slot()
    def discardDraft(self):
        self._controller.discard_draft()
        self._error = None
        self.publish()

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
        setParent(self)). Discards any in-progress draft first - closing the
        panel mid-edit is equivalent to cancelling, and without this a
        newly-created-but-never-resolved pin would silently persist just
        because the user closed the panel instead of clicking Cancel."""
        if self._controller.draft is not None:
            self._controller.discard_draft()
            self._error = None
        parent = self.parent()
        if parent is not None and hasattr(parent, "setVisible"):
            parent.setVisible(False)
