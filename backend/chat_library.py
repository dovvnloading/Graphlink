"""Chat library dialog: list/rename/delete (Qt-removal plan R2.5e).

An INDEPENDENT Qt-free reimplementation of ChatDatabase.get_all_chats()/
rename_chat()/delete_chat() - not an import - because
graphlink_session/__init__.py eagerly imports ChatSessionManager and
SaveWorkerThread (workers.py imports PySide6.QtCore.QThread/Signal) before
graphlink_session.database can ever be imported cleanly: Python always runs
a package's __init__.py first, even for `from graphlink_session.database
import ChatDatabase`. ChatDatabase itself (graphlink_session/database.py)
is Qt-free; only the package wrapper around it is hazardous. Same
reimplement-not-import precedent as backend/composer.py and
backend/plugins.py.

Reads/writes the SAME real ~/.graphlink/chats.db file the legacy app uses
(same "chats" table schema, same queries, same _format_timestamp display
format moved verbatim from graphlink_chat_library_bridge.py) - list, rename,
and delete are genuinely real here. loadChat/newChat are deferred to R6:
session load rebuilds the whole scene through backend/canvas.py's
SceneDocument and session save doesn't exist yet, so the SPA renders those
as disabled controls with an explicit R6 label rather than faking them.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.events import SessionBus

DEFAULT_DB_PATH = Path.home() / ".graphlink" / "chats.db"


def _format_timestamp(value: Any) -> str:
    """Moved verbatim from graphlink_chat_library_bridge.py - the stored
    format is sqlite's `"%Y-%m-%d %H:%M:%S"`; unparseable/empty values echo
    back unchanged, matching the legacy behavior exactly."""
    if not value:
        return "Unknown"
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
        return parsed.strftime("%b %d, %Y %I:%M %p")
    except ValueError:
        return str(value)


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_chats_table(conn: sqlite3.Connection) -> None:
    # Mirrors ChatDatabase.init_database()'s chats table exactly - this
    # library only ever reads/writes this one table, so it's the only one
    # this reimplementation needs to guarantee exists (matters if the SPA
    # backend runs before the legacy app has ever created chats.db).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data TEXT NOT NULL
        )
        """
    )


def get_all_chats(db_path: Path) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        _ensure_chats_table(conn)
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chats ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {
            "id": int(row[0]),
            "title": str(row[1]),
            "createdLabel": _format_timestamp(row[2]),
            "updatedLabel": _format_timestamp(row[3]),
        }
        for row in rows
    ]


def rename_chat(db_path: Path, chat_id: int, new_title: str) -> None:
    with _connect(db_path) as conn:
        _ensure_chats_table(conn)
        conn.execute(
            "UPDATE chats SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_title, chat_id),
        )


def delete_chat(db_path: Path, chat_id: int) -> None:
    with _connect(db_path) as conn:
        _ensure_chats_table(conn)
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))


def chat_library_payload(db_path: Path) -> dict[str, Any]:
    try:
        rows = get_all_chats(db_path)
        notice = None
    except sqlite3.Error as exc:
        # Recoverable inline message, matching ChatLibraryBridge's own
        # try/except around get_all_chats - the surface stays up rather
        # than the whole dialog erroring out.
        rows = []
        notice = f"Could not load saved chats: {exc}"
    return {"rows": rows, "notice": notice}


def register_chat_library(bus: SessionBus, db_path: Path | None = None) -> None:
    resolved_path = db_path if db_path is not None else DEFAULT_DB_PATH

    bus.register_topic("app-chat-library", lambda: chat_library_payload(resolved_path))

    async def rename(chat_id: int, new_title: str):
        # Non-empty guard matches the legacy `if ok and new_title:` - an
        # empty/whitespace title is ignored, no mutation, no error (the SPA
        # disables Save for an empty draft anyway).
        title = str(new_title or "").strip()
        if not title:
            return
        rename_chat(resolved_path, int(chat_id), title)
        await bus.publish("app-chat-library")

    async def delete(chat_id: int):
        # The SPA only calls this after its own two-step confirm, so no
        # confirmation happens here - same contract as the legacy bridge.
        delete_chat(resolved_path, int(chat_id))
        await bus.publish("app-chat-library")

    bus.register_intent("app-chat-library", "renameChat", rename)
    bus.register_intent("app-chat-library", "deleteChat", delete)
