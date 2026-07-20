"""Contract tests for the notification island bridge."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_notification_bridge import (
    NOTIFICATION_MAX_HEIGHT,
    NOTIFICATION_MIN_HEIGHT,
    NotificationBridge,
)


class _Window:
    def __init__(self, notifications_enabled=True):
        self._enabled = notifications_enabled
        self.calls = []

    def should_show_notification(self, msg_type):
        self.calls.append(msg_type)
        return self._enabled


def _states(bridge):
    payloads = []
    bridge.stateChanged.connect(lambda p: payloads.append(json.loads(p)))
    return payloads


class TestShowMessage:
    def test_publishes_visible_message_and_type(self):
        bridge = NotificationBridge(_Window())
        payloads = _states(bridge)

        bridge.show_message("Saved.", 3000, "success")

        assert payloads[-1]["visible"] is True
        assert payloads[-1]["message"] == "Saved."
        assert payloads[-1]["msgType"] == "success"

    def test_unrecognized_type_falls_back_to_info_in_the_payload(self):
        # Matches the old widget's TYPE_STYLES.get(msg_type, TYPE_STYLES["info"])
        # fallback - several real call sites pass a dynamic, not-always-one-of-
        # the-4-literals string (e.g. a settings-derived "level"/"tone" value).
        bridge = NotificationBridge(_Window())
        payloads = _states(bridge)

        bridge.show_message("Something happened.", 3000, "some_dynamic_value")

        assert payloads[-1]["msgType"] == "info"

    def test_gated_by_should_show_notification_publishes_nothing(self):
        window = _Window(notifications_enabled=False)
        bridge = NotificationBridge(window)
        payloads = _states(bridge)

        bridge.show_message("Should not appear.", 3000, "info")

        assert payloads == []
        assert window.calls == ["info"]

    def test_missing_should_show_notification_defaults_to_showing(self):
        bridge = NotificationBridge(window=object())  # no should_show_notification attr
        payloads = _states(bridge)

        bridge.show_message("Fine.", 3000, "info")

        assert payloads[-1]["visible"] is True

    def test_none_window_defaults_to_showing(self):
        bridge = NotificationBridge(window=None)
        payloads = _states(bridge)

        bridge.show_message("Fine.", 3000, "info")

        assert payloads[-1]["visible"] is True


class TestVisibilityChanged:
    def test_emits_true_on_first_show(self):
        bridge = NotificationBridge(_Window())
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.show_message("Hi.", 3000, "info")

        assert seen == [True]

    def test_does_not_re_emit_for_a_second_show_while_already_visible(self):
        bridge = NotificationBridge(_Window())
        bridge.show_message("First.", 3000, "info")
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.show_message("Second.", 3000, "success")

        assert seen == []

    def test_emits_false_when_the_hide_timer_fires(self):
        bridge = NotificationBridge(_Window())
        bridge.show_message("Hi.", 50, "info")
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.hide_timer.timeout.emit()

        assert seen == [False]

    def test_does_not_emit_when_gated_by_should_show_notification(self):
        window = _Window(notifications_enabled=False)
        bridge = NotificationBridge(window)
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.show_message("Should not appear.", 3000, "info")

        assert seen == []


class TestDurationOverrides:
    def test_error_type_never_auto_hides(self):
        bridge = NotificationBridge(_Window())

        bridge.show_message("Boom.", 3000, "error")

        assert not bridge.hide_timer.isActive()

    def test_warning_type_has_a_ten_second_minimum_even_if_a_shorter_one_is_requested(self):
        bridge = NotificationBridge(_Window())

        bridge.show_message("Careful.", 2000, "warning")

        assert bridge.hide_timer.isActive()
        assert bridge.hide_timer.remainingTime() > 9000  # ~10000ms, allowing for test jitter

    def test_warning_type_does_not_shrink_an_already_longer_duration(self):
        bridge = NotificationBridge(_Window())

        bridge.show_message("Careful.", 20000, "warning")

        assert bridge.hide_timer.remainingTime() > 19000

    def test_info_and_success_use_the_requested_duration_unmodified(self):
        bridge = NotificationBridge(_Window())

        bridge.show_message("Hi.", 1234, "info")

        assert bridge.hide_timer.remainingTime() <= 1234


class TestAutoHide:
    def test_hide_timer_firing_republishes_not_visible(self):
        bridge = NotificationBridge(_Window())
        bridge.show_message("Hi.", 50, "info")
        payloads = _states(bridge)

        bridge.hide_timer.timeout.emit()  # simulate the timer firing directly

        assert payloads[-1]["visible"] is False

    def test_hide_is_idempotent_if_already_hidden(self):
        bridge = NotificationBridge(_Window())
        payloads = _states(bridge)

        bridge._hide()

        assert payloads == []  # nothing to publish - was never visible

    def test_a_new_show_message_call_stops_any_pending_hide_timer(self):
        bridge = NotificationBridge(_Window())
        bridge.show_message("First.", 100, "info")
        assert bridge.hide_timer.isActive()

        bridge.show_message("Second.", 0, "error")

        assert not bridge.hide_timer.isActive()


class TestCopyDetails:
    def test_copies_the_current_message(self):
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        bridge = NotificationBridge(_Window())
        bridge.show_message("Copy this text.", 3000, "info")

        bridge.copyDetails()

        assert QApplication.clipboard().text() == "Copy this text."


class TestDismiss:
    def test_hides_a_visible_notification(self):
        bridge = NotificationBridge(_Window())
        bridge.show_message("Hi.", 0, "error")
        payloads = _states(bridge)

        bridge.dismiss()

        assert payloads[-1]["visible"] is False

    def test_stops_a_pending_auto_hide_timer(self):
        bridge = NotificationBridge(_Window())
        bridge.show_message("Hi.", 5000, "info")
        assert bridge.hide_timer.isActive()

        bridge.dismiss()

        assert not bridge.hide_timer.isActive()

    def test_emits_visibility_changed_false(self):
        bridge = NotificationBridge(_Window())
        bridge.show_message("Hi.", 0, "error")
        seen = []
        bridge.visibilityChanged.connect(seen.append)

        bridge.dismiss()

        assert seen == [False]

    def test_is_idempotent_if_already_hidden(self):
        bridge = NotificationBridge(_Window())
        payloads = _states(bridge)

        bridge.dismiss()

        assert payloads == []


class TestHeightNegotiation:
    def test_clamps_height_requests_to_bounds(self):
        bridge = NotificationBridge(_Window())
        heights = []
        bridge.heightRequested.connect(heights.append)

        bridge.resize(1)
        bridge.resize(10_000)

        assert heights == [NOTIFICATION_MIN_HEIGHT, NOTIFICATION_MAX_HEIGHT]

    def test_dedupes_repeated_identical_height_requests(self):
        bridge = NotificationBridge(_Window())
        heights = []
        bridge.heightRequested.connect(heights.append)

        bridge.resize(200)
        bridge.resize(200)

        assert heights == [200]


class TestDisposeIsIdempotent:
    def test_publish_is_a_no_op_after_dispose(self):
        bridge = NotificationBridge(_Window())
        payloads = _states(bridge)

        bridge.dispose()
        bridge.show_message("Too late.", 3000, "info")

        assert payloads == []
        assert bridge.disposed is True

    def test_dispose_stops_the_hide_timer(self):
        bridge = NotificationBridge(_Window())
        bridge.show_message("Hi.", 5000, "info")
        assert bridge.hide_timer.isActive()

        bridge.dispose()

        assert not bridge.hide_timer.isActive()

    def test_dispose_twice_does_not_raise(self):
        bridge = NotificationBridge(_Window())
        bridge.dispose()
        bridge.dispose()  # must not raise
