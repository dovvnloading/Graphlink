"""Notification banner state for the new architecture (Qt-removal plan R2).

A generic, real (not stubbed) message queue: any backend intent handler can
call `show()` to surface a transient banner - the direct successor of
NotificationBridge, minus the Qt Signal plumbing. R2 wires the topic and the
manual `dismiss` intent; later phases (R4 send errors, R6 session
save/load) call `show()` from their own handlers as those land.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from backend.events import SessionBus

if TYPE_CHECKING:
    from graphlink_settings_store import SettingsManager

MessageType = Literal["info", "success", "warning", "error"]


@dataclass
class ShowMessageArgs:
    """ADR-003 stage 3.2: args schema for showInfo/showError - both take
    exactly one required string. dispatch_intent validates a real request's
    args against this before show_info/show_error ever runs (see
    backend/events.py's register_intent for the mechanism); a malformed call
    (missing, wrong-typed, or extra args) is rejected with a structured error
    instead of silently coercing via show()'s own str(message) - a non-string
    arg used to become a display string with no complaint."""

    message: str


@dataclass
class NotificationState:
    visible: bool = False
    message: str = ""
    msg_type: MessageType = "info"
    # R8a (UI/UX issue list finding #10): Settings' own "Notification types"
    # checkboxes wrote real preferences (SettingsManager.get_notification_
    # type_enabled already existed) that nothing ever read - show() set
    # visible=True unconditionally no matter what the user had unchecked.
    # Optional so the many call sites/tests that only ever had `bus` still
    # work unchanged; None means "no preference to check", i.e. always show.
    settings_manager: "SettingsManager | None" = field(default=None, repr=False, compare=False)

    def show(self, message: str, msg_type: MessageType = "info") -> None:
        if self.settings_manager is not None and not self.settings_manager.get_notification_type_enabled(
            msg_type
        ):
            return
        self.message = str(message)
        self.msg_type = msg_type
        self.visible = True

    def dismiss(self) -> None:
        self.visible = False

    def payload(self) -> dict[str, Any]:
        return {"visible": self.visible, "message": self.message, "msgType": self.msg_type}


def register_notifications(bus: SessionBus, settings_manager: "SettingsManager | None" = None) -> NotificationState:
    state = NotificationState(settings_manager=settings_manager)
    bus.register_topic("notification", state.payload)

    async def dismiss():
        state.dismiss()
        await bus.publish("notification")

    async def show_info(message: str):
        # The one frontend-triggerable "show" entry point, deliberately fixed
        # to "info" rather than accepting an arbitrary msg_type over the wire
        # (Literal["info", "success", "warning", "error"] isn't enforced at
        # runtime - a caller-supplied type could otherwise land in the
        # payload's msgType and mismatch the CSS class the frontend switches
        # on). Exists for genuinely frontend-only conditions - a Document
        # View request for a node with no content to show, say - that have no
        # other reason to round-trip through a backend intent handler.
        state.show(str(message), "info")
        await bus.publish("notification")

    async def show_error(message: str):
        # ADR-003 stage 3.1: the same fixed-severity pattern as show_info
        # above, for the WsTransport-detected class of failure a WS-level
        # intent request can fail with (a structured {"kind":"error"} reply -
        # unknown topic/intent, or an unhandled exception inside a handler).
        # Deliberately a SECOND narrow, fixed-severity entry point rather
        # than widening show_info to accept a caller-supplied msg_type - see
        # that function's own reasoning for why an arbitrary wire-supplied
        # severity is not trusted here either.
        state.show(str(message), "error")
        await bus.publish("notification")

    bus.register_intent("notification", "dismiss", dismiss)
    bus.register_intent("notification", "showInfo", show_info, args_schema=ShowMessageArgs)
    bus.register_intent("notification", "showError", show_error, args_schema=ShowMessageArgs)
    return state
