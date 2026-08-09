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
import logging
import os
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from backend import db_backup
from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.session_load import restore_chat_into_document
from backend.asset_store import store_for
from backend.session_save import build_chat_data
from graphlink_migrations import run_sqlite_migrations

DEFAULT_DB_PATH = Path.home() / ".graphlink" / "chats.db"

# ADR-009 stage 9.2. How often, at most, a session's autosave tick (or a
# manual Save) is allowed to trigger a fresh backend/db_backup.py snapshot -
# see _maybe_backup_before_write's own docstring for the full cadence
# design (this same constant covers BOTH "before the first mutating write
# of a session" - the very first write always backs up immediately, since
# last_backup_at starts unset - AND the ongoing periodic cadence
# afterward, with no second timer). 10 minutes: frequent enough that a
# corruption discovered later never has to fall back past a handful of
# recent snapshots (bounded by backend/db_backup.py's own KEEP_MOST_RECENT
# at this cadence), infrequent enough that a long, active session doesn't
# spend disk churn re-backing-up a multi-tens-of-MB chats.db (embedded
# images can make a single chat's own row substantial) every single 30s
# autosave tick.
BACKUP_CADENCE_SECONDS = 600.0


class ConcurrentSaveConflict(RuntimeError):
    """Raised by save_chat_atomically_row when the caller supplied
    expected_updated_at but the UPDATE affected zero rows: another writer
    (a different session/window, or - pre ADR-004's single-instance model -
    a different process) already saved a newer version of this exact chat
    row since this session last loaded or saved it. The exit criterion this
    whole stage is built around ("a lost write race is surfaced, never
    clobbered") is enforced structurally, not just by convention: this is
    raised BEFORE either notes/pins DELETE statement runs (see that
    function's own body), so the entire write - including the row UPDATE
    itself - rolls back via the enclosing `with conn:` transaction. Nothing
    from this call is ever partially applied; the row a concurrent writer
    already committed is left completely untouched."""

# ADR-009 stage 9.1. PRAGMA user_version target for chats.db - bumped
# whenever a new migration function is added to _MIGRATIONS below. A
# genuinely fresh (never-opened) database reads user_version 0, so version
# "1" is the first real migration: it takes a brand-new DB from nothing to
# the full schema every _ensure_* probe used to (re)create piecemeal on
# every connection - see _migration_001_initial_schema's own docstring for
# exactly what that migration does and does not assume about the DB it's
# handed.
CHATS_DB_SCHEMA_VERSION = 1


# ADR-009 stage 9.2: `created_at`/rename_chat's own `updated_at` are written
# via SQLite's inline `CURRENT_TIMESTAMP` (second resolution, no fractional
# part) - the format every pre-9.2 row's timestamps are still in.
# save_chat_atomically_row's own `now` (see that function's own docstring)
# is now generated in PYTHON at microsecond resolution instead, specifically
# so two writes issued in close succession (the exact optimistic-concurrency
# scenario this stage exists for - two sessions racing a save) are NEVER
# indistinguishable the way two same-second CURRENT_TIMESTAMP values would
# be. Both formats are real, both must parse - _TIMESTAMP_DISPLAY_FORMATS is
# tried in order (second-resolution first, since it's still the more common
# case across created_at + every non-chat-save write).
_TIMESTAMP_DISPLAY_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f")


def _parse_stored_timestamp(value: Any) -> datetime | None:
    raw = str(value)
    for fmt in _TIMESTAMP_DISPLAY_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _format_timestamp(value: Any) -> str:
    """Moved verbatim from graphlink_chat_library_bridge.py, extended for
    ADR-009 stage 9.2 - see _TIMESTAMP_DISPLAY_FORMATS' own comment for why
    a second format is now tried. Unparseable/empty values echo back
    unchanged, matching the legacy behavior exactly."""
    if not value:
        return "Unknown"
    parsed = _parse_stored_timestamp(value)
    if parsed is None:
        return str(value)
    return parsed.strftime("%b %d, %Y %I:%M %p")


def _format_timestamp_iso(value: Any) -> str | None:
    """R8a: the Chat Library redesign groups rows by date (Today/Yesterday/
    Previous 7 Days/...), which needs a real, parseable instant - the
    display label from _format_timestamp above is deliberately locale/human
    formatted and not meant to be parsed back. None (not a sentinel string)
    on anything unparseable/empty, so the frontend can cleanly bucket those
    rows as "Unknown" rather than crash on a bad date. Extended for ADR-009
    stage 9.2 - see _TIMESTAMP_DISPLAY_FORMATS' own comment."""
    if not value:
        return None
    parsed = _parse_stored_timestamp(value)
    return parsed.isoformat() if parsed is not None else None


_PREVIEW_MAX_CHARS = 140


