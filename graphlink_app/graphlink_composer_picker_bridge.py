"""Desktop-side state bridge for the composer-picker island (Phase 5
increment 3) - absorbs ComposerPickerPopup (native Qt.Tool popup, deleted
this increment). One bridge instance serves BOTH the model picker and the
reasoning-level picker, exactly like the native popup it replaces (a `kind`
switch, not two surfaces) - see graphlink_composer_picker_payload.py.

Wraps the SAME ComposerBridge.route_snapshot()/selectModel()/
setReasoningLevel() every path already used (nothing about model/reasoning
selection itself moves) - this bridge only reformats route_snapshot()'s dict
into the option-row shape the React list renders, exactly as
ComposerPickerPopup._refresh_options() used to.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_composer_picker_payload import ComposerPickerOption
from graphlink_island_bridge import IslandBridge

# Legacy popup: minimumWidth 380 / maximumWidth 440 (Qt layout-managed,
# content free to grow between those bounds). The web host uses one fixed
# width instead - height is what negotiates with content (see
# apply_requested_height), matching the min/max_height-only sizing already
# established for PinOverlayHost/NotificationWebHost.
COMPOSER_PICKER_MIN_HEIGHT = 160
COMPOSER_PICKER_MAX_HEIGHT = 480


class ComposerPickerBridge(IslandBridge, QObject):
    stateChanged = Signal(str)
    heightRequested = Signal(int)  # Qt-only side channel; see PinOverlayBridge's identical field

    def __init__(self, composer_bridge, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._composer_bridge = composer_bridge
        self._kind = "model"
        self._open_token = 0
        self._last_height = 0

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    @property
    def kind(self) -> str:
        return self._kind

    def open(self, kind: str) -> None:
        """Called directly by graphlink_window.py's open_composer_model_picker
        - Python-initiated, not a React intent, mirroring PinOverlayHost.
        show_for_anchor()'s own plain-method-called-by-the-window shape."""
        self._kind = "reasoning" if kind == "reasoning" else "model"
        self._open_token += 1
        self.publish()

    def _route(self) -> dict[str, Any]:
        return self._composer_bridge.route_snapshot()

    def _options(self, route: dict[str, Any]) -> list[ComposerPickerOption]:
        if self._kind == "model":
            raw_options = route.get("modelOptions") or []
            active_id = str(route.get("modelId") or "").strip()
        else:
            reasoning = route.get("reasoning") or {}
            raw_options = reasoning.get("options") or [] if isinstance(reasoning, dict) else []
            active_id = str(reasoning.get("level") or "").strip() if isinstance(reasoning, dict) else ""

        options: list[ComposerPickerOption] = []
        for raw in raw_options:
            if not isinstance(raw, dict):
                continue
            option_id = str(raw.get("id") or "").strip()
            label = str(raw.get("label") or option_id or "Option").strip()
            is_current = bool(raw.get("active")) or (bool(option_id) and option_id == active_id)
            if self._kind == "model":
                ready = bool(raw.get("ready", True))
                available = bool(raw.get("available", True))
                unavailable = not available or (not ready and not is_current)
                meta = "Selected" if is_current else (
                    "Installed" if raw.get("source") == "installed" else "Available"
                )
                if not ready:
                    meta += " - verify in Settings"
            else:
                unavailable = False
                meta = str(raw.get("description") or "").strip()
            options.append(
                ComposerPickerOption(
                    id=option_id,
                    label=label,
                    meta=meta,
                    current=is_current,
                    unavailable=unavailable,
                )
            )
        return options

    def _title(self, route: dict[str, Any]) -> str:
        if self._kind == "model":
            return str(route.get("provider") or "Choose a model")
        return "Choose response depth"

    def _build_state_payload(self) -> dict[str, Any]:
        route = self._route()
        return {
            "kind": self._kind,
            "title": self._title(route),
            "options": [
                {
                    "id": option.id,
                    "label": option.label,
                    "meta": option.meta,
                    "current": option.current,
                    "unavailable": option.unavailable,
                }
                for option in self._options(route)
            ],
            "openToken": self._open_token,
        }

    @Slot()
    def ready(self):
        self.publish()

    @Slot(str)
    def selectOption(self, option_id: str):
        option_id = str(option_id or "").strip()
        if not option_id:
            return
        if self._kind == "model":
            self._composer_bridge.selectModel(option_id)
        else:
            self._composer_bridge.setReasoningLevel(option_id)
        self.close()

    @Slot()
    def requestSettings(self):
        window = getattr(self._composer_bridge, "window", None)
        show_settings = getattr(window, "show_settings", None)
        if callable(show_settings):
            show_settings()
        self.close()

    @Slot(int)
    def resize(self, height: int):
        bounded = max(COMPOSER_PICKER_MIN_HEIGHT, min(COMPOSER_PICKER_MAX_HEIGHT, int(height)))
        if bounded == self._last_height:
            return
        self._last_height = bounded
        self.heightRequested.emit(bounded)

    @Slot()
    def close(self):
        parent = self.parent()
        if parent is not None and hasattr(parent, "setVisible"):
            parent.setVisible(False)
