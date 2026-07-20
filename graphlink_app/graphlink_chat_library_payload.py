"""The chat-library island's outbound wire contract, as typed Python
dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. The `ChatDatabase.get_all_chats()` sqlite rows
(raw `(id, title, created_at, updated_at)` tuples) are deliberately NOT sent
verbatim: the two timestamp columns are pre-formatted into display strings on
the Python side (`_format_timestamp`, moved verbatim from the legacy
ChatLibraryDialog, per the migration's "timestamp formatting stays as today"
decision) so the web side renders strings and never has to know the stored
`"%Y-%m-%d %H:%M:%S"` format or reimplement `strftime`. Search filtering is
pure client-side, so no query field is round-tripped.

Cross-checked against a live ChatLibraryBridge snapshot by
tests/test_chat_library_payload_schema.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatLibraryRow:
    id: int
    title: str
    # Both pre-formatted by _format_timestamp for display - see module docstring.
    createdLabel: str
    updatedLabel: str


@dataclass
class ChatLibraryStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    rows: list[ChatLibraryRow]
    # Transient, recoverable Python-side message - a per-row load_chat()
    # failure (legacy's QMessageBox.critical) or a get_all_chats() read
    # failure. Rendered as an inline status line and cleared client-side on
    # the next keystroke/action, exactly like command-palette's `notice`.
    # Deliberately NOT surfaced through BridgeErrorState: that replaces the
    # whole surface and its hint is schema-mismatch-specific, wrong for a
    # recoverable DB error. Genuine island-load/payload-rejection failures
    # still use BridgeErrorState via the standard onRejection path.
    notice: str | None = None
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
