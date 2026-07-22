"""The SPA chat-library topic's wire contract (Qt-removal plan R2.5e).

Field-for-field the same shape as graphlink_chat_library_payload.py's
ChatLibraryStatePayload (id/title/createdLabel/updatedLabel rows, plus a
`notice` field for a recoverable DB-read error), registered as a distinct
codegen artifact so the SPA's validator is generated from this independent
Qt-free source rather than importing anything Qt-coupled. No loadChat/
newChat/search-query field: search is client-only, and load/new-chat are
deferred to R6 (see backend/chat_library.py's module docstring).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppChatLibraryRowPayload:
    id: int
    title: str
    createdLabel: str
    updatedLabel: str


@dataclass
class AppChatLibraryStatePayload:
    schemaVersion: int
    revision: int
    rows: list[AppChatLibraryRowPayload]
    notice: str | None = None
    minCompatibleSchemaVersion: int | None = None
