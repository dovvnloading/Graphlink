"""Notification banner state for the new architecture (Qt-removal plan R2).

A generic, real (not stubbed) message queue: any backend intent handler can
call `show()` to surface a transient banner - the direct successor of
NotificationBridge, minus the Qt Signal plumbing. R2 wires the topic and the
manual `dismiss` intent; later phases (R4 send errors, R6 session
save/load) call `show()` from their own handlers as those land.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from backend.events import SessionBus

MessageType = Literal["info", "success", "warning", "error"]


@dataclass
class NotificationState:
    visible: bool = False
    message: str = ""
    msg_type: MessageType = "info"

    def show(self, message: str, msg_type: MessageType = "info") -> None:
        self.message = str(message)
        self.msg_type = msg_type
        self.visible = True

    def dismiss(self) -> None:
        self.visible = False

    def payload(self) -> dict[str, Any]:
        return {"visible": self.visible, "message": self.message, "msgType": self.msg_type}


def register_notifications(bus: SessionBus) -> NotificationState:
    state = NotificationState()
    bus.register_topic("notification", state.payload)

    async def dismiss():
        state.dismiss()
        await bus.publish("notification")

    bus.register_intent("notification", "dismiss", dismiss)
    return state
