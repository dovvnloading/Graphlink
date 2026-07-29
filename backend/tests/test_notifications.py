"""NotificationState preference-gating tests (R8a UI/UX issue list finding #10).

Settings' "Notification types" checkboxes wrote real preferences via
SettingsManager.set_notification_preferences - the getter existed and had
zero callers. NotificationState.show() set visible=True unconditionally, so
unchecking a type had no effect on whether its banner appeared.
"""

import pytest
from graphlink_licensing import SettingsManager

from backend.events import SessionBus
from backend.notifications import NotificationState, register_notifications


@pytest.fixture
def manager(tmp_path):
    return SettingsManager(tmp_path / "session.dat")


def test_show_with_no_settings_manager_always_shows():
    # The many existing call sites/tests construct NotificationState/
    # register_notifications with no settings manager at all - that must
    # keep behaving exactly as before (always show).
    state = NotificationState()
    state.show("hello", "warning")
    assert state.visible is True
    assert state.message == "hello"
    assert state.msg_type == "warning"


def test_show_is_suppressed_when_the_type_is_disabled(manager):
    manager.set_notification_preferences({"warning": False})
    state = NotificationState(settings_manager=manager)

    state.show("a warning", "warning")

    assert state.visible is False
    assert state.message == ""


def test_show_still_works_for_a_type_left_enabled(manager):
    manager.set_notification_preferences({"warning": False})
    state = NotificationState(settings_manager=manager)

    state.show("all good", "success")

    assert state.visible is True
    assert state.message == "all good"
    assert state.msg_type == "success"


def test_disabling_and_then_reenabling_a_type_restores_show(manager):
    manager.set_notification_preferences({"error": False})
    state = NotificationState(settings_manager=manager)
    state.show("boom", "error")
    assert state.visible is False

    manager.set_notification_preferences({"error": True})
    state.show("boom again", "error")
    assert state.visible is True


def test_a_suppressed_show_does_not_clobber_a_currently_visible_banner(manager):
    # show() bails out before touching message/msg_type/visible when the
    # type is disabled - a currently-showing banner of a DIFFERENT type must
    # survive a suppressed call, not get silently wiped to blank.
    manager.set_notification_preferences({"warning": False})
    state = NotificationState(settings_manager=manager)
    state.show("still here", "info")
    assert state.visible is True

    state.show("suppressed", "warning")

    assert state.visible is True
    assert state.message == "still here"
    assert state.msg_type == "info"


def test_dismiss_is_unaffected_by_preferences(manager):
    manager.set_notification_preferences({"info": False})
    state = NotificationState(settings_manager=manager)
    state.visible = True

    state.dismiss()

    assert state.visible is False


def test_register_notifications_wires_the_settings_manager_through(manager):
    manager.set_notification_preferences({"success": False})
    bus = SessionBus("notifications-gating-test")
    state = register_notifications(bus, manager)

    state.show("saved", "success")

    assert state.visible is False


def test_register_notifications_without_a_settings_manager_still_shows_everything():
    bus = SessionBus("notifications-no-manager-test")
    state = register_notifications(bus)

    state.show("hello", "error")

    assert state.visible is True
