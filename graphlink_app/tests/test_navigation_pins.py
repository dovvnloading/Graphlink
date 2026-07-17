"""Regression coverage for canvas navigation-pin repaint geometry."""

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QStyleOptionViewItem

from graphlink_canvas.graphlink_canvas_navigation_pin import NavigationPin
from graphlink_navigation_pins import (
    NavigationPinRecord,
    NavigationPinStore,
    NavigationPinValidationError,
)
from graphlink_widgets.pins import NavigationPinDelegate, NavigationPinsListModel


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


def test_navigation_pin_list_rows_keep_consistent_vertical_rhythm():
    store = NavigationPinStore()
    store.add(title="Short pin")
    store.add(title="Annotated pin", note="A useful canvas checkpoint")
    model = NavigationPinsListModel(store)
    delegate = NavigationPinDelegate()
    option = QStyleOptionViewItem()

    plain_height = delegate.sizeHint(option, model.index(0, 0)).height()
    noted_height = delegate.sizeHint(option, model.index(1, 0)).height()

    assert plain_height == NavigationPinDelegate.ROW_HEIGHT
    assert noted_height == NavigationPinDelegate.NOTE_ROW_HEIGHT
    assert noted_height > plain_height
    model.dispose()


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
