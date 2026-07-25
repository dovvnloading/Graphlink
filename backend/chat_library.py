"""Chat library dialog: list/rename/delete/load (Qt-removal plan R2.5e + R6.4).

An INDEPENDENT Qt-free reimplementation of ChatDatabase.get_all_chats()/
rename_chat()/delete_chat()/load_chat()/load_notes()/load_pins() - not an
import - because graphlink_session/__init__.py eagerly imports
ChatSessionManager and SaveWorkerThread (workers.py imports
PySide6.QtCore.QThread/Signal) before graphlink_session.database can ever be
imported cleanly: Python always runs a package's __init__.py first, even for
`from graphlink_session.database import ChatDatabase`. ChatDatabase itself
(graphlink_session/database.py) is Qt-free; only the package wrapper around
it is hazardous. Same reimplement-not-import precedent as backend/composer.py
and backend/plugins.py.

Reads/writes the SAME real ~/.graphlink/chats.db file the legacy app uses
(same "chats"/"notes"/"pins" table schemas, same queries, same migration
ALTER TABLEs for older databases, same _format_timestamp display format moved
verbatim from graphlink_chat_library_bridge.py) - list, rename, delete, and
(R6.4) load are all genuinely real here. newChat has no backend counterpart
at all yet: creating a brand-new empty session is just "clear the canvas",
which will land alongside R6.5's save primitive, not this file.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.session_load import restore_chat_into_document

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


def _ensure_notes_table(conn: sqlite3.Connection) -> None:
    # R6.4: mirrors ChatDatabase.init_database()'s notes table + its own
    # is_system_prompt/is_summary_note migration ALTER TABLEs exactly - a
    # chats.db written by an OLDER legacy build may still be missing these
    # two columns, and load_notes_rows below unconditionally SELECTs them.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            position_x REAL NOT NULL,
            position_y REAL NOT NULL,
            width REAL NOT NULL,
            height REAL NOT NULL,
            color TEXT NOT NULL,
            header_color TEXT,
            FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
        )
        """
    )
    columns = [info[1] for info in conn.execute("PRAGMA table_info(notes)").fetchall()]
    if "is_system_prompt" not in columns:
        conn.execute("ALTER TABLE notes ADD COLUMN is_system_prompt INTEGER DEFAULT 0")
    if "is_summary_note" not in columns:
        conn.execute("ALTER TABLE notes ADD COLUMN is_summary_note INTEGER DEFAULT 0")


