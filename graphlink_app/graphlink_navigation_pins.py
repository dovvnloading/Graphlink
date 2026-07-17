"""Domain model and in-memory store for canvas navigation pins.

The graphics scene and the management panel are projections of this module.  Keeping
the persistent record Qt-free makes pin validation, migration, and command behavior
testable without constructing the full application window.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import isfinite
from typing import Callable, Mapping
from uuid import uuid4


MAX_PIN_TITLE_LENGTH = 120
MAX_PIN_NOTE_LENGTH = 4_000


class NavigationPinValidationError(ValueError):
    """Raised when a persisted or user-authored pin record is invalid."""


def _clean_text(value, *, field: str, maximum: int, required: bool = False) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise NavigationPinValidationError(f"{field} is required")
    if len(text) > maximum:
        raise NavigationPinValidationError(f"{field} exceeds {maximum} characters")
    return text


def _finite_float(value, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NavigationPinValidationError(f"{field} must be a number") from exc
    if not isfinite(number):
        raise NavigationPinValidationError(f"{field} must be finite")
    return number


def _nonnegative_int(value, *, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise NavigationPinValidationError(f"{field} must be an integer") from exc
    if number < 0:
        raise NavigationPinValidationError(f"{field} must be non-negative")
    return number


@dataclass(frozen=True)
class NavigationPinRecord:
    """Validated, serializable state for one navigation pin."""

    pin_id: str
    title: str
    note: str
    position: tuple[float, float]
    anchor_item_id: str | None = None
    sort_order: int = 0
    created_at: str | None = None

    @classmethod
    def create(
        cls,
        *,
        title: str = "Canvas location",
        note: str = "",
        x: float = 0.0,
        y: float = 0.0,
        pin_id: str | None = None,
        anchor_item_id: str | None = None,
        sort_order: int = 0,
        created_at: str | None = None,
    ) -> "NavigationPinRecord":
        return cls(
            pin_id=str(pin_id or uuid4().hex),
            title=_clean_text(title, field="title", maximum=MAX_PIN_TITLE_LENGTH, required=True),
            note=_clean_text(note, field="note", maximum=MAX_PIN_NOTE_LENGTH),
            position=(_finite_float(x, field="position.x"), _finite_float(y, field="position.y")),
            anchor_item_id=str(anchor_item_id).strip() if anchor_item_id else None,
            sort_order=_nonnegative_int(sort_order, field="sort_order"),
            created_at=str(created_at or datetime.now(timezone.utc).isoformat()),
        )

    @classmethod
    def from_mapping(cls, payload: Mapping, *, fallback_order: int = 0) -> "NavigationPinRecord":
        if not isinstance(payload, Mapping):
            raise NavigationPinValidationError("pin payload must be an object")

        position = payload.get("position")
        if not isinstance(position, Mapping):
            raise NavigationPinValidationError("pin position must be an object")

        return cls.create(
            title=payload.get("title", "Canvas location"),
            note=payload.get("note", ""),
            x=position.get("x", 0.0),
            y=position.get("y", 0.0),
            pin_id=payload.get("pin_id") or payload.get("id"),
            anchor_item_id=payload.get("anchor_item_id"),
            sort_order=payload.get("sort_order", fallback_order),
            created_at=payload.get("created_at"),
        )

    def to_mapping(self) -> dict:
        return {
            "pin_id": self.pin_id,
            "title": self.title,
            "note": self.note,
            "position": {"x": self.position[0], "y": self.position[1]},
            "anchor_item_id": self.anchor_item_id,
            "sort_order": self.sort_order,
            "created_at": self.created_at,
        }

    def with_updates(self, **changes) -> "NavigationPinRecord":
        payload = self.to_mapping()
        position = payload.pop("position")
        payload.update(changes)
        if "x" in payload or "y" in payload:
            position = {
                "x": payload.pop("x", position["x"]),
                "y": payload.pop("y", position["y"]),
            }
        payload["position"] = position
        return NavigationPinRecord.from_mapping(payload, fallback_order=self.sort_order)


PinStoreListener = Callable[[str, object], None]


class NavigationPinStore:
    """Ordered record store with small, deterministic mutation notifications."""

    def __init__(self, records: list[NavigationPinRecord] | None = None):
        self._records: list[NavigationPinRecord] = []
        self._listeners: list[PinStoreListener] = []
        if records:
            self.reset(records)

    @property
    def records(self) -> tuple[NavigationPinRecord, ...]:
        return tuple(self._records)

    def subscribe(self, listener: PinStoreListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def unsubscribe(self, listener: PinStoreListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _emit(self, event: str, payload) -> None:
        for listener in tuple(self._listeners):
            listener(event, payload)

    def get(self, pin_id: str) -> NavigationPinRecord | None:
        return next((record for record in self._records if record.pin_id == pin_id), None)

    def index(self, pin_id: str) -> int:
        for index, record in enumerate(self._records):
            if record.pin_id == pin_id:
                return index
        return -1

    def add(self, record: NavigationPinRecord | None = None, **kwargs) -> NavigationPinRecord:
        if record is None:
            record = NavigationPinRecord.create(sort_order=len(self._records), **kwargs)
        elif self.get(record.pin_id) is not None:
            raise NavigationPinValidationError(f"duplicate pin id: {record.pin_id}")
        self._records.append(record)
        self._emit("added", (len(self._records) - 1, record))
        return record

    def update(self, pin_id: str, **changes) -> NavigationPinRecord:
        index = self.index(pin_id)
        if index < 0:
            raise KeyError(pin_id)
        before = self._records[index]
        after = before.with_updates(**changes)
        self._records[index] = after
        self._emit("updated", (index, before, after))
        return after

    def move(self, pin_id: str, x: float, y: float) -> NavigationPinRecord:
        return self.update(pin_id, x=x, y=y)

    def remove(self, pin_id: str) -> NavigationPinRecord | None:
        index = self.index(pin_id)
        if index < 0:
            return None
        removed = self._records.pop(index)
        self._records = [replace(record, sort_order=i) for i, record in enumerate(self._records)]
        self._emit("removed", (index, removed))
        return removed

    def reset(self, records: list[NavigationPinRecord]) -> None:
        unique: list[NavigationPinRecord] = []
        seen: set[str] = set()
        for index, record in enumerate(records):
            if record.pin_id in seen:
                raise NavigationPinValidationError(f"duplicate pin id: {record.pin_id}")
            seen.add(record.pin_id)
            unique.append(replace(record, sort_order=index))
        self._records = unique
        self._emit("reset", tuple(self._records))

    def clear(self) -> None:
        if not self._records:
            return
        self._records.clear()
        self._emit("reset", ())


class NavigationPinsController:
    """Application-facing command boundary for navigation-pin actions.

    The controller deliberately depends on the scene/view protocols rather than on
    widgets. This keeps the panel replaceable while ensuring every mutation and focus
    operation follows one path.
    """

    def __init__(self, scene, view):
        self.scene = scene
        self.view = view

    def create_at(self, position, *, title="Canvas location", note="", anchor_item_id=None):
        return self.scene.add_navigation_pin(
            position,
            title=title,
            note=note,
            anchor_item_id=anchor_item_id,
        )

    def update(self, pin, *, title=None, note=None):
        return self.scene.update_navigation_pin(pin, title=title, note=note)

    def remove(self, pin):
        return self.scene.remove_navigation_pin(pin)

    def clear(self):
        return self.scene.clear_navigation_pins()

    def focus(self, pin):
        if pin is None or pin.scene() != self.scene:
            return False
        self.scene.clearSelection()
        pin.setSelected(True)
        self.view.ensureVisible(pin, 48, 48)
        self.view.centerOn(pin)
        return True
