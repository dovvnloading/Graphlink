"""Chat library dialog: list/rename/delete/load/save/new (Qt-removal plan
R2.5e + R6.4 + R6.5).

An INDEPENDENT Qt-free reimplementation of ChatDatabase.get_all_chats()/
rename_chat()/delete_chat()/load_chat()/load_notes()/load_pins()/
save_chat_atomically() - not an import - because graphlink_session/
__init__.py eagerly imports ChatSessionManager and SaveWorkerThread
(workers.py imports PySide6.QtCore.QThread/Signal) before graphlink_session.
database can ever be imported cleanly: Python always runs a package's
__init__.py first, even for `from graphlink_session.database import
ChatDatabase`. ChatDatabase itself (graphlink_session/database.py) is
Qt-free; only the package wrapper around it is hazardous. Same
reimplement-not-import precedent as backend/composer.py and
backend/plugins.py.

Reads/writes the SAME real ~/.graphlink/chats.db file the legacy app uses
(same "chats"/"notes"/"pins" table schemas, same queries, same migration
ALTER TABLEs for older databases, same _format_timestamp display format moved
verbatim from graphlink_chat_library_bridge.py) - list, rename, delete,
load, save, and new are ALL genuinely real here as of R6.5.

R6.5's save_chat_atomically_row mirrors ChatDatabase.save_chat_atomically
exactly: ONE shared connection for the chats-row write (UPDATE if a
current_chat_id is set, else INSERT) plus a full delete-then-reinsert-all of
notes and pins, all committed together - see that method's own docstring
(database.py:271-289) for why a single transaction matters (three separate
connections, as the now-dead save_chat/update_chat/save_notes/save_pins
methods used, left chat/notes/pins inconsistent on a mid-sequence crash).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data

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


def save_chat_atomically_row(
    db_path: Path,
    chat_id: int | None,
    title: str,
    chat_data: dict[str, Any],
    notes_data: list[dict[str, Any]],
    pins_data: list[dict[str, Any]],
) -> int:
    """Mirrors ChatDatabase.save_chat_atomically exactly (database.py:271-
    315): ONE shared connection - UPDATE if `chat_id` is truthy, else INSERT
    (the SQLite AUTOINCREMENT rowid becomes the new chat's id) - then an
    unconditional full delete-then-reinsert of notes and pins for the
    resolved id, all inside the SAME transaction (Python's sqlite3 `with
    conn:` commits everything together, or rolls all of it back on any
    exception - never a partial chat/notes/pins write). `chat_data` here is
    the dict AFTER notes_data/pins_data have already been popped out by the
    caller (mirrors _prepare_chat_payload's own pop, done once at the
    boundary rather than inside this function)."""
    chat_data_json = json.dumps(chat_data)
    with contextlib.closing(_connect(db_path)) as conn:
        _ensure_chats_table(conn)
        _ensure_notes_table(conn)
        _ensure_pins_table(conn)
        with conn:
            if chat_id:
                conn.execute(
                    "UPDATE chats SET title = ?, data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (title, chat_data_json, chat_id),
                )
                resolved_chat_id = chat_id
            else:
                cursor = conn.execute(
                    "INSERT INTO chats (title, data, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (title, chat_data_json),
                )
                resolved_chat_id = cursor.lastrowid

            conn.execute("DELETE FROM notes WHERE chat_id = ?", (resolved_chat_id,))
            for note in notes_data:
                position = note.get("position") if isinstance(note.get("position"), dict) else {}
                size = note.get("size") if isinstance(note.get("size"), dict) else {}
                conn.execute(
                    """
                    INSERT INTO notes (
                        chat_id, content, position_x, position_y,
                        width, height, color, header_color, is_system_prompt, is_summary_note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_chat_id,
                        str(note.get("content", "")),
                        float(position.get("x", 0.0)),
                        float(position.get("y", 0.0)),
                        float(size.get("width", 0.0)),
                        float(size.get("height", 0.0)),
                        str(note.get("color") or "#4a7c59"),
                        note.get("header_color"),
                        1 if note.get("is_system_prompt") else 0,
                        1 if note.get("is_summary_note") else 0,
                    ),
                )

            conn.execute("DELETE FROM pins WHERE chat_id = ?", (resolved_chat_id,))
            for index, pin in enumerate(pins_data):
                position = pin.get("position") if isinstance(pin.get("position"), dict) else {}
                conn.execute(
                    """
                    INSERT INTO pins (
                        chat_id, title, note, position_x, position_y,
                        pin_id, sort_order, anchor_item_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_chat_id,
                        str(pin.get("title", "")),
                        pin.get("note"),
                        float(position.get("x", 0.0)),
                        float(position.get("y", 0.0)),
                        pin.get("pin_id"),
                        pin.get("sort_order", index),
                        pin.get("anchor_item_id"),
                        pin.get("created_at"),
                    ),
                )

        return resolved_chat_id


_FALLBACK_TITLE_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _fallback_title(seed_message: str) -> str:
    """Byte-for-byte port of SaveWorkerThread._fallback_title (workers.py:
    28-37): first 5 regex-matched words of the seed message, space-joined,
    truncated to 80 chars; a literal "Chat {timestamp}" if the seed message
    yields no usable words at all (e.g. empty, or punctuation-only)."""
    words = _FALLBACK_TITLE_WORD_RE.findall(str(seed_message or ""))
    if words:
        title = " ".join(words[:5]).strip()
        if title:
            return title[:80]
    return f"Chat {datetime.now().strftime('%Y%m%d_%H%M%S')}"


def _resolve_seed_message(document: SceneDocument) -> str:
    """Deliberate simplification of ChatSessionManager.save_current_chat's
    own `next((node for node in reversed(scene.nodes) if node.text), None)`
    (manager.py:108) - legacy's `.text` is a generic attribute several
    different live Qt widget classes each define with their own meaning;
    this backend has no single equivalent across all 12 node kinds. Since
    this text only ever seeds a cosmetic fallback title (see this module's
    own docstring on skipping the LLM title-generation call entirely), the
    dominant "has real conversational text" kind - chat - is a reasonable,
    bounded stand-in: the last chat-kind node's content, in creation order,
    or "New Chat" if the canvas has no chat node at all yet."""
    last_chat_content = None
    for node in document.nodes.values():
        if node.kind == "chat" and node.content:
            last_chat_content = node.content
    return last_chat_content if last_chat_content is not None else "New Chat"


def _content_digest(chat_data: dict[str, Any], notes_data: list, pins_data: list) -> str:
    """A pure function of "what would actually get written" - the change-guard
    autosave skips redundant writes with. Lives here, next to the save
    primitives it digests the output of, rather than in backend/autosave.py:
    saveChat and loadChat both need to record a digest too (see
    _new_save_state below), and autosave.py already imports FROM this module,
    never the reverse.

    sort_keys makes this independent of dict insertion order (build_chat_data's
    own key order never changes call to call, but this is cheap insurance
    against a false "changed" from something that isn't); default=str tolerates
    any value json can't natively encode without ever raising (a digest
    mismatch on an unexpected type just means "assume changed, write it" - the
    safe direction to fail in, never "assume unchanged, skip a real write")."""
    payload = json.dumps(
        {"chat_data": chat_data, "notes_data": notes_data, "pins_data": pins_data},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _new_save_state() -> dict[str, Any]:
    """The one cell tracking "what is currently on disk for this session",
    shared by every path that writes or reads a chat row: autosave's tick,
    the manual saveChat, and loadChat.

    Audit finding (this used to be a closure-local in register_autosave that
    NOTHING else could reach, while autosave.py's own docstring claimed it
    covered manual saves too). Two real consequences of that, both fixed by
    making it shared and by tracking chat_id alongside the digest:

    1. A manual Save or a loadChat left the cell stale, so the very next tick
       rewrote a byte-identical row - bumping updated_at and re-sorting the
       Chat Library (get_all_chats orders by updated_at DESC) under the user
       for no reason.
    2. The guard compared CONTENT only, but chats.db is shared mutable state.
       Delete the currently-open chat from the library and keep working
       without touching the canvas: the document's digest never changes, so
       every subsequent tick short-circuited and autosave silently stopped
       protecting the session entirely. Comparing chat_id too means a row
       that vanished underneath the session can no longer be mistaken for
       "already saved"."""
    return {"digest": None, "chat_id": None}


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
    *,
    autosave_interval_seconds: float | None = 30.0,
) -> None:
    resolved_path = db_path if db_path is not None else DEFAULT_DB_PATH

    bus.register_topic("app-chat-library", lambda: chat_library_payload(resolved_path))

    # Adversarial review finding: loadChat/saveChat/newChat all mutate the
    # SAME canvas_document and each awaits at least one asyncio.to_thread DB
    # call - an await point that yields control back to the event loop. A
    # session can have MULTIPLE attached WS connections at once (every tab/
    # window that doesn't pass its own ?session= query param shares
    # session="default" - see backend/app.py's ws_endpoint), so two tabs
    # racing Save/Load/New Chat could genuinely interleave mid-await and
    # silently overwrite or corrupt one another's work - there is no
    # per-window isolation here the way Qt's single-threaded-per-window
    # model gave legacy for free. This mutable flag - checked and set at
    # entry, cleared in a finally - serializes all three against each
    # other, the generalized (load/new included, not just save)
    # counterpart of ChatSessionManager's own _is_saving reentrancy guard.
    _mutation_in_progress = {"active": False}

    # R6.6 + audit fix: see _new_save_state's own docstring for what this
    # tracks and the two bugs that came from autosave owning it privately.
    _last_saved = _new_save_state()
    # Stashed on the bus for the same reason register_autosave stashes
    # bus.autosave_task: it is per-session state a caller may legitimately
    # need to observe, and a closure-local is unreachable to anything -
    # including the tests that have to prove the sharing actually works,
    # which is the whole point of the fix.
    bus.chat_save_state = _last_saved

    def _record_saved(
        chat_data: dict[str, Any], notes_data: list, pins_data: list, chat_id: int | None
    ) -> None:
        _last_saved["digest"] = _content_digest(chat_data, notes_data, pins_data)
        _last_saved["chat_id"] = int(chat_id) if chat_id is not None else None

    def _serialize_mutating_intent(handler):
        async def wrapped(*args, **kwargs):
            if _mutation_in_progress["active"]:
                if notifications is not None:
                    notifications.show("Another chat operation is already in progress. Please wait.", "warning")
                    await bus.publish("notification")
                return
            _mutation_in_progress["active"] = True
            try:
                await handler(*args, **kwargs)
            finally:
                _mutation_in_progress["active"] = False

        return wrapped

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
        if canvas_document is not None and canvas_document.current_chat_id == int(chat_id):
            # Audit fix: deleting the row this session is currently pointed at
            # used to leave current_chat_id dangling AND leave the autosave
            # digest looking "already saved", so a user who deleted their open
            # chat and kept working silently had no autosave protection at all
            # until they happened to edit the canvas. Dropping both makes the
            # next tick treat this as an unsaved session and INSERT a fresh
            # row - the same thing a manual Save already does here (save_chat's
            # own existing_row-is-None fallback).
            canvas_document.current_chat_id = None
            _last_saved["digest"] = None
            _last_saved["chat_id"] = None
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
            # R6.5: remember which row this scene now corresponds to, so a
            # later Save updates THIS row instead of always inserting a new
            # one - the backend analog of ChatSessionManager.current_chat_id
            # being set from the load path, not just the save path.
            canvas_document.current_chat_id = int(chat_id)
            # Audit fix: the document now matches this row exactly, so record
            # that. Without it the first tick after a load rewrote a
            # byte-identical row and bumped updated_at, re-sorting the Chat
            # Library under the user for a session they had only just opened.
            try:
                fresh = build_chat_data(canvas_document)
                fresh_notes = fresh.pop("notes_data", [])
                fresh_pins = fresh.pop("pins_data", [])
                _record_saved(fresh, fresh_notes, fresh_pins, chat_id)
            except Exception:
                # Never fail a successful load over bookkeeping - leaving the
                # digest unset just means one redundant tick, the pre-fix
                # behavior.
                _last_saved["digest"] = None
                _last_saved["chat_id"] = int(chat_id)
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

    async def save_chat():
        # R6.5: replicates ChatSessionManager.save_current_chat's own
        # orchestration (manager.py:83-133) - serialize -> resolve title/
        # chat_id -> one atomic DB write -> adopt the resolved chat_id.
        # Unlike legacy, this runs synchronously start-to-finish on the
        # event loop rather than handing off to a background QThread; the
        # _serialize_mutating_intent wrapper this is registered through
        # (see register_chat_library's own docstring above it) is this
        # function's actual reentrancy guard - see that comment for why one
        # is needed at all (a naive "nothing else runs during an await" -
        # this file's own ORIGINAL, WRONG assumption - does not hold once a
        # session can have more than one attached WS connection).
        if canvas_document is None:
            return

        has_any_nodes = bool(canvas_document.nodes)
        if not has_any_nodes and canvas_document.current_chat_id is None:
            # Mirrors save_current_chat's own "Nothing was added to the chat
            # canvas yet." guard (manager.py:94-96) - an empty, never-saved
            # canvas has nothing worth writing a row for.
            if notifications is not None:
                notifications.show("Nothing was added to the chat canvas yet.", "warning")
                await bus.publish("notification")
            return

        try:
            chat_data = build_chat_data(canvas_document)
        except Exception as exc:
            if notifications is not None:
                notifications.show(f"Failed to prepare chat save payload: {exc}", "error")
                await bus.publish("notification")
            return

        notes_data = chat_data.pop("notes_data", [])
        pins_data = chat_data.pop("pins_data", [])

        chat_id_for_save: int | None = None
        title: str
        current_id = canvas_document.current_chat_id
        if not current_id:
            title = _fallback_title(_resolve_seed_message(canvas_document))
        else:
            existing_row = await asyncio.to_thread(load_chat_row, resolved_path, int(current_id))
            if existing_row is not None:
                # Resaving an existing chat NEVER regenerates its title,
                # matching SaveWorkerThread.run()'s own `title = chat["title"]`
                # (workers.py:69) exactly.
                title = str(existing_row.get("title") or "Untitled")
                chat_id_for_save = int(current_id)
            else:
                # The row was deleted elsewhere between load and this save -
                # falls back to a fresh INSERT, matching legacy's own
                # tolerance for this race (workers.py:71-72) rather than
                # erroring.
                title = _fallback_title(_resolve_seed_message(canvas_document))

        try:
            new_chat_id = await asyncio.to_thread(
                save_chat_atomically_row, resolved_path, chat_id_for_save, title, chat_data, notes_data, pins_data,
            )
        except Exception as exc:
            if notifications is not None:
                notifications.show(f"Failed to save the chat session.\nError: {exc}", "error")
                await bus.publish("notification")
            return

        canvas_document.current_chat_id = int(new_chat_id)
        # Audit fix: record what this manual Save just put on disk, so the
        # next autosave tick recognizes it as already-saved instead of
        # rewriting a byte-identical row 30 seconds later.
        _record_saved(chat_data, notes_data, pins_data, new_chat_id)
        await bus.publish("app-chat-library")
        if notifications is not None:
            notifications.show(f'Saved "{title}".', "success")
            await bus.publish("notification")

    async def new_chat():
        # R6.5: the backend counterpart of legacy's "start with an empty
        # scene" - there is no legacy method this ports 1:1 (Qt's
        # ChatSessionManager has no "new chat" concept at all; a fresh scene
        # is just whatever exists before the first load/save of a session),
        # so this simply clears the live document and drops current_chat_id,
        # exactly like clear_for_load already does for a session LOAD.
        if canvas_document is None:
            return
        canvas_document.clear_for_load()
        # clear_for_load drops current_chat_id, so the save state that
        # described the old document no longer describes anything.
        _last_saved["digest"] = None
        _last_saved["chat_id"] = None
        await bus.publish("scene")

    bus.register_intent("app-chat-library", "renameChat", rename)
    bus.register_intent("app-chat-library", "deleteChat", delete)
    bus.register_intent("app-chat-library", "loadChat", _serialize_mutating_intent(load_chat))
    bus.register_intent("app-chat-library", "saveChat", _serialize_mutating_intent(save_chat))
    bus.register_intent("app-chat-library", "newChat", _serialize_mutating_intent(new_chat))

    if canvas_document is not None and autosave_interval_seconds is not None:
        # R6.6: local import - backend/autosave.py itself imports several
        # names FROM this module (_fallback_title/_resolve_seed_message/
        # load_chat_row/save_chat_atomically_row, so it can reuse them
        # rather than duplicating any save logic); importing it at this
        # module's own top level would be a circular import. Shares the
        # SAME _mutation_in_progress flag every manual intent above already
        # uses, so an autosave tick can never race a manual load/save/new.
        from backend.autosave import register_autosave

        register_autosave(
            bus, resolved_path, canvas_document, notifications, _mutation_in_progress, _last_saved,
            interval_seconds=autosave_interval_seconds,
        )
