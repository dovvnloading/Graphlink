"""The notification island's outbound wire contract, as typed Python
dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. Deliberately does NOT carry TYPE_STYLES' raw
color/icon values (graphlink_ui_components.py's NotificationBanner.TYPE_STYLES)
- only the semantic msgType is sent; picking a color/icon for a given type is
the React component's own styling concern, matching how composer sends
route "mode" rather than pre-resolved colors.

Cross-checked against a live NotificationBridge snapshot by
tests/test_notification_payload_schema.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MessageType = Literal["info", "success", "warning", "error"]


@dataclass
class NotificationStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    visible: bool
    message: str
    msgType: MessageType
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