def _ensure_pins_table(conn: sqlite3.Connection) -> None:
    # R6.4: mirrors ChatDatabase.init_database()'s pins table + its own
    # pin_id/sort_order/anchor_item_id/created_at migration ALTER TABLEs.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            note TEXT,
            position_x REAL NOT NULL,
            position_y REAL NOT NULL,
            FOREIGN KEY (chat_id) REFERENCES chats (id) ON DELETE CASCADE
        )
        """
    )
    columns = [info[1] for info in conn.execute("PRAGMA table_info(pins)").fetchall()]
    if "pin_id" not in columns:
        conn.execute("ALTER TABLE pins ADD COLUMN pin_id TEXT")
    if "sort_order" not in columns:
        conn.execute("ALTER TABLE pins ADD COLUMN sort_order INTEGER DEFAULT 0")
    if "anchor_item_id" not in columns:
        conn.execute("ALTER TABLE pins ADD COLUMN anchor_item_id TEXT")
    if "created_at" not in columns:
        conn.execute("ALTER TABLE pins ADD COLUMN created_at TEXT")


def get_all_chats(db_path: Path) -> list[dict[str, Any]]:
    # closing() + the connection's own transaction context: sqlite3's
    # `with conn:` commits/rolls back but does NOT close the connection -
    # without closing() the handle would linger until garbage collection.
    with contextlib.closing(_connect(db_path)) as conn, conn:
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
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure_chats_table(conn)
        conn.execute(
            "UPDATE chats SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_title, chat_id),
        )


def delete_chat(db_path: Path, chat_id: int) -> None:
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure_chats_table(conn)
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))


def load_chat_row(db_path: Path, chat_id: int) -> dict[str, Any] | None:
    """Mirrors ChatDatabase.load_chat exactly: {"title", "data"} with `data`
    already json.loads()'d, or None if the id doesn't exist (a chat deleted
    from another window/process between the library listing and this call -
    the caller shows a real notice, not a crash)."""
    import json

    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure_chats_table(conn)
        row = conn.execute("SELECT title, data FROM chats WHERE id = ?", (chat_id,)).fetchone()
    if row is None:
        return None
    return {"title": row[0], "data": json.loads(row[1])}


def load_notes_rows(db_path: Path, chat_id: int) -> list[dict[str, Any]]:
    """Mirrors ChatDatabase.load_notes exactly - see that method's own
    SELECT column list; shape matches what backend/session_load.py's
    _restore_notes expects (nested "position"/"size" dicts)."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure_notes_table(conn)
        rows = conn.execute(
            """
            SELECT content, position_x, position_y, width, height,
                   color, header_color, is_system_prompt, is_summary_note
            FROM notes WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchall()
    return [
        {
            "content": row[0],
            "position": {"x": row[1], "y": row[2]},
            "size": {"width": row[3], "height": row[4]},
            "color": row[5],
            "header_color": row[6],
            "is_system_prompt": bool(row[7]),
            "is_summary_note": bool(row[8]),
        }
        for row in rows
    ]


def load_pins_rows(db_path: Path, chat_id: int) -> list[dict[str, Any]]:
    """Mirrors ChatDatabase.load_pins exactly, including its own
    sort_order-defaults-to-enumerate-index fallback for pre-migration rows."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        _ensure_pins_table(conn)
        rows = conn.execute(
            """
            SELECT pin_id, title, note, position_x, position_y,
                   anchor_item_id, sort_order, created_at
            FROM pins WHERE chat_id = ?
            ORDER BY sort_order, id
            """,
            (chat_id,),
        ).fetchall()
    return [
        {
            "pin_id": row[0],
            "title": row[1],
            "note": row[2],
            "position": {"x": row[3], "y": row[4]},
            "anchor_item_id": row[5],
            "sort_order": row[6] if row[6] is not None else index,
            "created_at": row[7],
        }
        for index, row in enumerate(rows)
    ]


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


def register_chat_library(
    bus: SessionBus,
    db_path: Path | None = None,
    canvas_document: SceneDocument | None = None,
    notifications: NotificationState | None = None,
) -> None:
    resolved_path = db_path if db_path is not None else DEFAULT_DB_PATH

    bus.register_topic("app-chat-library", lambda: chat_library_payload(resolved_path))

    # Writes run in worker threads (asyncio.to_thread) so a slow disk/WAL
    # commit never stalls the event loop. No Python-side lock is needed:
    # each call opens, uses, and closes its own connection inside one thread
    # (satisfying check_same_thread), and sqlite's file locking (timeout=30
    # in _connect) serializes concurrent writers. The topic builder's read
    # stays on the loop - a few-row SELECT, negligible.

    async def rename(chat_id: int, new_title: str):
        # Non-empty guard matches the legacy `if ok and new_title:` - an
        # empty/whitespace title is ignored, no mutation, no error (the SPA
        # disables Save for an empty draft anyway).
        title = str(new_title or "").strip()
        if not title:
            return
        await asyncio.to_thread(rename_chat, resolved_path, int(chat_id), title)
        await bus.publish("app-chat-library")

    async def delete(chat_id: int):
        # The SPA only calls this after its own two-step confirm, so no
        # confirmation happens here - same contract as the legacy bridge.
        await asyncio.to_thread(delete_chat, resolved_path, int(chat_id))
        await bus.publish("app-chat-library")

    async def load_chat(chat_id: int):
        # R6.4: replicates ChatSessionManager.load_chat's own orchestration
        # order (load row -> load notes/pins -> restore) and
        # SceneDeserializer._handle_load_error's "notification, not a
        # crash" posture for anything that goes wrong - canvas_document/
        # notifications are only None in a test harness that didn't wire
        # them; a real running session always has both (see backend/app.py's
        # _configure_session ordering).
        try:
            row = await asyncio.to_thread(load_chat_row, resolved_path, int(chat_id))
            if row is None:
                if notifications is not None:
                    notifications.show("This chat could not be found. It may have already been deleted.", "error")
                    await bus.publish("notification")
                return

            notes_rows = await asyncio.to_thread(load_notes_rows, resolved_path, int(chat_id))
            pins_rows = await asyncio.to_thread(load_pins_rows, resolved_path, int(chat_id))

            if canvas_document is None:
                return

            restore_chat_into_document(canvas_document, row, notes_rows, pins_rows)
        except Exception as exc:
            # Adversarial review finding: load_notes_rows/load_pins_rows (a
            # real sqlite3.Error, e.g. a locked/corrupted db file) previously
            # had no safety net here, unlike load_chat_row's None-check and
            # restore_chat_into_document's own try/except - it would
            # propagate uncaught out of dispatch_intent. Since the frontend's
            # loadChat call is fire-and-forget (no msg_id), the generic WS-
            # level error reply that DOES still get sent lands nowhere the
            # user can see (console.error only) - the dialog just closes and
            # goes silent. Wrapping the whole load sequence, not just the
            # restore step, guarantees a real, visible notification for
            # every failure mode here, matching legacy's own
            # _handle_load_error posture (one catch-all around the entire
            # load, not per-step).
            if notifications is not None:
                notifications.show(f"Failed to load the chat session. It may be corrupted.\nError: {exc}", "error")
                await bus.publish("notification")
            return

        await bus.publish("scene")
        if notifications is not None:
            title = str(row.get("title") or "chat")
            notifications.show(f'Loaded "{title}".', "success")
            await bus.publish("notification")

    bus.register_intent("app-chat-library", "renameChat", rename)
    bus.register_intent("app-chat-library", "deleteChat", delete)
    bus.register_intent("app-chat-library", "loadChat", load_chat)