def _extract_preview_and_message_count(chat_data: dict[str, Any]) -> tuple[str, int]:
    """R8a: a one-line snippet (the last chat message) + total message count
    for the redesigned Chat Library list. Deliberately computed HERE, at
    save time, from the SAME chat_data dict already about to be
    json.dumps'd - not parsed back out of `data` at list-read time in
    get_all_chats, which would mean loading every row's full JSON blob
    (images can be embedded as base64 bytes inside it - see
    backend/canvas.py's _process_content_for_serialization) just to render
    a list of titles. This is effectively free: no extra I/O, no extra
    parsing, just a pass over a dict already in memory.

    raw_content is a plain string for a text-only message, or a list of
    content-part dicts (`_serialize_chat_node`) for a multimodal one - only
    the "text" parts of the latter contribute to the preview, matching what
    a user actually reads as the message."""
    chat_nodes = [
        node for node in chat_data.get("nodes", [])
        if isinstance(node, dict) and node.get("node_type") == "chat"
    ]
    last_content = chat_nodes[-1].get("raw_content") if chat_nodes else None
    if isinstance(last_content, list):
        text = " ".join(
            str(part.get("text", "")) for part in last_content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    else:
        text = str(last_content or "")
    preview = " ".join(text.split())[:_PREVIEW_MAX_CHARS]
    return preview, len(chat_nodes)


def _connect(
    db_path: Path, *, notifications: NotificationState | None = None, _retry: bool = False,
) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA foreign_keys = ON")
        # These two PRAGMAs are inside the SAME try/except as the connect()
        # call above (not split off into their own block further down) -
        # empirically, "file is not a database" surfaces on the FIRST real
        # touch of the file's header, which for a fresh Python-level
        # connect() is exactly here (PRAGMA journal_mode is the earliest
        # statement in this function that forces SQLite to actually read
        # the file), not necessarily at connect() itself (SQLite's own
        # connect is lazy - see this function's own corruption-handling
        # comment above for the empirical verification this rests on).
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
    except sqlite3.OperationalError:
        # ADR-009 stage 9.2: a locked/busy file or a real disk I/O error -
        # genuinely transient conditions (a concurrent writer, a slow
        # disk), never evidence of corruption. sqlite3.OperationalError is
        # - verified directly, not assumed, see backend/tests/
        # test_chat_library.py's own hierarchy-pinning test - a SUBCLASS of
        # sqlite3.DatabaseError, so it must be caught and re-raised HERE,
        # strictly before the broader except clause below, or a plain lock
        # timeout would be wrongly treated as a corrupt file and quarantined.
        # Closed for the same "never leak an open handle on a raise path"
        # reason as the DatabaseError branch below.
        if conn is not None:
            conn.close()
        raise
    except sqlite3.DatabaseError as exc:
        # Genuine corruption ("file is not a database" / "database disk
        # image is malformed") - both confirmed empirically (not from
        # documentation alone) to raise the base sqlite3.DatabaseError
        # itself, never a locked/busy-shaped subclass, which is exactly
        # what makes the except-order above safe to rely on. _retry bounds
        # this to at most ONE rescue attempt per call: if the file is STILL
        # unopenable immediately after quarantine+restore (quarantine
        # itself failed, or there was no backup to restore and something
        # keeps re-corrupting it), this re-raises for real rather than
        # recursing forever.
        #
        # MUST close conn here, before _rescue_corrupt_chats_db attempts to
        # rename db_path out from under it - verified empirically (not
        # assumed): on Windows, renaming a file with an open handle raises
        # WinError 32 ("the process cannot access the file because it is
        # being used by another process"), which would make the rescue
        # itself fail every single time on this platform if conn were left
        # open. sqlite3.connect() succeeding is exactly the case that
        # leaves `conn` non-None here even though a LATER statement in the
        # same try block is what actually raised.
        if conn is not None:
            conn.close()
        if _retry:
            raise
        _rescue_corrupt_chats_db(db_path, exc, notifications)
        return _connect(db_path, notifications=notifications, _retry=True)
    # ADR-004 stage 4.4 follow-up (adversarial-review finding): WAL mode,
    # not SQLite's default rollback-journal - set inside the try block
    # above, not here (see this function's own corruption-handling comment
    # for why). journal_mode is a database-level setting persisted in the
    # file header, not a per-connection default - this PRAGMA only needs to
    # actually FLIP the mode once ever (every later connection, including
    # from a pre-existing chats.db, inherits it automatically; re-issuing
    # it when already WAL is a cheap no-op). The rollback journal
    # materializes a `<db>-journal` sidecar ONLY transiently, mid-
    # transaction (SQLite creates and deletes it around each write with no
    # Python-level hook to chmod it before it's gone), which meant it was
    # the one piece of ADR-004 stage 4.4's own permission hardening that
    # couldn't be closed.
    #
    # WAL's `<db>-wal`/`<db>-shm` sidecars behave differently, verified
    # empirically (this module opens-does-work-closes a fresh connection
    # per call, never holding one open, so the exact lifecycle mattered):
    # once a database is GENUINELY already in WAL mode, connecting to it
    # again - even for a pure read, before any write - immediately
    # re-attaches both sidecars, which is exactly when the chmod loop
    # below (positioned right after this PRAGMA) catches them. They still
    # get removed when the sole connection closes, same as the rollback-
    # journal case, but the WINDOW during which they exist now starts
    # right when the loop below runs rather than only after a caller's own
    # later write - so this closes the gap for the entire steady-state
    # lifetime of a chats.db, not just some of it. One narrow, accepted
    # exception remains: the VERY FIRST connection that ever switches a
    # given chats.db into WAL mode needs an actual write before the
    # sidecars exist at all, by which point this loop has already run and
    # found nothing - a one-time-ever, single-connection window per
    # database (pinned explicitly by
    # TestChatsDbUsesWalModeForChmoddableSidecars's own bootstrap-gap test
    # in backend/tests/test_chat_library.py), not a persistent exposure.
    #
    # This also happens to be a piece of ADR-009 stage 9.1's own planned
    # "sqlite hygiene: WAL mode" bullet, done ahead of that stage's larger
    # bundle (user_version/migration runner/FK indexes/one-time DDL) since
    # it's purely additive and doesn't block any of that later work.
    #
    # busy_timeout (also moved into the try block above): an explicit
    # PRAGMA so a writer that finds chats.db locked by another connection
    # (e.g. autosave's tick and a manual Save's own DB call briefly
    # overlapping under WAL) retries for a while before raising "database
    # is locked", instead of failing immediately. Deliberately NOT the ADR
    # text's own illustrative "e.g. 5000ms" and deliberately set to 30000,
    # matching (not shortening) the existing 30-second convention this
    # module's own AUTOSAVE_YIELD_TIMEOUT_SECONDS docstring and several
    # tests already reason about explicitly as "sqlite's own 30s lock
    # timeout" - self-documenting and independently correct here even
    # though it is also, empirically, already what this function's own
    # `timeout=30` connect() kwarg sets via the same underlying C API.
    #
    # chats.db holds real conversation content, POSIX 0600 like
    # session.dat (graphlink_settings_store.py's own SettingsManager).
    # sqlite3.connect()/the PRAGMA above create these files with no mode
    # parameter exposed anywhere in the stdlib API, so there is no earlier
    # hook than right here to fix them up - and since EVERY read/write
    # helper in this file goes through this one shared function
    # (get_all_chats, rename_chat, delete_chat, load_chat_row,
    # load_notes_rows, load_pins_rows, save_chat_atomically_row), doing it
    # here rather than at each of those call sites both closes the gap for
    # new files and self-heals a pre-existing chats.db (and its sidecars)
    # on their very next connection - unconditional, not "only if just
    # created". No-op on Windows (see SettingsManager's own __init__
    # comment for why POSIX permission bits don't apply there).
    for path in (db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
        if path.exists():
            try:
                os.chmod(path, 0o600)
            except OSError:
                logger.warning("could not chmod %s to 0600 - continuing with existing permissions", path)

    # ADR-009 stage 9.1: runs on EVERY connect, not just the first ever, but
    # is a cheap no-op (a single "PRAGMA user_version" read, no transaction
    # opened, no other statement executed) once this database is already at
    # CHATS_DB_SCHEMA_VERSION - see run_sqlite_migrations' own docstring for
    # why that no-op path is safe to leave on the hot path. This is what
    # makes the old "_ensure_*, re-probed on every call" pattern this
    # replaces unreachable on a normal connect: schema creation now happens
    # AT MOST ONCE per database, ever, the moment it first falls behind
    # target - never again after that. Positioned after the chmod loop
    # above (not before) so the very-first-ever-WAL-connection bootstrap gap
    # documented on that loop's own comment is unaffected: chmod still runs
    # before this migration's first real write to a brand-new db_path.
    #
    # ADR-009 stage 9.2: wrapped in the same corruption try/except shape as
    # the connect+PRAGMA block above - a corrupt page can just as easily
    # surface here (e.g. PRAGMA table_info reading a malformed page) as at
    # the earlier PRAGMAs, and this call site needs its own `conn.close()`
    # first (the earlier block never got as far as opening one).
    try:
        run_sqlite_migrations(conn, CHATS_DB_SCHEMA_VERSION, _MIGRATIONS)
    except sqlite3.OperationalError:
        conn.close()
        raise
    except sqlite3.DatabaseError as exc:
        conn.close()
        if _retry:
            raise
        _rescue_corrupt_chats_db(db_path, exc, notifications)
        return _connect(db_path, notifications=notifications, _retry=True)
    return conn


def _quarantine_corrupt_chats_db(db_path: Path, error: Exception) -> Path | None:
    """Mirrors graphlink_settings_store.py's own _backup_corrupt_state_file
    EXACTLY on the naming/permission convention: the same ISO8601-compact-
    UTC timestamp format (`strftime("%Y%m%dT%H%M%SZ")`), the same
    `Path.replace()` rename (a rename, not a copy-then-delete, so it
    preserves the source inode's mode bits and is atomic - the corrupt file
    is either still at db_path or already fully at quarantine_path, never
    briefly duplicated or lost), and the same explicit chmod 0600
    afterward (a corrupt chats.db can hold the same real conversation
    content the live file did, and is kept indefinitely "for forensic
    recovery" - same reasoning as that function's own comment on why it
    needs its own explicit permissioning independent of the source file's).

    NOT extracted into a module BOTH files import: the two call sites
    differ in one real respect (chats.db has WAL sidecars to clean up
    afterward; session.dat does not), and graphlink_settings_store.py is a
    root-level module SettingsManager already depends on directly - adding
    a NEW shared dependency between it and backend/chat_library.py for
    ~15 lines of logic was judged a worse trade than duplicating this small
    amount of logic with this comment explaining why, matching this
    module's own established "reimplement, don't cross-import" precedent
    for exactly this kind of small, self-contained algorithm (see this
    file's own module docstring).

    Returns the quarantine path, or None if the rename itself failed (a
    permissions issue, or the file vanished between the caller detecting
    corruption and this call) - in which case NOTHING else is touched: see
    this function's own caller (_rescue_corrupt_chats_db) for why leaving
    an un-quarantined corrupt file exactly where it was is the safe
    direction to fail in, rather than attempting a restore that could
    collide with it."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_path = db_path.with_name(f"{db_path.name}.corrupted-{timestamp}")
    try:
        db_path.replace(quarantine_path)
    except OSError as quarantine_error:
        logger.error(
            "%s is corrupt (%s) and could not be quarantined (%s) - leaving it in place",
            db_path, error, quarantine_error,
        )
        return None
    try:
        os.chmod(quarantine_path, 0o600)
    except OSError:
        logger.warning("could not chmod %s to 0600 - continuing", quarantine_path)

    # The corrupt file's own -wal/-shm sidecars (if any survived whatever
    # crash caused the corruption) describe writes against THAT specific,
    # now-quarantined file's page layout - left in place, a later connect
    # to the freshly-restored db_path could find and try to replay them,
    # grafting unrelated transactions onto a completely different file's
    # content. Deleting them is safe: they are derived, recoverable-from-
    # nowhere-else-anyway state, never primary data - the corrupt MAIN file
    # (the one thing that might hold forensic value) is exactly what got
    # preserved above.
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                logger.warning("could not remove stale sidecar %s - continuing", sidecar)

    logger.error("%s was corrupt (%s) - quarantined to %s", db_path, error, quarantine_path)
    return quarantine_path


def _rescue_corrupt_chats_db(
    db_path: Path, error: Exception, notifications: NotificationState | None,
) -> None:
    """The chats.db analog of graphlink_settings_store.py's own
    _backup_corrupt_state_file - but genuinely BETTER, not just a reset to
    empty, per this stage's own ground rules: session.dat has no backup
    store to restore from and resets to defaults after quarantining (see
    that function's own docstring); chats.db has backend/db_backup.py's
    real retained snapshots, so this restores the newest good one instead,
    whenever one exists, rather than starting the user over from nothing.

    Called from _connect() itself, for EVERY caller in this module (the
    self-healing is transparent - a caller that hits this never sees the
    exception at all, unless quarantine+restore also fails). `notifications`
    is optional and None for most call sites (get_all_chats is the one
    exception - see that function's own comment for why it is safe to wire
    a live NotificationState through specifically there and not the
    others): a rescue is ALWAYS logged via logger.error regardless (durable
    in graphlink.log - see backend/crash_recovery.py's own docstring on
    every unhandled condition in this codebase landing somewhere durable),
    so silence from `notifications` here is never silence altogether."""
    quarantine_path = _quarantine_corrupt_chats_db(db_path, error)
    if quarantine_path is None:
        # Could not even move the corrupt file out of the way - leave
        # EVERYTHING else alone rather than attempting a restore that could
        # collide with a file we can't prove is actually gone from
        # db_path. The caller's own retry will hit the exact same
        # DatabaseError again and this time let it propagate for real
        # (_retry=True) - safer than silently overwriting an unquarantined
        # file.
        if notifications is not None:
            notifications.show(
                "Your chat history file (chats.db) appears to be corrupted and could not be "
                "automatically repaired. See graphlink.log for details.",
                "error",
            )
        return

    restored_from = db_backup.restore_from_newest_backup(db_path)
    if restored_from is not None:
        message = (
            "Your chat history file was corrupted and has been restored from a recent backup. "
            f"The corrupted file was saved as {quarantine_path.name} in your .graphlink folder in case "
            "you need it."
        )
    else:
        message = (
            "Your chat history file was corrupted and no backup was available, so a new, empty chat "
            f"library was started. The corrupted file was saved as {quarantine_path.name} in your "
            ".graphlink folder in case it can be recovered."
        )
    logger.error(
        "%s corruption rescue complete: quarantined=%s restored_from_backup=%s",
        db_path, quarantine_path, restored_from,
    )
    if notifications is not None:
        notifications.show(message, "warning")


def _migration_001_initial_schema(conn: sqlite3.Connection) -> None:
    """ADR-009 stage 9.1, migration "1" (0 -> 1): the one-time replacement
    for the old _ensure_chats_table/_ensure_notes_table/_ensure_pins_table
    trio, which used to run this exact DDL - CREATE TABLE IF NOT EXISTS +
    PRAGMA table_info + conditional ALTER TABLE - on EVERY single connect,
    from EVERY query function in this module. Landing on user_version = 1
    now does it once, ever, per database.

    Must be correct for BOTH of two starting shapes, since both are real:

      1. A genuinely fresh, empty chats.db (nothing in sqlite_master at
         all). This is the "0 -> 1" case the version number literally
         names - every CREATE TABLE below actually creates something.

      2. An EXISTING real chats.db that was created and evolved entirely by
         the OLD per-connection _ensure_* probes, which never once touched
         PRAGMA user_version - so it reads 0 today no differently than a
         truly empty database, even though every table and column below
         already exists and likely holds real chat rows. This is the
         upgrade path every actual user's machine takes the first time a
         build containing this migration runs - see
         TestMigrationUpgradesAPreExistingRealShapedDatabase in
         backend/tests/test_chat_library.py, which builds a database in
         exactly this shape by hand (raw DDL, real rows, user_version left
         at 0) and asserts the data survives byte-for-byte.

      Every statement below is IF NOT EXISTS or a guarded "column already
      there?" ALTER TABLE for exactly that reason - re-running this against
      an already-correct schema (case 2) must be a pure no-op on the tables/
      columns themselves, identical to what the old probes already
      guaranteed by construction.

    Schema is byte-for-byte what the three old _ensure_* functions produced
    together: chats (+ R8a's preview/message_count columns), notes (+ R6.4's
    is_system_prompt/is_summary_note columns), pins (+ R6.4's pin_id/
    sort_order/anchor_item_id/created_at columns) - plus this stage's own
    new work, CREATE INDEX IF NOT EXISTS on the two foreign-key columns that
    never had one (notes.chat_id, pins.chat_id - the schema's only two FK
    columns; chats itself declares no FK). Without these, delete_chat's
    "DELETE FROM chats WHERE id=?" - which cascades via ON DELETE CASCADE -
    and every load_notes_rows/load_pins_rows "WHERE chat_id=?" lookup did a
    full table scan of notes/pins instead of an index seek.

    Runs inside run_sqlite_migrations' own managed transaction (manual
    BEGIN/COMMIT/ROLLBACK around isolation_level=None - see that function's
    docstring for why `with conn:` alone is not atomic for DDL) - must not
    BEGIN, COMMIT, or ROLLBACK anything itself."""
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
    chats_columns = [info[1] for info in conn.execute("PRAGMA table_info(chats)").fetchall()]
    if "preview" not in chats_columns:
        conn.execute("ALTER TABLE chats ADD COLUMN preview TEXT DEFAULT ''")
    if "message_count" not in chats_columns:
        conn.execute("ALTER TABLE chats ADD COLUMN message_count INTEGER DEFAULT 0")

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
    notes_columns = [info[1] for info in conn.execute("PRAGMA table_info(notes)").fetchall()]
    if "is_system_prompt" not in notes_columns:
        conn.execute("ALTER TABLE notes ADD COLUMN is_system_prompt INTEGER DEFAULT 0")
    if "is_summary_note" not in notes_columns:
        conn.execute("ALTER TABLE notes ADD COLUMN is_summary_note INTEGER DEFAULT 0")

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
    pins_columns = [info[1] for info in conn.execute("PRAGMA table_info(pins)").fetchall()]
    if "pin_id" not in pins_columns:
        conn.execute("ALTER TABLE pins ADD COLUMN pin_id TEXT")
    if "sort_order" not in pins_columns:
        conn.execute("ALTER TABLE pins ADD COLUMN sort_order INTEGER DEFAULT 0")
    if "anchor_item_id" not in pins_columns:
        conn.execute("ALTER TABLE pins ADD COLUMN anchor_item_id TEXT")
    if "created_at" not in pins_columns:
        conn.execute("ALTER TABLE pins ADD COLUMN created_at TEXT")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_chat_id ON notes (chat_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pins_chat_id ON pins (chat_id)")


# Keyed by the version each function PRODUCES (migration "1" takes a
# database from 0 -> 1), matching graphlink_migrations' own ordering
# convention - see run_sqlite_migrations' docstring. The stage 9.2 agent
# building backups/corrupt-rescue on top of this: add step "2" here (and
# bump CHATS_DB_SCHEMA_VERSION to 2) for any further schema change, never
# renumber or replace step "1" - it must stay exactly what it is today so it
# keeps correctly upgrading every already-migrated real database.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migration_001_initial_schema,
}


def get_all_chats(db_path: Path, notifications: NotificationState | None = None) -> list[dict[str, Any]]:
    # ADR-009 stage 9.2: the ONE call in this module that threads a real
    # `notifications` reference into _connect()'s corruption rescue (every
    # other function below stays silent-except-log - see _rescue_corrupt_
    # chats_db's own docstring for why). This is the safest single spot to
    # wire it: get_all_chats has no OTHER notifications.show() call of its
    # own to race against (unlike loadChat/saveChat's own closures, which
    # already show a "Loaded"/"Saved" toast right after their own DB call -
    # a rescue notice set moments earlier would be silently overwritten
    # before ever being seen), and it backs the "app-chat-library" topic,
    # which is rebuilt on essentially every real user action in this
    # module (a fresh subscribe, and every rename/delete/save/load/new-chat
    # republish) - the single most-likely first real touch of chats.db
    # after a relaunch.
    #
    # closing() + the connection's own transaction context: sqlite3's
    # `with conn:` commits/rolls back but does NOT close the connection -
    # without closing() the handle would linger until garbage collection.
    with contextlib.closing(_connect(db_path, notifications=notifications)) as conn, conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at, preview, message_count "
            "FROM chats ORDER BY updated_at DESC"
        ).fetchall()
    return [
        {
            "id": int(row[0]),
            "title": str(row[1]),
            "createdLabel": _format_timestamp(row[2]),
            "updatedLabel": _format_timestamp(row[3]),
            "createdAtIso": _format_timestamp_iso(row[2]),
            "updatedAtIso": _format_timestamp_iso(row[3]),
            "preview": str(row[4] or ""),
            "messageCount": int(row[5] or 0),
        }
        for row in rows
    ]


def rename_chat(db_path: Path, chat_id: int, new_title: str) -> None:
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE chats SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_title, chat_id),
        )


def delete_chat(db_path: Path, chat_id: int) -> None:
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))


def load_chat_row(db_path: Path, chat_id: int) -> dict[str, Any] | None:
    """Mirrors ChatDatabase.load_chat, extended for ADR-009 stage 9.2:
    {"title", "data", "updated_at"} with `data` already json.loads()'d, or
    None if the id doesn't exist (a chat deleted from another window/
    process between the library listing and this call - the caller shows a
    real notice, not a crash).

    `updated_at` (new in stage 9.2) is the value optimistic concurrency is
    built on: the caller that loads a chat is expected to carry THIS exact
    string forward (see backend/chat_library.py's own register_chat_library
    loadChat closure, which seeds it into the shared last_saved cell) and
    hand it back as save_chat_atomically_row's expected_updated_at when it
    later saves - never re-read moments before that save, which would
    trivially always match and defeat the whole point of detecting a race
    against some OTHER writer that saved in between."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT title, data, updated_at FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()
    if row is None:
        return None
    return {"title": row[0], "data": json.loads(row[1]), "updated_at": row[2]}


def load_notes_rows(db_path: Path, chat_id: int) -> list[dict[str, Any]]:
    """Mirrors ChatDatabase.load_notes exactly - see that method's own
    SELECT column list; shape matches what backend/session_load.py's
    _restore_notes expects (nested "position"/"size" dicts)."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
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
    *,
    expected_updated_at: str | None = None,
) -> tuple[int, str]:
    """Mirrors ChatDatabase.save_chat_atomically (database.py:271-315): ONE
    shared connection - UPDATE if `chat_id` is truthy, else INSERT (the
    SQLite AUTOINCREMENT rowid becomes the new chat's id) - then an
    unconditional full delete-then-reinsert of notes and pins for the
    resolved id, all inside the SAME transaction (Python's sqlite3 `with
    conn:` commits everything together, or rolls all of it back on any
    exception - never a partial chat/notes/pins write). `chat_data` here is
    the dict AFTER notes_data/pins_data have already been popped out by the
    caller (mirrors _prepare_chat_payload's own pop, done once at the
    boundary rather than inside this function).

    Returns (resolved_chat_id, new_updated_at) - ADR-009 stage 9.2 extends
    the pre-9.2 bare-int return with the fresh updated_at this write just
    committed, so a caller can carry it forward as the NEXT save's own
    expected_updated_at (see this function's own optimistic-concurrency
    paragraph below for why that value must always come from a real write/
    load, never be re-derived independently).

    OPTIMISTIC CONCURRENCY (stage 9.2, the exit criterion this whole stage
    is built around): when `chat_id` is truthy AND `expected_updated_at` is
    given, the UPDATE is `WHERE id = ? AND updated_at = ?` instead of a
    blind `WHERE id = ?` - if some OTHER writer already committed a newer
    version of this row since the caller last loaded/saved it (the
    `updated_at` it is holding is stale), the UPDATE affects zero rows and
    this raises ConcurrentSaveConflict BEFORE either notes/pins DELETE
    statement below ever runs - so the whole write, not just the row
    UPDATE, rolls back via the enclosing `with conn:` transaction the
    instant that happens. Nothing here is ever partially applied, and the
    concurrent writer's own already-committed row is left byte-for-byte
    untouched. `expected_updated_at=None` (the default) skips this check
    entirely - a blind `WHERE id = ?`, byte-identical to this function's
    pre-9.2 behavior - for the (legitimate) case of a caller that has no
    real prior version to compare against (never loaded through
    load_chat_row for this session, or the INSERT branch below, which has
    no prior row to race against in the first place).

    `now` is generated in PYTHON (datetime.now(timezone.utc), microsecond
    resolution) rather than via SQLite's own inline `CURRENT_TIMESTAMP`
    function (this function's pre-9.2 behavior, and what rename_chat still
    uses) - SECOND resolution alone is not fine-grained enough for a real
    optimistic-concurrency token: two writes issued within the same wall-
    clock second (verified directly - this is not a theoretical concern;
    it is exactly what a fast two-session race, including this stage's own
    test suite driving two saves back-to-back with no real delay, produces)
    would otherwise share the identical CURRENT_TIMESTAMP string, making a
    genuinely stale expected_updated_at indistinguishable from a fresh one
    and silently defeating the whole check. Bound as an explicit parameter
    for the UPDATE/INSERT's own `updated_at` column - this also guarantees
    the exact string returned to the caller is the exact string that landed
    in the row, with no possibility of drift from a second, separate read-
    back after the write. See _TIMESTAMP_DISPLAY_FORMATS' own comment for
    how the display-formatting functions handle both this and the older,
    second-resolution format that pre-9.2 rows and rename_chat's own writes
    still use."""
    chat_data_json = json.dumps(chat_data)
    preview, message_count = _extract_preview_and_message_count(chat_data)
    with contextlib.closing(_connect(db_path)) as conn:
        with conn:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            if chat_id:
                if expected_updated_at is not None:
                    cursor = conn.execute(
                        "UPDATE chats SET title = ?, data = ?, preview = ?, message_count = ?, "
                        "updated_at = ? WHERE id = ? AND updated_at = ?",
                        (title, chat_data_json, preview, message_count, now, chat_id, expected_updated_at),
                    )
                    if cursor.rowcount == 0:
                        raise ConcurrentSaveConflict(
                            f"chat {chat_id} was modified elsewhere since it was last loaded/saved "
                            "in this session (expected updated_at "
                            f"{expected_updated_at!r} did not match)"
                        )
                else:
                    conn.execute(
                        "UPDATE chats SET title = ?, data = ?, preview = ?, message_count = ?, "
                        "updated_at = ? WHERE id = ?",
                        (title, chat_data_json, preview, message_count, now, chat_id),
                    )
                resolved_chat_id = chat_id
            else:
                cursor = conn.execute(
                    "INSERT INTO chats (title, data, preview, message_count, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (title, chat_data_json, preview, message_count, now),
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

        return resolved_chat_id, now


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


USER_OWNER = "user"
AUTOSAVE_OWNER = "autosave"

# How long a user-initiated intent will wait out an in-flight autosave tick
# before giving up and warning instead. A tick measures ~10-50ms even on a
# large canvas, so this is ~40x headroom; the only way to exceed it is a tick
# genuinely stuck on sqlite's own lock timeout, where a truthful "try again"
# beats a UI that appears to have frozen.
AUTOSAVE_YIELD_TIMEOUT_SECONDS = 2.0


def _busy_message(owner: str | None) -> str:
    """Audit finding: the single generic message named "another chat
    operation" even when the holder was a background autosave the user never
    started, which reads as a bug rather than as the app protecting itself."""
    if owner == AUTOSAVE_OWNER:
        return "Autosave is still finishing. Please try again in a moment."
    return "Another chat operation is already in progress. Please wait."


def _new_mutation_guard() -> dict[str, Any]:
    """The one definition of the guard's shape, so register_chat_library,
    backend/autosave.py and the tests can never drift apart on it.

    `released` is constructed with no running event loop on purpose - safe
    since 3.10 (Event binds its loop at wait() time, not construction), and
    register_chat_library is genuinely called outside a loop, which is the
    exact hazard that broke R6.6's own asyncio.create_task call."""
    return {"active": False, "owner": None, "released": asyncio.Event()}


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
       "already saved".

    ADR-009 stage 9.2 adds two more cells to this SAME shared dict, for the
    same "one shared home, never a closure-local" reason as the two above:

    3. "updated_at" - the exact value optimistic concurrency's
       expected_updated_at is read from before every save (see
       save_chat_atomically_row's own docstring). Sourced from a real
       load_chat_row read or a real save's own return value - NEVER a
       fresh re-read taken moments before the save that will use it, which
       would trivially always match and defeat the whole point of
       detecting a race against some OTHER writer.
    4. "last_backup_at" - a time.monotonic() timestamp of this session's
       last backend/db_backup.py snapshot, so _maybe_backup_before_write
       can implement BOTH "before the first mutating write of a session"
       (this starts at None, which always backs up immediately) and the
       ongoing periodic cadence afterward (see that function's own
       docstring) from ONE piece of state, with no second timer."""
    return {"digest": None, "chat_id": None, "updated_at": None, "last_backup_at": None}


# ADR-009 stage 9.2: shown via the SAME NotificationState channel every
# other user-visible chat-library warning already uses (backend/
# notifications.py) - both the manual saveChat closure and autosave_tick
# route through one of these two constants so the two surfaces never drift
# apart on wording. Deliberately distinct from _busy_message above (a
# TEMPORARY contention the guard already resolves on its own) - this is a
# genuine, not-self-resolving conflict: the user's own edit was NOT
# written, and nothing will retry it automatically in a way that could
# still lose the newer version, so the message says so plainly and points
# at the one safe recovery action (reload).
LOST_RACE_MESSAGE_MANUAL = (
    "This chat changed elsewhere. Your latest edit was not saved - reload the chat to see the "
    "current version before making further changes."
)
LOST_RACE_MESSAGE_AUTOSAVE = (
    "Autosave couldn't save - this chat changed elsewhere. Reload the chat to see the current "
    "version and avoid losing further edits."
)


def _maybe_backup_before_write(db_path: Path, last_saved: dict[str, Any]) -> None:
    """ADR-009 stage 9.2: the ONE call site both backup triggers the ADR
    text asks for route through - "before the first mutating write of a
    session" and "on a periodic cadence" are the SAME check on the SAME
    shared last_saved cell (see _new_save_state's own docstring for why
    last_backup_at lives there), not two separate mechanisms:

      - First write of a session: last_saved["last_backup_at"] is still
        None (_new_save_state's own initial value), so the condition below
        is unconditionally true - a backup is always taken before that
        write lands.
      - Every write after that: only once BACKUP_CADENCE_SECONDS have
        elapsed since the last one - reusing whatever clock already drove
        THIS call (a manual Save's own call, or an autosave tick that
        decided to write - see backend/autosave.py's own autosave_tick and
        this module's own save_chat closure, the two real callers), never
        a second, independently-scheduled timer task.

    Called from a synchronous context in both real callers (already inside
    an asyncio.to_thread worker thread, same as the DB write it precedes) -
    this function does real (bounded, local) file I/O itself and must
    never be awaited directly.

    A backup FAILURE (disk full, permissions) is logged and swallowed, not
    raised - a failed backup must never block the actual chat save it
    precedes; losing a redundancy layer is a strictly smaller problem than
    losing the user's actual edit over it. last_backup_at is still
    advanced on a failure, deliberately: retrying every single tick against
    a systemic problem (e.g. a genuinely full disk) would just waste cycles
    for the whole BACKUP_CADENCE_SECONDS window either way; the next
    natural write will try again."""
    now = time.monotonic()
    last_backup_at = last_saved.get("last_backup_at")
    if last_backup_at is not None and (now - last_backup_at) < BACKUP_CADENCE_SECONDS:
        return
    try:
        db_backup.take_backup(db_path)
    except Exception:
        logger.exception("chats.db backup failed - continuing with the write anyway")
    last_saved["last_backup_at"] = now


def make_serialize_mutating_intent(
    bus: SessionBus,
    mutation_in_progress: dict[str, Any],
    notifications: NotificationState | None,
):
    """Factory for the reentrancy-guard decorator register_chat_library's
    own loadChat/saveChat/newChat intents are registered through - see
    register_chat_library's own docstring (right below its call site) for
    the full OWNERSHIP audit-fix story this implements. Lifted out to a
    top-level factory - matching _new_mutation_guard/_new_save_state/
    _busy_message's own "one definition ... so it can never drift apart"
    precedent already established in this file - purely to keep
    register_chat_library itself under ADR-002's 300-line registration-
    function cap (stage 2.7). Captures nothing register_chat_library's own
    callers couldn't already reach via bus.chat_mutation_guard, which is
    the SAME dict passed in here as mutation_in_progress."""

    def _claim_guard(owner: str) -> None:
        mutation_in_progress["active"] = True
        mutation_in_progress["owner"] = owner
        mutation_in_progress["released"].clear()

    def _release_guard() -> None:
        mutation_in_progress["active"] = False
        mutation_in_progress["owner"] = None
        mutation_in_progress["released"].set()

    def _serialize_mutating_intent(handler):
        async def wrapped(*args, **kwargs):
            if mutation_in_progress["active"] and mutation_in_progress["owner"] == AUTOSAVE_OWNER:
                # Yield to the user: wait the tick out rather than discarding
                # their click. On timeout we fall through to the same
                # drop-and-warn below, so a stuck tick degrades to exactly the
                # pre-fix behavior instead of hanging.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        mutation_in_progress["released"].wait(),
                        timeout=AUTOSAVE_YIELD_TIMEOUT_SECONDS,
                    )

            if mutation_in_progress["active"]:
                # Re-checked, not assumed: several intents can be released
                # from the wait above at once, and only the first may claim.
                if notifications is not None:
                    notifications.show(_busy_message(mutation_in_progress["owner"]), "warning")
                    await bus.publish("notification")
                return

            _claim_guard(USER_OWNER)
            try:
                await handler(*args, **kwargs)
            finally:
                _release_guard()

        return wrapped

    return _serialize_mutating_intent


def chat_library_payload(db_path: Path, notifications: NotificationState | None = None) -> dict[str, Any]:
    try:
        rows = get_all_chats(db_path, notifications=notifications)
        notice = None
    except sqlite3.Error as exc:
        # Recoverable inline message, matching ChatLibraryBridge's own
        # try/except around get_all_chats - the surface stays up rather
        # than the whole dialog erroring out. Only reachable today for a
        # failure _connect()'s own corruption rescue could not recover from
        # (a genuinely unopenable file even after quarantine+restore, or a
        # real lock/IO error) - a plain corruption has already self-healed
        # silently by the time get_all_chats would ever raise.
        rows = []
        notice = f"Could not load saved chats: {exc}"
    return {"rows": rows, "notice": notice}


def make_load_chat(
    bus: SessionBus,
    resolved_path: Path,
    canvas_document: SceneDocument | None,
    notifications: NotificationState | None,
    record_saved: Callable[..., None],
    last_saved: dict[str, Any],
):
    """Factory for register_chat_library's own loadChat intent - lifted out
    to a top-level function purely to keep register_chat_library itself
    under ADR-002's 300-line registration-function cap (stage 2.7), the
    same "one definition, kept under the cap" precedent make_serialize_
    mutating_intent already established in this file (see that function's
    own docstring). Captures nothing register_chat_library's own callers
    couldn't already reach via bus.chat_save_state, which is the SAME dict
    passed in here as last_saved."""

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

            restore_chat_into_document(
                canvas_document, row, notes_rows, pins_rows,
                asset_store=store_for(resolved_path),
            )
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
                # Must use the SAME store as autosave below, or the
                # first tick after a load would see a payload that
                # differs only in image representation and rewrite a
                # row that is already correct.
                fresh = build_chat_data(canvas_document, asset_store=store_for(resolved_path))
                fresh_notes = fresh.pop("notes_data", [])
                fresh_pins = fresh.pop("pins_data", [])
                # ADR-009 stage 9.2: row["updated_at"] is the value that
                # will be carried forward as expected_updated_at on this
                # session's NEXT save of this chat - see load_chat_row's
                # own docstring for why it must come from exactly here
                # (this load), never re-read moments before that later
                # save.
                record_saved(fresh, fresh_notes, fresh_pins, chat_id, row.get("updated_at"))
            except Exception:
                # Never fail a successful load over bookkeeping - leaving the
                # digest unset just means one redundant tick, the pre-fix
                # behavior. updated_at is still recorded from the real row
                # (not lost to the same failure) so optimistic concurrency
                # for this session's next save stays correct even when this
                # narrower bookkeeping step failed.
                last_saved["digest"] = None
                last_saved["chat_id"] = int(chat_id)
                last_saved["updated_at"] = row.get("updated_at")
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

    return load_chat


def make_save_chat(
    bus: SessionBus,
    resolved_path: Path,
    canvas_document: SceneDocument | None,
    notifications: NotificationState | None,
    record_saved: Callable[..., None],
    last_saved: dict[str, Any],
):
    """Factory for register_chat_library's own saveChat intent - see
    make_load_chat's own docstring (immediately above) for why this is a
    top-level factory rather than a closure defined inline."""

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
            chat_data = build_chat_data(canvas_document, asset_store=store_for(resolved_path))
        except Exception as exc:
            if notifications is not None:
                notifications.show(f"Failed to prepare chat save payload: {exc}", "error")
                await bus.publish("notification")
            return

        notes_data = chat_data.pop("notes_data", [])
        pins_data = chat_data.pop("pins_data", [])

        chat_id_for_save: int | None = None
        title: str
        # ADR-009 stage 9.2: what THIS session believes is on disk for the
        # chat it's about to (re)save - only trusted as expected_updated_at
        # below when it actually describes the SAME row (chat_id match);
        # otherwise there is no real prior version to compare against
        # (current_chat_id set some other way than a load/save this cell
        # ever saw), and save_chat_atomically_row's own
        # expected_updated_at=None falls back to a blind UPDATE, matching
        # this function's pre-9.2 behavior exactly for that case.
        expected_updated_at: str | None = None
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
                if last_saved.get("chat_id") == int(current_id):
                    expected_updated_at = last_saved.get("updated_at")
            else:
                # The row was deleted elsewhere between load and this save -
                # falls back to a fresh INSERT, matching legacy's own
                # tolerance for this race (workers.py:71-72) rather than
                # erroring.
                title = _fallback_title(_resolve_seed_message(canvas_document))

        try:
            # ADR-009 stage 9.2: backup-before-write - see
            # _maybe_backup_before_write's own docstring for why this ONE
            # call covers both "before the first mutating write of a
            # session" and the ongoing periodic cadence. Best-effort: a
            # backup failure is logged inside that function and never
            # raised, so it can't block the real save below.
            await asyncio.to_thread(_maybe_backup_before_write, resolved_path, last_saved)
            new_chat_id, new_updated_at = await asyncio.to_thread(
                save_chat_atomically_row, resolved_path, chat_id_for_save, title, chat_data, notes_data, pins_data,
                expected_updated_at=expected_updated_at,
            )
        except ConcurrentSaveConflict:
            # ADR-009 stage 9.2 exit criterion: a lost write race is
            # surfaced, never clobbered. last_saved is deliberately left
            # exactly as it was (still pointing at the STALE updated_at) -
            # this session's edit was NOT written, so nothing here should
            # look like it now matches what's on disk.
            logger.warning(
                "saveChat: lost a save race for chat %r (session=%r) - not clobbering the newer version",
                current_id, bus.session_id,
            )
            if notifications is not None:
                notifications.show(LOST_RACE_MESSAGE_MANUAL, "warning")
                await bus.publish("notification")
            return
        except Exception as exc:
            if notifications is not None:
                notifications.show(f"Failed to save the chat session.\nError: {exc}", "error")
                await bus.publish("notification")
            return

        canvas_document.current_chat_id = int(new_chat_id)
        # Audit fix: record what this manual Save just put on disk, so the
        # next autosave tick recognizes it as already-saved instead of
        # rewriting a byte-identical row 30 seconds later.
        record_saved(chat_data, notes_data, pins_data, new_chat_id, new_updated_at)
        await bus.publish("app-chat-library")
        if notifications is not None:
            notifications.show(f'Saved "{title}".', "success")
            await bus.publish("notification")

    return save_chat


def register_chat_library(
    bus: SessionBus,
    db_path: Path | None = None,
    canvas_document: SceneDocument | None = None,
    notifications: NotificationState | None = None,
    *,
    autosave_interval_seconds: float | None = 30.0,
) -> None:
    resolved_path = db_path if db_path is not None else DEFAULT_DB_PATH
    # ADR-009 stage 9.2: stashed on the bus for the same reason bus.
    # chat_mutation_guard/bus.chat_save_state/bus.autosave_task already
    # are - per-session state a caller outside this closure legitimately
    # needs to reach (backend/app.py's _evict_idle_session, for the
    # flush-before-teardown fix - see flush_dirty_session_before_teardown's
    # own docstring), and a closure-local is unreachable to anything else,
    # including the tests that prove the sharing actually works.
    bus.chat_db_path = resolved_path

    bus.register_topic("app-chat-library", lambda: chat_library_payload(resolved_path, notifications))

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
    #
    # OWNERSHIP (audit fix). The flag above was written when only a
    # user-initiated intent could ever hold it, which is what makes "drop the
    # second one and warn" an honest contract: the user really did start two
    # operations. R6.6 then had a BACKGROUND task claim the same flag, and the
    # asymmetry went unnoticed - an autosave tick that happened to be mid-write
    # made the user's own Save/Load/New Chat vanish, with a warning naming an
    # operation they never started. A background convenience feature must never
    # be able to beat the user to their own data.
    #
    # So the guard now records WHO holds it, and the two directions differ:
    #   user arrives, autosave holds  -> wait briefly for the tick to finish,
    #                                    then proceed (ticks are ~10-50ms)
    #   user arrives, another user holds -> drop + warn, exactly as before
    #   autosave arrives, anyone holds   -> skip this interval, as before
    # The wait is bounded: if a tick is genuinely stuck (sqlite's own 30s lock
    # timeout), the user gets a truthful message instead of a frozen UI. Every
    # outcome is at least as good as the pre-fix behavior, and the common one
    # is strictly better - the Save just works.
    # `released` is set on every release and cleared on every claim.
    _mutation_in_progress = _new_mutation_guard()
    bus.chat_mutation_guard = _mutation_in_progress

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
        chat_data: dict[str, Any], notes_data: list, pins_data: list, chat_id: int | None,
        updated_at: str | None = None,
    ) -> None:
        _last_saved["digest"] = _content_digest(chat_data, notes_data, pins_data)
        _last_saved["chat_id"] = int(chat_id) if chat_id is not None else None
        # ADR-009 stage 9.2: the value the NEXT save on this session will
        # hand back as expected_updated_at - see save_chat_atomically_row's
        # own optimistic-concurrency docstring for why this must always be
        # a real value from a real load/save, never independently derived.
        _last_saved["updated_at"] = updated_at

    _serialize_mutating_intent = make_serialize_mutating_intent(bus, _mutation_in_progress, notifications)

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
            _last_saved["updated_at"] = None
        await bus.publish("app-chat-library")

    load_chat = make_load_chat(bus, resolved_path, canvas_document, notifications, _record_saved, _last_saved)
    save_chat = make_save_chat(bus, resolved_path, canvas_document, notifications, _record_saved, _last_saved)

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
        _last_saved["updated_at"] = None
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


def flush_dirty_session_before_teardown(
    db_path: Path,
    canvas_document: SceneDocument,
    last_saved: dict[str, Any],
    notifications: NotificationState | None = None,
) -> None:
    """ADR-009 stage 9.2 / ADR-004 stage 4.3 interaction: backend/app.py's
    _evict_idle_session (idle-session teardown) used to just CANCEL the
    autosave task outright with no final write - any edit made since the
    LAST successful tick (up to autosave's own interval_seconds, 30s by
    default) was silently lost the instant this session's SceneDocument
    became unreachable. Confirmed as a real, live gap (not a hypothetical)
    by reading _evict_idle_session as it stood before this stage: it calls
    cancel_all/cancel_all_pending_approvals/dispose_all_pycoder_repls, then
    autosave_task.cancel() - no flush anywhere in between.

    This is the SYNCHRONOUS counterpart of backend/autosave.py's
    autosave_tick, for the one caller (eviction teardown) that has no
    natural async dispatch to await asyncio.to_thread the way every other
    write path in this codebase does - _evict_idle_session already performs
    other blocking teardown work synchronously (cancel_all,
    dispose_all_pycoder_repls), so one more small, bounded blocking DB
    write here is consistent with that existing posture, not a new kind of
    risk.

    Deliberately mirrors autosave_tick's own change-guard (skip if nothing
    changed since last_saved) and title-resolution (never regenerate an
    existing chat's title) - not extracted into one shared function with
    autosave_tick because that function is async (uses asyncio.to_thread
    throughout) and this caller has no event loop to dispatch onto; see
    that function's own docstring for the shared reasoning behind each
    step mirrored here.

    A write failure (including a lost optimistic-concurrency race) is
    logged and swallowed here, never raised - the session is being torn
    down either way, and there is no later tick that will retry. Passing a
    real `notifications` reference is supported for callers that have one
    and could plausibly still reach the user (this function does not
    assume anything about who might be watching), but note that
    backend/app.py's own eviction call site deliberately does NOT wire one
    through: eviction only ever runs for a session with ZERO live
    connections (that is what "idle" means), so nothing set on THIS
    session's own NotificationState could ever be seen by anyone - the
    bus itself, and the NotificationState instance with it, is discarded
    the moment eviction finishes. A reconnect later gets a genuinely fresh
    session with its own fresh NotificationState. logger.error/.warning
    calls below are therefore the real, durable signal for this path (see
    backend/crash_recovery.py's own docstring on everything unhandled in
    this codebase landing somewhere durable) - not a gap, a deliberate
    choice given who could possibly observe a notification here."""
    if not canvas_document.nodes and canvas_document.current_chat_id is None:
        return

    try:
        chat_data = build_chat_data(canvas_document, asset_store=store_for(db_path))
    except Exception:
        logger.exception("eviction flush: failed to build chat data - the last edit may be lost")
        return
    notes_data = chat_data.pop("notes_data", [])
    pins_data = chat_data.pop("pins_data", [])

    digest = _content_digest(chat_data, notes_data, pins_data)
    if digest == last_saved.get("digest") and canvas_document.current_chat_id == last_saved.get("chat_id"):
        return  # already matches what's on disk - nothing to protect

    current_id = canvas_document.current_chat_id
    expected_updated_at: str | None = None
    chat_id_for_save: int | None = None
    if current_id:
        try:
            existing_row = load_chat_row(db_path, int(current_id))
        except Exception:
            logger.exception("eviction flush: failed to read the existing chat row - the last edit may be lost")
            return
        if existing_row is not None:
            title = str(existing_row.get("title") or "Untitled")
            chat_id_for_save = int(current_id)
            if last_saved.get("chat_id") == int(current_id):
                expected_updated_at = last_saved.get("updated_at")
        else:
            title = _fallback_title(_resolve_seed_message(canvas_document))
    else:
        title = _fallback_title(_resolve_seed_message(canvas_document))

    try:
        _maybe_backup_before_write(db_path, last_saved)
        new_chat_id, new_updated_at = save_chat_atomically_row(
            db_path, chat_id_for_save, title, chat_data, notes_data, pins_data,
            expected_updated_at=expected_updated_at,
        )
    except ConcurrentSaveConflict:
        logger.warning(
            "eviction flush: lost a save race for chat %r - not clobbering the newer version", current_id,
        )
        if notifications is not None:
            notifications.show(LOST_RACE_MESSAGE_AUTOSAVE, "warning")
        return
    except Exception:
        logger.exception("eviction flush: DB write failed - the last edit may be lost")
        if notifications is not None:
            notifications.show("Your last edits could not be saved before this session closed.", "error")
        return

    canvas_document.current_chat_id = int(new_chat_id)
    last_saved["digest"] = digest
    last_saved["chat_id"] = int(new_chat_id)
    last_saved["updated_at"] = new_updated_at
