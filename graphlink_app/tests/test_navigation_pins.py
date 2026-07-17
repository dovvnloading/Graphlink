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

    # The beacon reaches y=33 and the hover/selection label spans 164px from
    # x=-82. The dirty region must cover the complete visual so moving/panning
    # cannot leave a frame-sized trail behind.
    assert rect.left() <= -82
    assert rect.top() <= -52
    assert rect.right() >= 82
    assert rect.bottom() >= 33
    assert rect.contains(QPointF(0, 32))


def test_navigation_pin_uses_the_new_beacon_visual_and_waypoint_default():
    pin = NavigationPin()

    assert pin.title == "Waypoint"
    assert pin.shape().contains(QPointF(0, 0))
    assert pin.shape().contains(QPointF(0, 30))
    assert pin.boundingRect().width() > 160


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
