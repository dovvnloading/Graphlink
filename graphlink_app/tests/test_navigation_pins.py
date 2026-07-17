"""Regression coverage for canvas navigation-pin repaint geometry."""

from PySide6.QtCore import QPointF

from graphlink_canvas.graphlink_canvas_navigation_pin import NavigationPin
from graphlink_navigation_pins import (
    NavigationPinRecord,
    NavigationPinStore,
    NavigationPinValidationError,
)


def test_navigation_pin_dirty_rect_covers_everything_it_paints():
    pin = NavigationPin()
    rect = pin.boundingRect()

    # The pin body reaches y=25 and the hover/selection title is painted in a
    # 100px-wide rectangle starting at (-50, -35).  The dirty region must cover
    # both so moving/panning cannot leave a frame-sized trail behind.
    assert rect.left() <= -50
    assert rect.top() <= -35
    assert rect.right() >= 50
    assert rect.bottom() >= 25
    assert rect.contains(QPointF(0, 25))


def test_pin_store_preserves_explicit_order_and_reindexes_after_remove():
    store = NavigationPinStore()
    first = store.add(title="First", x=1, y=2)
    second = store.add(title="Second", x=3, y=4)

    assert [record.pin_id for record in store.records] == [first.pin_id, second.pin_id]
    store.remove(first.pin_id)

    assert [record.title for record in store.records] == ["Second"]
    assert store.records[0].sort_order == 0


def test_pin_record_accepts_legacy_shape_and_rejects_invalid_coordinates():
    record = NavigationPinRecord.from_mapping(
        {"title": "Legacy", "note": None, "position": {"x": 5, "y": 6}},
        fallback_order=3,
    )
    assert record.title == "Legacy"
    assert record.note == ""
    assert record.sort_order == 3

    try:
        NavigationPinRecord.from_mapping(
            {"title": "Bad", "position": {"x": "nan", "y": 1}}
        )
    except NavigationPinValidationError:
        pass
    else:
        raise AssertionError("invalid coordinates must be rejected")
