"""The SPA chat-library topic's wire contract (Qt-removal plan R2.5e, R8a
library redesign).

Started field-for-field identical to graphlink_chat_library_payload.py's
(now-deleted, Qt-era) ChatLibraryStatePayload; the Qt app is fully gone as
of R7.6b, so there is no longer any legacy shape to mirror. R8a adds what
the redesigned list needs to show real per-row content instead of just a
title and two timestamps: createdAtIso/updatedAtIso (real parseable
instants, for date-bucketed grouping - createdLabel/updatedLabel stay as
human display strings, not meant to be parsed back), preview (a one-line
snippet of the last message, computed once at save time - see
backend/chat_library.py's _extract_preview_and_message_count), and
messageCount.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppChatLibraryRowPayload:
    id: int
    title: str
    createdLabel: str
    updatedLabel: str
    createdAtIso: str | None
    updatedAtIso: str | None
    preview: str
    messageCount: int


@dataclass
class AppChatLibraryStatePayload:
    schemaVersion: int
    revision: int
    rows: list[AppChatLibraryRowPayload]
    notice: str | None = None
    minCompatibleSchemaVersion: int | None = None
