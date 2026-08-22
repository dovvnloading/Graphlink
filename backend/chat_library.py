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
load, save, and new are ALL genuinely real here as of R6.5. ADR-020 stage
20.1 renamed the "chats" table itself to "graphs" (see
_migration_002_workspaces_and_graphs) - the .db FILENAME and every other
table/query/function/topic/intent name below are unaffected.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from backend import db_backup
from backend import knowledge_store
from backend import native_dialogs
from backend import workspace_archive
from backend.canvas import SceneDocument
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.session_load import restore_chat_into_document
from backend.asset_store import store_for
from backend.session_save import build_chat_data
from graphlink_migrations import run_sqlite_migrations
from graphlink_settings_store import SettingsManager

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
# handed. ADR-020 stage 20.1 adds version "2": the "chats" table (still
# created under that name by migration "1" above, unchanged) is renamed to
# "graphs" and gains a workspace_id column - see
# _migration_002_workspaces_and_graphs's own docstring. ADR-020 stage 20.2
# adds version "3": graphs gains favorite/archived columns and a tags/
# graph_tags pair - see _migration_003_tags_favorite_archive's own
# docstring. ADR-020 stage 20.3 adds version "4": workspaces gains a
# per-workspace default model pin and a (this stage deliberately leaves
# unpopulated - see that migration's own docstring) knowledge-collection
# mirror column - see _migration_004_workspace_defaults's own docstring.
# The note-edge fix adds version "5": the notes table gains a note_id
# column so note-endpoint edges survive a save/load round-trip - see
# _migration_005_note_ids's own docstring.
CHATS_DB_SCHEMA_VERSION = 5


# ADR-009 stage 9.2: `created_at` and historical `updated_at` values use
# SQLite's inline `CURRENT_TIMESTAMP` (second resolution, no fractional
# part). Optimistic-concurrency writes from save_chat_atomically_row and
# rename_chat generate `updated_at` in Python at microsecond resolution so
# two back-to-back writes cannot share a token. Both formats are real and
# must parse; try second-resolution first because every created_at and every
# row last touched before this transition still uses it.
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


def _flatten_content_part_text(content: Any) -> str:
    """Shared by both raw_content (chat) and conversation_history entries
    (conversation/chat/html) below - the SAME "text is either a plain
    string, or a list of {"type","text"} content-part dicts, only the
    text-type parts count" shape _extract_preview_and_message_count above
    already established as this codebase's own precedent for raw_content
    specifically; conversation_history messages (backend/domain/
    content_codec.py's own _serialize_history, each `{"role", "content"}`)
    use the identical shape for their own `content` field."""
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return str(content or "")


def _flatten_conversation_history(history: Any) -> str:
    """ADR-020 stage 20.4: mirrors backend/api/intents_knowledge.py's own
    branch_history_to_text exactly (same "role: text" per-turn join) -
    conversation nodes have no OTHER text-bearing field at all (see
    backend/session_save.py's own _serialize_conversation_node: node_type/
    conversation_history/is_collapsed, nothing else), so without this a
    conversation node would be completely unindexable, not even findable
    by a title (it has none - see _extract_node_index_text's own
    docstring)."""
    if not isinstance(history, list):
        return ""
    lines = []
    for turn in history:
        if not isinstance(turn, dict):
            continue
        text = _flatten_content_part_text(turn.get("content")).strip()
        if text:
            lines.append(f"{turn.get('role', 'user')}: {text}")
    return "\n\n".join(lines)


# ADR-020 stage 20.4: one dominant, obviously-text-shaped field per kind,
# ground-truthed against backend/session_save.py's real _NODE_SERIALIZERS
# (session_save.py:198-527) - the SAVED WIRE key each kind's own serializer
# actually writes, not a guess at the live domain object's own attribute
# name (which often differs - e.g. a "code" node's domain field is
# node.state.code, but _serialize_code_node writes it out under the wire
# key "code", while an "artifact" node's domain field is node.state.
# artifact_content but _serialize_artifact_node writes it under the wire
# key "content", the SAME key "document" nodes use for their own content).
# NAMED, HONEST GAP: only each kind's single most obviously-dominant
# prompt/instruction/body field is indexed here - pycoder/code_sandbox's
# own code/output/analysis, gitlink's own context_xml/proposal_data, plan's
# own steps, and web_research's own research_result body are NOT indexed,
# per this stage's own explicit "a reasonable single text field is enough;
# skip the rest" allowance for kinds with no one obvious whole-content
# field. "chat" and "conversation" are handled separately, immediately
# below this map (see _extract_node_index_text).
_TEXT_FIELD_BY_NODE_TYPE = {
    "code": "code",
    "document": "content",
    "artifact": "content",
    "thinking": "thinking_text",
    "html": "html_content",
    "web_research": "query",
    "gitlink": "task_prompt",
    "pycoder": "prompt",
    "code_sandbox": "prompt",
    "plan": "goal",
    "image": "prompt",
}


def _extract_node_index_text(node: dict[str, Any]) -> str:
    """One indexable text string for a single node in chat_data["nodes"]
    (the already-serialized wire shape - see _TEXT_FIELD_BY_NODE_TYPE's own
    docstring for why this is grounded in session_save.py's real per-kind
    serializers, not guessed). Concatenates the node's own "title" (only
    "document" nodes and Plugin SDK nodes - backend/session_save.py's own
    _serialize_plugin_node universal fields - carry one; every other
    built-in kind has none, so title contributes nothing there, which is
    fine: title is a bonus, not the only signal) with a best-effort
    dominant body field:

      chat: raw_content - a plain string, or a list of {"type","text"}
        content parts - the SAME shape/handling
        _extract_preview_and_message_count above already established.
      conversation: its own conversation_history, flattened (see
        _flatten_conversation_history's own docstring - this is the ONLY
        text this kind has at all).
      every kind in _TEXT_FIELD_BY_NODE_TYPE: that one field, verbatim.
      anything else (a Plugin SDK node not in the map above): falls back
        to the generic "content" field _serialize_plugin_node's own
        universal fields always write.

    Returns "" (title + body both empty/absent) for a node with nothing
    worth indexing - the caller (_extract_indexable_node_chunks) skips
    writing a chunk row for that node entirely rather than indexing an
    empty string under a real chunk id."""
    node_type = node.get("node_type")
    title = node.get("title")
    title_text = title.strip() if isinstance(title, str) else ""

    if node_type == "chat":
        body_text = _flatten_content_part_text(node.get("raw_content")).strip()
    elif node_type == "conversation":
        body_text = _flatten_conversation_history(node.get("conversation_history")).strip()
    else:
        field_name = _TEXT_FIELD_BY_NODE_TYPE.get(node_type, "content")
        raw_value = node.get(field_name)
        body_text = str(raw_value).strip() if isinstance(raw_value, str) else ""

    return " ".join(part for part in (title_text, body_text) if part)


def _extract_indexable_node_chunks(chat_data: dict[str, Any]) -> list[tuple[str, str]]:
    """Walks chat_data["nodes"] (already-serialized, pre-json.dumps - the
    SAME dict _extract_preview_and_message_count above reads from, at the
    same save-time point) and returns `[(node_id, text), ...]` for every
    node with real indexable content - the input backend/knowledge_store.
    py's own reindex_graph_content turns into one chunks row per node.
    Nodes with no id, or with _extract_node_index_text returning "" (see
    that function's own docstring), are skipped outright - never written
    as an empty chunk under a real id."""
    chunks: list[tuple[str, str]] = []
    for node in chat_data.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            continue
        text = _extract_node_index_text(node)
        if text:
            chunks.append((node_id, text))
    return chunks


def _reindex_graph_into_knowledge_store(
    graph_id: int,
    workspace_id: int,
    title: str,
    chat_data: dict[str, Any],
    knowledge_db_path: Path | None,
) -> None:
    """The one call site save_chat_atomically_row uses to keep this graph's
    global-search index in step with what was just written to chats.db -
    see that function's own docstring for exactly where this runs (after
    the chats.db write has already committed).

    `knowledge_db_path` resolves knowledge_store.DEFAULT_DB_PATH here, at
    CALL time (a live module-attribute read via `knowledge_store.
    DEFAULT_DB_PATH`, never a value captured at import time) when the
    caller passes None - the same "resolve the real default only when
    asked to, read fresh every call" idiom this module's own
    register_chat_library already uses for chats.db's own DEFAULT_DB_PATH.
    This is what lets backend/tests/conftest.py's own real-user-data guard
    fixture safely redirect EVERY caller that omits this parameter to a
    throwaway tmp path by monkeypatching knowledge_store.DEFAULT_DB_PATH
    alone, with no changes needed to any of this suite's many pre-existing
    save_chat_atomically_row call sites - see that fixture's own comment.

    Best-effort: indexing failure is logged and swallowed, never raised -
    the chats.db write this runs after is the correctness-critical one and
    must never be rolled back or reported as failed over a SECONDARY
    search-index write. A knowledge.db that is locked, mid corruption-
    rescue, or otherwise briefly unavailable just means global search is
    stale for this one graph until its next save, not a lost or corrupted
    chat."""
    resolved_knowledge_db_path = (
        knowledge_db_path if knowledge_db_path is not None else knowledge_store.DEFAULT_DB_PATH
    )
    try:
        node_chunks = _extract_indexable_node_chunks(chat_data)
        knowledge_store.reindex_graph_content(
            resolved_knowledge_db_path,
            graph_id=graph_id, workspace_id=workspace_id, title=title, node_chunks=node_chunks,
        )
    except Exception:
        logger.exception(
            "save: failed to index graph %s into the knowledge store - global search may be stale",
            graph_id,
        )


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


def _migration_002_workspaces_and_graphs(conn: sqlite3.Connection) -> None:
    """ADR-020 stage 20.1, migration "2" (1 -> 2): introduces the workspace
    organizing unit. Every existing chat becomes a graph in one backfilled
    "Default" workspace - no data loss, no user action required. Runs
    inside run_sqlite_migrations' own managed transaction (manual BEGIN/
    COMMIT/ROLLBACK around isolation_level=None - see that function's
    docstring, and _migration_001_initial_schema's own docstring above) -
    must not BEGIN, COMMIT, or ROLLBACK anything itself.

    Deliberately narrow, matching ADR-020 stage 20.1's own exit criterion
    ("pre-migration chats.db loads identically; all chats in Default
    workspace"): adds ONLY `workspaces` + `graphs.workspace_id`. Tags/
    favorite/archive are stage 20.2's own migration, landing alongside the
    UI that actually uses them - not bundled in here, matching this
    module's own "one small, narrowly-scoped, independently-testable
    migration step per stage, never one big migration for the whole ADR"
    precedent (see _MIGRATIONS' own comment below).

    Deliberately does NOT rename the on-disk .db FILE (stays chats.db) or
    any Python function/WS-topic/intent name in this module - only the SQL
    TABLE "chats" renames to "graphs". Every query below this point in the
    module was updated to match (get_all_chats/rename_chat/delete_chat/
    load_chat_row/save_chat_atomically_row's own SQL strings) - so this
    module now reads a little oddly for one stage (Python functions named
    "chat" querying a table named "graphs"). That is an intentional,
    temporary, honest incremental-migration tradeoff: renaming the user-
    facing API/UI surface to "graph"/"workspace" terminology belongs to
    stage 20.2, which is doing the real UI rework anyway and can rename
    topic/intents/dialog title as part of that same, already-UI-touching
    change - not an oversight here.

    TABLE RENAME AND FOREIGN KEYS (verified empirically against this
    project's actual bundled SQLite - 3.50.4, `PRAGMA legacy_alter_table`
    reads 0/off by default - not assumed from documentation alone): `ALTER
    TABLE chats RENAME TO graphs` correctly rewrites notes/pins' own
    `FOREIGN KEY (chat_id) REFERENCES chats (id)` declarations to point at
    "graphs" automatically (confirmed via `PRAGMA foreign_key_list` on
    notes/pins after the rename, and a live cascade-delete-through-the-
    renamed-table check) - no separate fix-up of notes/pins is needed or
    done here.

    `workspace_id` deliberately carries a plain `NOT NULL DEFAULT
    <default_workspace_id>` with NO `REFERENCES workspaces (id)` clause -
    verified empirically that SQLite's `ALTER TABLE ... ADD COLUMN` rejects
    a REFERENCES clause combined with a non-NULL DEFAULT ("Cannot add a
    REFERENCES column with non-NULL default value" - raised as
    sqlite3.OperationalError), which every pre-existing graph row here
    needs (the column is NOT NULL, so there is no NULL-then-backfill
    option once it's added). Same no-declared-FK shape as this module's own
    sibling store, backend/knowledge_store.py's `documents.collection_id` -
    a conceptual foreign key enforced by this codebase's own CRUD, not a
    declared SQLite constraint.

    Guarded exactly like _migration_001_initial_schema: CREATE TABLE IF NOT
    EXISTS, a PRAGMA table_info probe before each ALTER TABLE, CREATE INDEX
    IF NOT EXISTS - a second run against an already-migrated database (or a
    database that reaches this migration having already been renamed to
    "graphs" by some earlier run of this same function) is a pure no-op,
    matching that function's own idempotency discipline."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            icon TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            archived INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    existing_default = conn.execute("SELECT id FROM workspaces WHERE name = 'Default'").fetchone()
    if existing_default is not None:
        # A second run of this migration (already-migrated db reconnecting,
        # or this exact function somehow invoked twice) - reuse the SAME
        # Default workspace row rather than creating a duplicate.
        default_workspace_id = int(existing_default[0])
    else:
        cursor = conn.execute("INSERT INTO workspaces (name) VALUES ('Default')")
        default_workspace_id = int(cursor.lastrowid)

    # "chats" still exists under its old name - a database at version 0/1
    # that hasn't seen this migration yet (real user data, or the fresh-db
    # case where migration "1" just created it moments ago in this SAME
    # transaction). On an already-migrated database PRAGMA table_info
    # returns [] here (nothing named "chats" anymore) and the rename is
    # skipped, matching migration "1"'s own guarded-ALTER-TABLE idiom.
    chats_columns = [info[1] for info in conn.execute("PRAGMA table_info(chats)").fetchall()]
    if chats_columns:
        conn.execute("ALTER TABLE chats RENAME TO graphs")

    graphs_columns = [info[1] for info in conn.execute("PRAGMA table_info(graphs)").fetchall()]
    if "workspace_id" not in graphs_columns:
        conn.execute(
            f"ALTER TABLE graphs ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT {default_workspace_id}"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_graphs_workspace_id ON graphs (workspace_id)")


def _migration_003_tags_favorite_archive(conn: sqlite3.Connection) -> None:
    """ADR-020 stage 20.2, migration "3" (2 -> 3): adds the three pieces of
    per-graph metadata the real Chat Library UI (workspace switcher, tag
    chips, favorite/archive icon buttons) needs - `graphs.favorite`,
    `graphs.archived`, and a `tags`/`graph_tags` join-table pair for a
    many-to-many graph<->tag relationship. Runs inside run_sqlite_migrations'
    own managed transaction (manual BEGIN/COMMIT/ROLLBACK around
    isolation_level=None - see that function's docstring, and
    _migration_001_initial_schema's own docstring above) - must not BEGIN,
    COMMIT, or ROLLBACK anything itself.

    Deliberately narrow, matching _migration_002_workspaces_and_graphs's own
    "one small, narrowly-scoped, independently-testable migration step per
    stage" precedent (see _MIGRATIONS' own comment below) - this migration
    does not touch workspaces at all, and does not backfill any tag data
    (every existing graph simply starts untagged/not-favorited/not-archived,
    which is exactly what favorite/archived's own `DEFAULT 0` and an empty
    tags table already give it for free).

    FAVORITE/ARCHIVED COLUMNS: unlike migration "2"'s own `workspace_id`
    (which needed a non-NULL DEFAULT computed at migration time, and so
    could NOT also declare a REFERENCES clause - see that migration's own
    docstring for the empirically-verified reason), `favorite`/`archived`
    have no FK to declare in the first place - a plain `NOT NULL DEFAULT 0`
    ALTER TABLE ADD COLUMN, guarded exactly like every other ALTER TABLE in
    this file (PRAGMA table_info probe first, so a second run against an
    already-migrated database is a pure no-op).

    TAGS/GRAPH_TAGS AND REAL DECLARED FOREIGN KEYS (verified empirically
    against this project's actual bundled SQLite - 3.50.4 - via a live
    cascade-delete test, not assumed from documentation alone): CREATE TABLE
    has no restriction against a REFERENCES clause combined with a NOT NULL
    column the way ALTER TABLE ADD COLUMN does (migration "2"'s own
    constraint above is specific to ADD COLUMN) - so graph_tags declares
    real `REFERENCES graphs (id) ON DELETE CASCADE` / `REFERENCES tags (id)
    ON DELETE CASCADE` FKs directly, unlike graphs.workspace_id's
    intentionally-undeclared conceptual FK. This means delete_chat's own
    "DELETE FROM graphs WHERE id = ?" (which already cascades into notes/
    pins - see _migration_001_initial_schema's own docstring) now also
    cascades into graph_tags for free, with no separate cleanup code needed
    anywhere in this module - _connect() already issues `PRAGMA foreign_keys
    = ON` on every connection, before any migration or query ever runs, so
    this cascade is live the instant a graph row is deleted through this
    module's normal delete_chat path.

    `tags.name` is `UNIQUE COLLATE NOCASE`: two graphs tagging "Work" and
    "work" share ONE tags row - the case-insensitive collapse the wire
    contract's own `tags: list[str]` field docstring promises happens at
    this constraint (backstopped by set_graph_tags' own server-side
    normalization, which never trusts the client alone to have already
    collapsed case) rather than needing a second, redundant application-level
    uniqueness check.

    Guarded exactly like every migration above: CREATE TABLE IF NOT EXISTS,
    a PRAGMA table_info probe before each ALTER TABLE, CREATE INDEX IF NOT
    EXISTS - a second run against an already-migrated database is a pure
    no-op."""
    graphs_columns = [info[1] for info in conn.execute("PRAGMA table_info(graphs)").fetchall()]
    if "favorite" not in graphs_columns:
        conn.execute("ALTER TABLE graphs ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0")
    if "archived" not in graphs_columns:
        conn.execute("ALTER TABLE graphs ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_tags (
            graph_id INTEGER NOT NULL REFERENCES graphs (id) ON DELETE CASCADE,
            tag_id INTEGER NOT NULL REFERENCES tags (id) ON DELETE CASCADE,
            PRIMARY KEY (graph_id, tag_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_graph_tags_tag_id ON graph_tags (tag_id)")


def _migration_004_workspace_defaults(conn: sqlite3.Connection) -> None:
    """ADR-020 stage 20.3, migration "4" (3 -> 4): gives each workspace its
    own default model pin (the genuinely new "workspace default" rung of
    graphlink_model_catalog.resolve_model_ref's chain - see backend/
    agents.py's _resolve_model_ref_for_dispatch for the real caller this
    stage finally wires up) plus a knowledge-collection mirror column. Runs
    inside run_sqlite_migrations' own managed transaction (manual BEGIN/
    COMMIT/ROLLBACK around isolation_level=None - see that function's
    docstring, and _migration_001_initial_schema's own docstring above) -
    must not BEGIN, COMMIT, or ROLLBACK anything itself.

    DEFAULT_MODEL_PROVIDER/DEFAULT_MODEL_ID: `NOT NULL DEFAULT ''` (not
    NULL-able) - EMPTY STRING is this pair's own "unset" sentinel, checked
    by get_workspace_default_model below (`if not provider or not
    model_id: return None`), never SQL NULL. This mirrors graphs.
    workspace_id's own migration-2 precedent of a plain, undeclared-FK
    column with a concrete default rather than a nullable one - the
    resolver that reads these two columns (backend/agents.py's
    _resolve_model_ref_for_dispatch) needs a value it can compare with a
    cheap truthiness check on every dispatch, not a three-way NULL/empty/
    set distinction it would have to special-case.

    KNOWLEDGE_COLLECTION_ID: genuinely nullable (no DEFAULT at all) -
    unlike the two columns above, NULL here is a real, meaningful "not yet
    resolved" state, not conflated with any other value. SCOPING DECISION,
    made explicit here per this stage's own design note (the ADR's own
    "document this as deliberate, not incomplete"): this column is added
    to the schema exactly as designed, but THIS STAGE never writes or
    reads it from any real code path. The single source of truth for
    "which knowledge_store.py collection belongs to this workspace" is
    knowledge.db's own `collections.workspace_id` column, resolved via
    backend/knowledge_store.py's get_or_create_workspace_collection - a
    plain, cheap, idempotent SELECT-or-INSERT called fresh by every real
    ingest/search call site (backend/api/intents_knowledge.py, graphlink_
    plugins/web_research/service.py) on every use, self-healing by
    construction (a workspace whose collection row is missing, was never
    created, or was restored from an older knowledge.db backup simply gets
    a fresh one - see that function's own docstring). A denormalized
    mirror of that id living in THIS database (chats.db, a separate SQLite
    file - no declared cross-database FK is possible) would only ever be
    as fresh as whichever caller last bothered to write it back, and could
    silently point at a since-restored-away collection id after a
    knowledge.db corruption rescue; trusting it for real resolution would
    be strictly WORSE than just asking knowledge.db directly, which is
    already cheap enough to do unconditionally. The column exists so a
    future stage that wants to expose/display "this workspace's own
    knowledge collection" without opening a second database file has
    somewhere to put that value - reserved, not (yet) wired to anything.

    Guarded exactly like every migration above: a PRAGMA table_info probe
    before each ALTER TABLE, so a second run against an already-migrated
    database is a pure no-op."""
    workspaces_columns = [info[1] for info in conn.execute("PRAGMA table_info(workspaces)").fetchall()]
    if "default_model_provider" not in workspaces_columns:
        conn.execute("ALTER TABLE workspaces ADD COLUMN default_model_provider TEXT NOT NULL DEFAULT ''")
    if "default_model_id" not in workspaces_columns:
        conn.execute("ALTER TABLE workspaces ADD COLUMN default_model_id TEXT NOT NULL DEFAULT ''")
    if "knowledge_collection_id" not in workspaces_columns:
        conn.execute("ALTER TABLE workspaces ADD COLUMN knowledge_collection_id INTEGER")


def _migration_005_note_ids(conn: sqlite3.Connection) -> None:
    """Migration "5" (4 -> 5): give the notes table a `note_id` column that
    persists each note's save-time payload id (SceneNode.id).

    THE DATA LOSS THIS CLOSES. Since stage 9.6 the flat `edges` list is the
    authoritative edge record, and it references every endpoint by its
    save-time payload id. Node ids round-trip via the `data` blob and chart
    ids via their own in-blob id, but a NOTE's id was written only into the
    `notes` table, which had no column for it - so load_notes_rows returned
    notes with no id, backend/session_load.py's flat-edge pass could not
    resolve any note endpoint, and EVERY note connection (a System Prompt
    note attached to a chat, a chat->summary note, a user-drawn note link)
    was silently dropped on load and then written back gone on the next
    save. Persisting the id here is what lets those edges round-trip, the
    same way pins already persist pin_id.

    Nullable TEXT with no default: a pre-existing row keeps NULL, which
    load_notes_rows surfaces as "" (falsy) - identical to the old "no id"
    behaviour for that one legacy row (its note edges were already lost on
    the save that predated this fix; nothing here can resurrect them), while
    every row written from now on carries a real id and round-trips. Runs
    inside run_sqlite_migrations' own managed transaction - must not BEGIN/
    COMMIT/ROLLBACK itself - and is guarded by a PRAGMA table_info probe so a
    second run against an already-migrated database is a pure no-op, exactly
    like every migration above."""
    notes_columns = [info[1] for info in conn.execute("PRAGMA table_info(notes)").fetchall()]
    if "note_id" not in notes_columns:
        conn.execute("ALTER TABLE notes ADD COLUMN note_id TEXT")


# Keyed by the version each function PRODUCES (migration "1" takes a
# database from 0 -> 1), matching graphlink_migrations' own ordering
# convention - see run_sqlite_migrations' docstring. ADR-020 stage 20.1
# added step "2" (bumping CHATS_DB_SCHEMA_VERSION to 2) for the workspaces/
# graphs schema change; stage 20.2 added step "3" (bumping
# CHATS_DB_SCHEMA_VERSION to 3) for the tags/favorite/archive schema change;
# stage 20.3 added step "4" (bumping CHATS_DB_SCHEMA_VERSION to 4) for the
# workspace-default-model/knowledge-collection-mirror schema change - add
# step "5" (bumping CHATS_DB_SCHEMA_VERSION to 5) for the notes.note_id
# column that lets note-endpoint edges round-trip - see
# _migration_005_note_ids's own docstring - add step "6" here (and bump
# CHATS_DB_SCHEMA_VERSION to 6) for the next schema change; never renumber
# or replace step "1" through "5" now that all five have shipped - each must
# stay exactly what it is today so it keeps correctly upgrading every
# already-migrated real database.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migration_001_initial_schema,
    2: _migration_002_workspaces_and_graphs,
    3: _migration_003_tags_favorite_archive,
    4: _migration_004_workspace_defaults,
    5: _migration_005_note_ids,
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
    # ADR-020 stage 20.2: workspace_id/favorite/archived are plain columns on
    # graphs, pulled in the same SELECT as everything else. tags is a
    # separate query (a graph<->tag many-to-many via graph_tags) rather than
    # a JOIN against the row SELECT above - a JOIN would multiply each
    # multi-tagged graph's row once per tag, which this function would then
    # have to de-duplicate back down anyway; one small second query plus an
    # in-memory group-by is simpler and, for the corpus size this app
    # targets (hundreds of graphs, a handful of tags each - see this ADR's
    # own "send everything, filter locally" design note), not meaningfully
    # more expensive. Ordered by tag name (COLLATE NOCASE, matching tags.name
    # itself) so each graph's own tag list already arrives display-sorted -
    # no second Python-side sort needed.
    with contextlib.closing(_connect(db_path, notifications=notifications)) as conn, conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at, preview, message_count, "
            "workspace_id, favorite, archived "
            "FROM graphs ORDER BY updated_at DESC"
        ).fetchall()
        tag_rows = conn.execute(
            "SELECT graph_tags.graph_id, tags.name FROM graph_tags "
            "JOIN tags ON tags.id = graph_tags.tag_id "
            "ORDER BY tags.name COLLATE NOCASE"
        ).fetchall()
    tags_by_graph_id: dict[int, list[str]] = {}
    for graph_id, tag_name in tag_rows:
        tags_by_graph_id.setdefault(int(graph_id), []).append(str(tag_name))
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
            "workspaceId": int(row[6]),
            "favorite": bool(row[7]),
            "archived": bool(row[8]),
            "tags": tags_by_graph_id.get(int(row[0]), []),
        }
        for row in rows
    ]


def rename_chat(
    db_path: Path,
    chat_id: int,
    new_title: str,
    *,
    expected_updated_at: str | None = None,
) -> str | None:
    """Rename one graph and return the exact new optimistic-save token.

    A rename of the graph currently open in a session participates in the
    same optimistic-concurrency contract as a content save.  Without
    returning the new ``updated_at`` value, the session keeps holding the
    token from before its own rename and its next Save is rejected as though
    some other window changed the graph.  ``expected_updated_at`` also keeps
    token synchronization from weakening the existing lost-update guard: a
    stale session may not rename a newer row and then adopt that row's fresh
    token before overwriting its content.

    ``None`` is returned for the existing blind-update call shape when the
    graph no longer exists.  With an expected token, a missing row and a
    stale row are intentionally the same concurrency conflict.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    with contextlib.closing(_connect(db_path)) as conn, conn:
        if expected_updated_at is not None:
            cursor = conn.execute(
                "UPDATE graphs SET title = ?, updated_at = ? WHERE id = ? AND updated_at = ?",
                (new_title, now, chat_id, expected_updated_at),
            )
            if cursor.rowcount == 0:
                raise ConcurrentSaveConflict(
                    f"chat {chat_id} was modified elsewhere since it was last loaded/saved "
                    "in this session (expected updated_at "
                    f"{expected_updated_at!r} did not match)"
                )
        else:
            cursor = conn.execute(
                "UPDATE graphs SET title = ?, updated_at = ? WHERE id = ?",
                (new_title, now, chat_id),
            )
    return now if cursor.rowcount else None


def delete_chat(db_path: Path, chat_id: int, *, knowledge_db_path: Path | None = None) -> None:
    """ADR-020 stage 20.4: also removes this graph's own indexed content
    from knowledge_store.py's knowledge.db (backend.knowledge_store.
    delete_graph_index - the deletion-side mirror of save_chat_atomically_
    row's own reindex_graph_content call) - a deleted graph's chunks would
    otherwise linger as a real, jump-to-node-able global-search hit
    pointing at a graph that no longer exists, a user-visible bug, not a
    cosmetic one. Best-effort and non-blocking, same posture and same
    resolve-DEFAULT_DB_PATH-at-call-time shape as save_chat_atomically_row's
    own indexing call - see _reindex_graph_into_knowledge_store's own
    docstring for why."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM graphs WHERE id = ?", (chat_id,))
    resolved_knowledge_db_path = (
        knowledge_db_path if knowledge_db_path is not None else knowledge_store.DEFAULT_DB_PATH
    )
    try:
        knowledge_store.delete_graph_index(resolved_knowledge_db_path, chat_id)
    except Exception:
        logger.exception(
            "delete: failed to remove graph %s's indexed content from the knowledge store", chat_id,
        )


# -- ADR-020 stage 20.2: favorite/archived/tags + workspaces CRUD -----------
#
# Deliberately mirror rename_chat/delete_chat's own shape immediately above
# (explicit db_path param, one function per single-field mutation, no
# updated_at bump) rather than a single generic "patch this graph" function -
# each is triggered by one row's own dedicated action button/input in the
# real UI (a star toggle, an archive toggle, a tag-edit commit), exactly like
# rename/delete already are, not a bulk list editor (see
# SettingsManager.set_plugin_grant's own docstring for the precedent this
# follows: a single-item write path, not McpServersPage's whole-collection
# replace, for a mutation scoped to one row's own control).
#
# NONE of favorite/archived/tags bump `updated_at` (unlike rename_chat above,
# and unlike save_chat_atomically_row's real content writes) - these are
# metadata toggles, not content edits, and get_all_chats orders by
# `updated_at DESC`. Bumping it here would silently re-sort (and re-bucket
# under a date-grouped UI's "Today" section) every chat a user merely starred
# or archived, which reads as a bug, not a feature - the same "no
# reason to disturb the list order" instinct chat_library.py's own audit-fix
# history already established for loadChat/saveChat's own digest bookkeeping
# (see _new_save_state's own docstring, point 1).


def set_graph_favorite(db_path: Path, graph_id: int, favorite: bool) -> None:
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE graphs SET favorite = ? WHERE id = ?",
            (1 if favorite else 0, graph_id),
        )


def set_graph_archived(db_path: Path, graph_id: int, archived: bool) -> None:
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE graphs SET archived = ? WHERE id = ?",
            (1 if archived else 0, graph_id),
        )


def _normalize_tags(tags: list[str]) -> list[str]:
    """Trim/drop-empty/case-insensitively-dedupe a raw tag list BEFORE it
    ever reaches SQL - the server-side half of the wire contract's own
    "don't trust the client alone" requirement (tags.name's own UNIQUE
    COLLATE NOCASE constraint is the storage-level backstop; this is the
    Python-level one, so a caller never even attempts a same-collation
    INSERT that constraint would reject). First-seen casing wins on a
    collision (["work", "Work"] -> ["work"]) - an arbitrary but deterministic
    tie-break; which casing "wins" has no behavioral consequence since every
    lookup and constraint downstream is already case-insensitive."""
    seen: dict[str, str] = {}
    for raw in tags:
        trimmed = str(raw).strip()
        if not trimmed:
            continue
        key = trimmed.casefold()
        if key not in seen:
            seen[key] = trimmed
    return list(seen.values())


def set_graph_tags(db_path: Path, graph_id: int, tags: list[str]) -> None:
    """BULK REPLACE of graph_id's full tag set - not an add/remove delta, per
    this stage's own wire contract for setGraphTags. Deletes every existing
    graph_tags row for graph_id, then (re)creates exactly the normalized set:
    `INSERT OR IGNORE` into tags is what makes a name collision against an
    EXISTING tag (any casing, thanks to tags.name's own COLLATE NOCASE
    constraint) a safe no-op instead of a raised IntegrityError, and the
    follow-up SELECT resolves to whichever row - new or pre-existing - is now
    the canonical one for that name.

    Orphaned tags (a tags row no graph_tags row references anymore, after
    this or a later delete_chat's own cascade) are deliberately left in the
    tags table rather than swept here - a tag a user is actively mid-typing
    across two different graphs in quick succession should not have its own
    row vanish and get regenerated with a new id between those two calls,
    and an unused tags row is cheap (a handful of bytes, no unbounded growth
    given this app's own "hundreds of graphs" scale)."""
    normalized = _normalize_tags(tags)
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute("DELETE FROM graph_tags WHERE graph_id = ?", (graph_id,))
        for tag_name in normalized:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
            tag_row = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()
            conn.execute(
                "INSERT OR IGNORE INTO graph_tags (graph_id, tag_id) VALUES (?, ?)",
                (graph_id, int(tag_row[0])),
            )


def get_all_workspaces(db_path: Path) -> list[dict[str, Any]]:
    """Every workspace row (including archived ones - the same "backend
    always sends everything, filtering is client-side" design this whole
    stage's wire contract already uses for graphs/rows - see this module's
    own get_all_chats). Ordered by id (creation order), matching
    workspaces.id's own AUTOINCREMENT semantics - stable and predictable for
    a switcher-tabs UI, unlike ordering by name (which would reshuffle tabs
    as workspaces are renamed) or updated_at (workspaces have no such
    column).

    ADR-020 stage 20.3: `defaultModelProvider`/`defaultModelId` (both ''
    when unset - see migration "4"'s own docstring) ride along on every row,
    same "send everything, filter/render locally" posture as favorite/
    archived/tags already have on get_all_chats' own rows."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        rows = conn.execute(
            "SELECT id, name, icon, archived, default_model_provider, default_model_id "
            "FROM workspaces ORDER BY id"
        ).fetchall()
    return [
        {
            "id": int(row[0]),
            "name": str(row[1]),
            "icon": str(row[2] or ""),
            "archived": bool(row[3]),
            "defaultModelProvider": str(row[4] or ""),
            "defaultModelId": str(row[5] or ""),
        }
        for row in rows
    ]


def create_workspace(db_path: Path, name: str) -> dict[str, Any] | None:
    """Creates a new workspace. Returns the created row's shape (id, name,
    icon, archived, defaultModelProvider, defaultModelId) on success, or
    None for an empty/whitespace-only name - the caller (createWorkspace's
    own intent handler) treats a None return as a rejected request: a
    notification is shown and the topic is NOT republished, matching
    rename's own `if not title: return` no-mutation convention immediately
    above rather than silently accepting a blank workspace name.

    ADR-020 stage 20.3: a brand-new workspace always starts with no default
    model pinned (empty strings, matching migration "4"'s own DEFAULT '' for
    every pre-existing row) - a caller sets one afterward via
    set_workspace_default_model, same "create first, configure after" shape
    as rename_workspace/archive_workspace already establish for their own
    fields."""
    trimmed = str(name or "").strip()
    if not trimmed:
        return None
    with contextlib.closing(_connect(db_path)) as conn, conn:
        cursor = conn.execute("INSERT INTO workspaces (name) VALUES (?)", (trimmed,))
        workspace_id = int(cursor.lastrowid)
    return {
        "id": workspace_id, "name": trimmed, "icon": "", "archived": False,
        "defaultModelProvider": "", "defaultModelId": "",
    }


def get_workspace_default_model(db_path: Path, workspace_id: int) -> tuple[str, str] | None:
    """ADR-020 stage 20.3: the workspace-default rung's own read path - the
    real caller is backend/agents.py's _resolve_model_ref_for_dispatch,
    which builds the actual graphlink_model_catalog.ModelRef itself (this
    module deliberately does not import that root module just to construct
    one - matching every other "this module returns plain data, the caller
    builds domain types" split already established here, e.g. get_all_chats
    returning plain dicts rather than SceneNode instances).

    Returns (provider, model_id), or None when workspace_id does not exist
    OR has no default set - EITHER column empty counts as "no default" (a
    half-set pair, only reachable via a UI bug or a hand-edited row, is
    treated exactly like a fully-unset one, never a confusing partial
    resolution - see set_workspace_default_model's own docstring for the
    write-side half of this same posture)."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT default_model_provider, default_model_id FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
    if row is None:
        return None
    provider = str(row[0] or "").strip()
    model_id = str(row[1] or "").strip()
    if not provider or not model_id:
        return None
    return provider, model_id


def set_workspace_default_model(db_path: Path, workspace_id: int, provider: str, model_id: str) -> None:
    """ADR-020 stage 20.3: sets (or, given ("", "") for both, CLEARS - see
    migration "4"'s own docstring on empty-string being this pair's "unset"
    sentinel) the workspace's own default model pin. Both fields write
    together, mirroring backend/domain/branches.py's own set_model_override
    "no partial value" posture for the node/branch-level pin this rung sits
    directly below in graphlink_model_catalog.resolve_model_ref's chain -
    trimmed independently rather than validated-as-a-pair, so a genuinely
    partial write (one empty, one not - a UI bug, never something the real
    ChatLibraryDialog picker can produce) resolves to "no default set"
    (get_workspace_default_model's own either-empty-counts-as-unset check)
    rather than a confusing half-set state, matching create_workspace's own
    trim-not-reject posture for `name`."""
    provider = str(provider or "").strip()
    model_id = str(model_id or "").strip()
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE workspaces SET default_model_provider = ?, default_model_id = ? WHERE id = ?",
            (provider, model_id, workspace_id),
        )


def rename_workspace(db_path: Path, workspace_id: int, name: str) -> None:
    title = str(name or "").strip()
    if not title:
        return
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute("UPDATE workspaces SET name = ? WHERE id = ?", (title, workspace_id))


def archive_workspace(db_path: Path, workspace_id: int, archived: bool) -> None:
    """Archiving/unarchiving a workspace touches ONLY the workspaces row -
    its graphs are untouched (still `archived = 0` unless a graph was
    separately archived via set_graph_archived above), matching this stage's
    own design note that archiving a workspace must not archive or delete
    the graphs inside it. A UI that hides archived workspaces from its
    switcher tabs by default (this stage's own real ChatLibraryDialog does)
    is a client-side filtering choice, not something this function needs to
    know about - get_all_workspaces above always returns every workspace,
    archived or not, same "send everything, filter locally" posture as
    get_all_chats."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        conn.execute(
            "UPDATE workspaces SET archived = ? WHERE id = ?",
            (1 if archived else 0, workspace_id),
        )


def load_chat_row(db_path: Path, chat_id: int) -> dict[str, Any] | None:
    """Mirrors ChatDatabase.load_chat, extended for ADR-009 stage 9.2:
    {"title", "data", "updated_at", "workspace_id"} with `data` already
    json.loads()'d, or None if the id doesn't exist (a chat deleted from
    another window/process between the library listing and this call - the
    caller shows a real notice, not a crash).

    `updated_at` (new in stage 9.2) is the value optimistic concurrency is
    built on: the caller that loads a chat is expected to carry THIS exact
    string forward (see backend/chat_library.py's own register_chat_library
    loadChat closure, which seeds it into the shared last_saved cell) and
    hand it back as save_chat_atomically_row's expected_updated_at when it
    later saves - never re-read moments before that save, which would
    trivially always match and defeat the whole point of detecting a race
    against some OTHER writer that saved in between.

    `workspace_id` (new in ADR-020 stage 20.3): every graphs row always
    carries a real, non-NULL one (graphs.workspace_id's own schema DEFAULT,
    set at INSERT time by save_chat_atomically_row/migration "2" - see
    those functions' own docstrings), so this is never None for an
    existing row. ADVERSARIAL-REVIEW FIX: pre-20.3, loadChat's own closure
    (below) never read this column at all, so
    canvas_document.current_workspace_id stayed at whatever newChat last
    left it (usually None) even after loading a chat that plainly belongs
    to some OTHER, real workspace - meaning THIS stage's own workspace-
    default model rung (backend/agents.py's _resolve_model_ref_for_dispatch)
    and workspace-scoped knowledge corpus (backend/api/intents_knowledge.py)
    would have silently resolved against the WRONG workspace (or none at
    all) for the single most common real flow, opening an existing chat and
    continuing the conversation - not just a brand-new chat started via
    newChat(workspaceId=...). Fixed here, at the one place every load
    already reads this row, rather than only in the callers that needed it."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        row = conn.execute(
            "SELECT title, data, updated_at, workspace_id FROM graphs WHERE id = ?", (chat_id,)
        ).fetchone()
    if row is None:
        return None
    return {"title": row[0], "data": json.loads(row[1]), "updated_at": row[2], "workspace_id": row[3]}


def load_notes_rows(db_path: Path, chat_id: int) -> list[dict[str, Any]]:
    """Mirrors ChatDatabase.load_notes exactly - see that method's own
    SELECT column list; shape matches what backend/session_load.py's
    _restore_notes expects (nested "position"/"size" dicts)."""
    with contextlib.closing(_connect(db_path)) as conn, conn:
        rows = conn.execute(
            """
            SELECT content, position_x, position_y, width, height,
                   color, header_color, is_system_prompt, is_summary_note, note_id
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
            # The save-time payload id, restored so backend/session_load.py's
            # flat-edge pass can map a note endpoint back to its new node id.
            # Only present (truthy) for rows written after the note-edge fix;
            # a NULL from a pre-fix row stays absent, harmless to the loader's
            # own `if note_payload.get("id")` guard.
            "id": row[9] or "",
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
    workspace_id: int | None = None,
    knowledge_db_path: Path | None = None,
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
    function (this function's pre-9.2 behavior) - SECOND resolution alone
    is not fine-grained enough for a real
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
    second-resolution format historical rows still use.

    ADR-020 stage 20.2: `workspace_id`, when given (not None), is written
    ONLY on the INSERT branch below - a resave of an EXISTING graph (the
    UPDATE branch) never touches which workspace it belongs to, no matter
    what `workspace_id` a caller happens to pass; the parameter is silently
    ignored there rather than accepted-but-unused, so a future caller cannot
    be misled into believing this can move a graph between workspaces.
    `workspace_id=None` (the default, and what every pre-20.2 caller and
    test still passes) omits the column from the INSERT entirely, which
    means the row falls back to `graphs.workspace_id`'s own schema-level
    `DEFAULT` - the Default workspace's real id, fixed in place by
    _migration_002_workspaces_and_graphs at the moment it ran - preserving
    this function's exact pre-20.2 INSERT behavior byte-for-byte. Callers
    that DO know which workspace a new graph belongs to (backend/
    chat_library.py's own save_chat closure, reading
    canvas_document.current_workspace_id - see that field's own docstring in
    backend/domain/graph.py) are expected to have ALREADY validated the id
    against a real, current workspace row before calling this - this
    function trusts the value it is given rather than re-querying
    workspaces here, matching save_chat_atomically_row's own long-standing
    "the caller resolves titles/ids, this function only writes" division of
    labor (see e.g. how `title` itself already arrives fully resolved).

    ADR-020 stage 20.4: after the chats.db write above has fully committed,
    this graph's content is re-indexed into knowledge_store.py's own
    knowledge.db for global search - see _reindex_graph_into_knowledge_
    store's own docstring for the exact strategy (delete-then-reinsert,
    since a graph is mutated and re-saved constantly, unlike an immutable-
    once-ingested file) and why a failure there is logged and swallowed
    rather than raised. `knowledge_db_path` defaults (None) to
    knowledge_store.DEFAULT_DB_PATH, resolved at call time - see that
    helper's own docstring for why this specific resolution shape is what
    keeps every pre-existing test call site of this function safe from
    accidentally touching the real ~/.graphlink/knowledge/knowledge.db."""
    chat_data_json = json.dumps(chat_data)
    preview, message_count = _extract_preview_and_message_count(chat_data)
    with contextlib.closing(_connect(db_path)) as conn:
        with conn:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
            if chat_id:
                if expected_updated_at is not None:
                    cursor = conn.execute(
                        "UPDATE graphs SET title = ?, data = ?, preview = ?, message_count = ?, "
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
                        "UPDATE graphs SET title = ?, data = ?, preview = ?, message_count = ?, "
                        "updated_at = ? WHERE id = ?",
                        (title, chat_data_json, preview, message_count, now, chat_id),
                    )
                resolved_chat_id = chat_id
            elif workspace_id is not None:
                cursor = conn.execute(
                    "INSERT INTO graphs (title, data, preview, message_count, updated_at, workspace_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (title, chat_data_json, preview, message_count, now, workspace_id),
                )
                resolved_chat_id = cursor.lastrowid
            else:
                cursor = conn.execute(
                    "INSERT INTO graphs (title, data, preview, message_count, updated_at) "
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
                        width, height, color, header_color, is_system_prompt, is_summary_note,
                        note_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        # The note's save-time payload id, so flat-edge restore
                        # can resolve note endpoints on load - see _ensure_schema's
                        # note_id column comment for the data-loss this closes.
                        str(note.get("id") or "") or None,
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

        # ADR-020 stage 20.4: runs AFTER the `with conn:` block above has
        # committed (or, on a ConcurrentSaveConflict, never reached here at
        # all - that exception propagates straight out of the `with conn:`
        # block, so this line is unreachable for a lost write race, exactly
        # as it should be: nothing was actually written, so nothing here
        # needs re-indexing). One extra cheap read on the SAME still-open
        # connection - graphs.workspace_id is always a real, non-NULL int
        # for resolved_chat_id regardless of which branch above ran (the
        # INSERT branches always populate it - explicitly when `workspace_id`
        # was given, via the schema's own DEFAULT otherwise; the UPDATE
        # branch never changes it - see this function's own workspace_id
        # paragraph above), so this is never the graph's SOURCE of workspace
        # truth, only a resolve-what-was-just-written convenience read.
        resolved_workspace_id = conn.execute(
            "SELECT workspace_id FROM graphs WHERE id = ?", (resolved_chat_id,)
        ).fetchone()[0]
        _reindex_graph_into_knowledge_store(
            resolved_chat_id, int(resolved_workspace_id), title, chat_data, knowledge_db_path,
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
    own loadChat/saveChat/newChat/renameChat intents use - see
    register_chat_library's own docstring (right below its call site) for
    the full OWNERSHIP audit-fix story this implements. Lifted out to a
    top-level factory - matching _new_mutation_guard/_new_save_state/
    _busy_message's own "one definition ... so it can never drift apart"
    precedent already established in this file - purely to keep
    register_chat_library itself under ADR-002's 300-line registration-
    function cap (stage 2.7). Captures nothing register_chat_library's own
    callers couldn't already reach via bus.chat_mutation_guard, which is
    the SAME dict passed in here as mutation_in_progress.

    ADR-020 stage 20.4: `wrapped` now RETURNS `handler`'s own return value
    (see the `return await handler(...)` line below) rather than discarding
    it - every pre-20.4 intent this wraps (loadChat/saveChat/newChat) is
    fire-and-forget from the frontend (transport.fireIntent never inspects
    the resolved value), so this was a silent no-op difference for all
    three, never previously worth fixing on its own. renameChat later joined
    the same wrapper to protect its optimistic-token refresh. ADR-020 stage 20.4's
    own loadGraphAndFocusNode is the first REQUEST/REPLY intent wrapped
    through this same guard (it needs the SAME reentrancy protection
    loadChat/saveChat/newChat already get, since it also mutates
    canvas_document via a real load) - without this fix, dispatch_intent
    would resolve the frontend's transport.request(...) promise with `None`
    on every successful call, silently breaking the one thing that makes
    request/reply the right shape here (see that intent's own docstring)."""

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
                return await handler(*args, **kwargs)
            finally:
                _release_guard()

        return wrapped

    return _serialize_mutating_intent


def chat_library_payload(db_path: Path, notifications: NotificationState | None = None) -> dict[str, Any]:
    try:
        rows = get_all_chats(db_path, notifications=notifications)
        # ADR-020 stage 20.2: the workspace switcher's own data - fetched
        # inside the SAME try/except as rows above (a corrupt/locked
        # chats.db fails both queries the same way, and this stage's own
        # wire contract has no separate "workspaces failed to load" notice
        # distinct from "could not load saved chats").
        workspaces = get_all_workspaces(db_path)
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
        workspaces = []
        notice = f"Could not load saved chats: {exc}"
    return {"rows": rows, "notice": notice, "workspaces": workspaces}


def make_load_chat(
    bus: SessionBus,
    resolved_path: Path,
    canvas_document: SceneDocument | None,
    notifications: NotificationState | None,
    record_saved: Callable[..., None],
    last_saved: dict[str, Any],
    settings_manager: "SettingsManager | None" = None,
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
                asset_store=store_for(resolved_path), settings_manager=settings_manager,
            )
            # R6.5: remember which row this scene now corresponds to, so a
            # later Save updates THIS row instead of always inserting a new
            # one - the backend analog of ChatSessionManager.current_chat_id
            # being set from the load path, not just the save path.
            canvas_document.current_chat_id = int(chat_id)
            # ADR-020 stage 20.3 adversarial-review fix: mirror the SAME
            # "set from the load path, not just newChat" treatment onto
            # current_workspace_id - see load_chat_row's own docstring for
            # the real gap this closes (pre-fix, opening an existing chat
            # left current_workspace_id wherever newChat last set it,
            # usually None, so this stage's own workspace-scoped model/
            # knowledge resolution would silently miss the workspace the
            # loaded chat actually belongs to). row["workspace_id"] is
            # always a real int for an existing row (graphs.workspace_id's
            # own NOT NULL DEFAULT), never None.
            canvas_document.current_workspace_id = row.get("workspace_id")
            # Audit fix: the document now matches this row exactly, so record
            # that. Without it the first tick after a load rewrote a
            # byte-identical row and bumped updated_at, re-sorting the Chat
            # Library under the user for a session they had only just opened.
            try:
                # Must use the SAME store as autosave below, or the
                # first tick after a load would see a payload that
                # differs only in image representation and rewrite a
                # row that is already correct.
                fresh = build_chat_data(
                    canvas_document, asset_store=store_for(resolved_path), settings_manager=settings_manager,
                )
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


# backend/domain/graph.py's own register_restored_node - used ONLY by the
# session loader - mints a BRAND NEW id for every node on EVERY restore and
# permanently discards whatever "id" the saved payload carried (see that
# function's own docstring: "Assigns a fresh id ... is overwritten here -
# backend ids are a different namespace/format than legacy's uuid-based
# persistent ids"). This is deliberate and permanent, not a gap: two
# DIFFERENT saved graphs' own "id" values routinely collide (each graph's
# session minted its own "n1, n2, ..." independently), so reusing a saved
# id verbatim on restore could collide with an already-live node from
# whatever this document is CURRENTLY holding.
#
# Consequence for make_load_graph_and_focus_node below: node_id (sourced
# from a PAST save's own recorded id - see _extract_indexable_node_chunks)
# can be looked up directly against canvas_document.nodes ONLY when no
# restore has happened since that save (the FAST PATH - ids are stable for
# the entire time one graph stays continuously loaded). The moment a fresh
# restore runs (the graph was NOT already open), that comparison is
# comparing two unrelated id namespaces and must not be attempted - see
# that function's own RELOAD PATH comment for the ordinal-position-based
# fix this uses instead.
_NON_REGULAR_NODE_KINDS = frozenset({"note", "chart", "frame", "container"})


def make_load_graph_and_focus_node(
    resolved_path: Path, canvas_document: SceneDocument | None, load_chat: Callable[..., Any],
):
    """Factory for register_chat_library's own loadGraphAndFocusNode intent
    (ADR-020 stage 20.4) - see make_load_chat's own docstring immediately
    above for why this is a top-level factory rather than a closure defined
    inline.

    REQUEST/REPLY, not fire-and-forget, unlike loadChat itself (which this
    reuses without duplicating - see below): the frontend's global-search
    "jump to this node, even in a graph from another workspace" action
    needs the target node's real x/y coordinates back in hand before it can
    call useReactFlow().setCenter(...) - there is no "wait for the next
    matching scene state" primitive anywhere in this codebase's own
    transport.ts to build that some other way (only persistent subscribe*
    helpers), and this stage deliberately does not invent one. Returning
    the coordinates directly in the resolved reply means the frontend never
    has to wait for or inspect the "scene" topic separately, and no race is
    possible: the reply literally cannot resolve before the restore below
    (when one runs) has fully landed in canvas_document.

    REUSE, NOT DUPLICATION: does exactly what load_chat already does
    (restore_chat_into_document, set current_chat_id/current_workspace_id,
    bus.publish("scene"), the "Loaded ..." notification) by calling straight
    through to the SAME `load_chat` closure register_chat_library's own
    loadChat intent is built from - not a second, parallel restore
    implementation. `load_chat` never returns a value (loadChat's own
    fire-and-forget contract is unchanged - this does not alter that
    function at all, only calls it), so this function does its own node
    lookup afterward rather than threading anything back through it.

    FAST PATH vs. RELOAD PATH - see _NON_REGULAR_NODE_KINDS' own comment
    immediately above for the full "node ids are NOT stable across a
    restore" reasoning this split exists to handle correctly:

      FAST PATH: `graph_id` already equals canvas_document's own
      current_chat_id (the target graph is already the one open, so NO
      restore runs here) - node_id is looked up directly against the live
      document. Correct because ids never change while a graph stays
      continuously loaded, and also a cheap, correctness-neutral
      optimization (skips a pointless full scene reload + notification).

      RELOAD PATH: a different graph was open (or none was), so load_chat
      runs a REAL restore, which mints entirely new ids for every node -
      node_id (recorded by a PAST save) can never be compared against
      those directly. Instead: the saved row is re-read (load_chat_row,
      the exact same row load_chat itself just restored FROM), node_id's
      ORDINAL POSITION is found within THAT row's own "nodes" array (the
      exact array _extract_indexable_node_chunks walked to produce this id
      in the first place - i.e. this is comparing node_id against the
      SAME payload it came from, never against live ids), and that ordinal
      is mapped onto the freshly-restored live document's own "regular"
      nodes (backend/session_save.py's own build_chat_data writes "nodes"
      from exactly this filtered, array-ordered set - _NON_REGULAR_NODE_
      KINDS' own exclusion mirrors that filter without importing session_
      save.py's private constants) in RESTORE order, which - since
      session_load.py restores each "nodes" array entry in array order -
      is the same order as the saved array. The Nth saved entry and the
      Nth freshly-restored live node are therefore the same node.

    Returns `{"x": ..., "y": ...}` (real floats) on success, or `None` when
    the target graph could not be loaded (a stale search result pointing at
    a since-deleted graph - load_chat already shows its own "could not be
    found" notification for that case, so this function raises nothing
    further), the target node's own saved id is no longer present in the
    CURRENT saved row at all (deleted since the graph was last saved/
    indexed), or the ordinal position it resolves to falls outside the
    freshly-restored live node list (a node that failed to restore for an
    unrelated reason shifted every later ordinal down by one - a rare,
    honestly-accepted degraded case, not a crash) - every one of these is
    the same honest "the world moved on since this was indexed" outcome, a
    stale search result, not a bug."""

    async def load_graph_and_focus_node(graph_id: int, node_id: str) -> dict[str, float] | None:
        if canvas_document is None:
            return None
        graph_id = int(graph_id)
        node_id = str(node_id)

        if canvas_document.current_chat_id == graph_id:
            # FAST PATH - see this factory's own docstring.
            node = canvas_document.nodes.get(node_id)
            return {"x": float(node.x), "y": float(node.y)} if node is not None else None

        await load_chat(graph_id)
        if canvas_document.current_chat_id != graph_id:
            # load_chat's own row-not-found branch never adopts graph_id as
            # current_chat_id - see that function's own docstring/body.
            # Nothing further to do: it already showed its own "could not
            # be found" notification.
            return None

        # RELOAD PATH - see this factory's own docstring for the full
        # ordinal-position reasoning.
        row = await asyncio.to_thread(load_chat_row, resolved_path, graph_id)
        if row is None:
            return None
        saved_data = row.get("data")
        saved_nodes = saved_data.get("nodes", []) if isinstance(saved_data, dict) else []
        ordinal = next(
            (index for index, saved_node in enumerate(saved_nodes)
             if isinstance(saved_node, dict) and saved_node.get("id") == node_id),
            None,
        )
        if ordinal is None:
            return None

        live_regular_nodes = [
            live_node for live_node in canvas_document.nodes.values()
            if live_node.kind not in _NON_REGULAR_NODE_KINDS
        ]
        if ordinal >= len(live_regular_nodes):
            return None
        node = live_regular_nodes[ordinal]
        return {"x": float(node.x), "y": float(node.y)}

    return load_graph_and_focus_node


def make_save_chat(
    bus: SessionBus,
    resolved_path: Path,
    canvas_document: SceneDocument | None,
    notifications: NotificationState | None,
    record_saved: Callable[..., None],
    last_saved: dict[str, Any],
    settings_manager: "SettingsManager | None" = None,
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
            chat_data = build_chat_data(
                canvas_document, asset_store=store_for(resolved_path), settings_manager=settings_manager,
            )
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
                # ADR-020 stage 20.2: only meaningful on the INSERT branch
                # (chat_id_for_save falsy) - see save_chat_atomically_row's
                # own workspace_id docstring. canvas_document.current_
                # workspace_id was already validated against a real
                # workspace by new_chat() before it was ever set.
                workspace_id=canvas_document.current_workspace_id,
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


# ADR-020 stage 20.2: args schemas for the 6 new intents on "app-chat-
# library", matching the ADR-003 dataclass-args_schema convention already
# established elsewhere in this codebase (backend/plugins.py's
# ExecutePluginArgs/InvokePluginIntentArgs/SetPluginGrantArgs, backend/
# notifications.py's ShowMessageArgs) - dataclass FIELD ORDER is the
# positional mapping dispatch_intent validates a real request's args
# against (see backend/events.py's _validate_intent_args). This topic's
# five PRE-EXISTING intents (renameChat/deleteChat/loadChat/saveChat/
# newChat, all registered with no args_schema at all - verified directly
# against this file, not assumed) are deliberately left as-is: retrofitting
# them is unrelated churn outside this stage's own scope, so only the
# genuinely NEW intents below opt into ADR-003 validation.
@dataclass
class SetGraphFavoriteArgs:
    graphId: int
    favorite: bool


@dataclass
class SetGraphArchivedArgs:
    graphId: int
    archived: bool


@dataclass
class SetGraphTagsArgs:
    """`tags` is the graph's FULL replacement tag set - a bulk replace, not
    an add/remove delta. See set_graph_tags' own docstring."""

    graphId: int
    tags: list[str]


@dataclass
class CreateWorkspaceArgs:
    name: str


@dataclass
class RenameWorkspaceArgs:
    workspaceId: int
    name: str


@dataclass
class ArchiveWorkspaceArgs:
    """Archiving (or unarchiving) a workspace never touches its graphs -
    see archive_workspace's own docstring."""

    workspaceId: int
    archived: bool


@dataclass
class SetWorkspaceDefaultModelArgs:
    """ADR-020 stage 20.3. `provider`/`modelId` both "" clears the
    workspace's default (set_workspace_default_model's own "both empty ->
    unset" contract) - the frontend's own picker sends both fields
    together on every real change, same "no partial value" shape
    set_model_override's own wire-side callers already follow."""

    workspaceId: int
    provider: str
    modelId: str


@dataclass
class LoadGraphAndFocusNodeArgs:
    """ADR-020 stage 20.4. See make_load_graph_and_focus_node's own
    docstring for the full request/reply contract this backs."""

    graphId: int
    nodeId: str


@dataclass
class ExportWorkspaceArgs:
    """ADR-020 stage 20.5. See register_chat_library's own export_workspace
    closure for the full contract this backs."""

    workspaceId: int


def _safe_export_filename(name: str) -> str:
    """Sanitizes a workspace name into a default `.graphlink` SAVE-dialog
    filename - a cosmetic prefill, not a security boundary (the user's own
    native OS dialog, not this string, decides the real write path - see
    native_dialogs.pick_save_file's own docstring). Unlike backend/assets.
    py's own _sanitize_chart_filename (which strips to ASCII because it
    feeds an HTTP header), this keeps full Unicode letters/digits - a real
    local filesystem filename has no such restriction - and only strips
    characters a filesystem path genuinely cannot contain (path separators,
    control characters, drive-letter colons) plus collapses whitespace to a
    single underscore, matching that function's own collapse convention."""
    text = str(name or "")
    safe = "".join(ch for ch in text if ch.isalnum() or ch in (" ", "-", "_")).strip()
    safe = re.sub(r"\s+", "_", safe)
    return safe or "workspace"


def make_export_workspace(bus: SessionBus, resolved_path: Path, notifications: NotificationState | None):
    """Factory for register_chat_library's own exportWorkspace intent -
    lifted out to a top-level function purely to keep register_chat_library
    itself under ADR-002's 300-line registration-function cap (stage 2.7),
    the same "one definition, kept under the cap" precedent make_serialize_
    mutating_intent/make_load_chat/make_load_graph_and_focus_node already
    established in this file.

    ADR-020 stage 20.5: exports every graph in `workspace_id` as one
    `.graphlink` archive - backend/workspace_archive.py's own export_archive,
    ADR-009 stage 9.4's own primitive (format/scrub/atomic-publish already
    built, tested, and simply never wired to a real intent until now - see
    that module's own docstring).

    Read-only against chats.db and never touches canvas_document, so - like
    register_chat_library's own createWorkspace/renameWorkspace/
    archiveWorkspace/setWorkspaceDefaultModel closures - this needs neither
    _serialize_mutating_intent's reentrancy guard nor an app-chat-library
    republish; nothing this function does changes what that topic's own
    snapshot would report.

    The native SAVE dialog (native_dialogs.pick_save_file) is what actually
    decides the destination - a cancelled dialog is a silent no-op, the SAME
    "no window/user-cancelled -> quiet return" contract pick_gitlink_local_
    root already established for every other native-dialog-backed intent in
    this app, not a special case invented here."""

    async def export_workspace(workspace_id: int):
        workspaces = await asyncio.to_thread(get_all_workspaces, resolved_path)
        workspace = next((w for w in workspaces if int(w["id"]) == int(workspace_id)), None)
        if workspace is None:
            return

        all_chats = await asyncio.to_thread(get_all_chats, resolved_path)
        graph_ids = [int(row["id"]) for row in all_chats if int(row["workspaceId"]) == int(workspace_id)]
        if not graph_ids:
            if notifications is not None:
                notifications.show(f'"{workspace["name"]}" has no graphs to export.', "warning")
                await bus.publish("notification")
            return

        default_name = f"{_safe_export_filename(str(workspace['name']))}.graphlink"
        try:
            target = await native_dialogs.pick_save_file(
                default_name, file_types=("Graphlink Archive (*.graphlink)",),
            )
        except Exception as exc:  # noqa: BLE001 - a local file path, not a credential
            if notifications is not None:
                notifications.show(f"Could not open the save dialog: {exc}", "error")
                await bus.publish("notification")
            return
        if not target:
            return

        def _load_one(graph_id: int) -> dict[str, Any]:
            row = load_chat_row(resolved_path, graph_id)
            return {
                "title": row["title"] if row else "Untitled",
                "data": row["data"] if row else {},
                "notes": load_notes_rows(resolved_path, graph_id),
                "pins": load_pins_rows(resolved_path, graph_id),
            }

        def _do_export() -> Path:
            chats = [_load_one(graph_id) for graph_id in graph_ids]
            return workspace_archive.export_archive(Path(target), chats, live_assets=store_for(resolved_path))

        try:
            written = await asyncio.to_thread(_do_export)
        except OSError as exc:
            if notifications is not None:
                notifications.show(f"Could not export workspace: {exc}", "error")
                await bus.publish("notification")
            return

        if notifications is not None:
            plural = "" if len(graph_ids) == 1 else "s"
            notifications.show(
                f'Exported {len(graph_ids)} graph{plural} from "{workspace["name"]}" to {written.name}.',
                "success",
            )
            await bus.publish("notification")

    return export_workspace


def make_delete_chat_intent(
    bus: SessionBus,
    resolved_path: Path,
    canvas_document: SceneDocument | None,
    last_saved: dict[str, Any],
):
    """Build the delete intent without inflating the registration function -
    same extraction the rename/load/save/export intents below already use.

    The caller wraps this in the shared chat-mutation guard, exactly like
    every other mutating intent on this topic. That matters rather than being
    cosmetic symmetry: this handler writes canvas_document.current_chat_id
    and last_saved, the same two cells a completing save/autosave writes, so
    an unguarded delete racing an in-flight save could leave the session
    pointed at a deleted row while the digest still claimed "already saved" -
    which is the silent no-autosave state the fix inside the handler itself
    exists to prevent.
    """

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
            last_saved["digest"] = None
            last_saved["chat_id"] = None
            last_saved["updated_at"] = None
        await bus.publish("app-chat-library")

    return delete


def make_rename_chat_intent(
    bus: SessionBus,
    resolved_path: Path,
    canvas_document: SceneDocument | None,
    notifications: NotificationState | None,
    last_saved: dict[str, Any],
):
    """Build the rename intent without inflating the registration function.

    Renaming the graph currently open in this session is part of the same
    optimistic-concurrency contract as saving its content: the metadata write
    must validate the token this session loaded and then replace it with the
    exact timestamp written by the rename. The caller wraps this handler in
    the shared chat-mutation guard, making the token update race-free against
    this session's save/load/autosave operations.
    """

    async def rename(chat_id: int, new_title: str):
        # Non-empty guard matches the legacy `if ok and new_title:` - an
        # empty/whitespace title is ignored, no mutation, no error (the SPA
        # disables Save for an empty draft anyway).
        title = str(new_title or "").strip()
        if not title:
            return
        resolved_chat_id = int(chat_id)
        expected_updated_at: str | None = None
        if (
            canvas_document is not None
            and canvas_document.current_chat_id == resolved_chat_id
            and last_saved.get("chat_id") == resolved_chat_id
        ):
            expected_updated_at = last_saved.get("updated_at")
        try:
            new_updated_at = await asyncio.to_thread(
                rename_chat,
                resolved_path,
                resolved_chat_id,
                title,
                expected_updated_at=expected_updated_at,
            )
        except ConcurrentSaveConflict:
            logger.warning(
                "renameChat: lost a save race for chat %r (session=%r) - not overwriting the newer title",
                resolved_chat_id,
                bus.session_id,
            )
            if notifications is not None:
                notifications.show(LOST_RACE_MESSAGE_MANUAL, "warning")
                await bus.publish("notification")
            return
        if (
            new_updated_at is not None
            and canvas_document is not None
            and canvas_document.current_chat_id == resolved_chat_id
            and last_saved.get("chat_id") == resolved_chat_id
        ):
            last_saved["updated_at"] = new_updated_at
        await bus.publish("app-chat-library")

    return rename


def register_chat_library(
    bus: SessionBus,
    db_path: Path | None = None,
    canvas_document: SceneDocument | None = None,
    notifications: NotificationState | None = None,
    *,
    autosave_interval_seconds: float | None = 30.0,
    settings_manager: "SettingsManager | None" = None,
) -> None:
    # ADR-014 review-fix: threaded through to build_chat_data/
    # restore_chat_into_document (via make_load_chat/make_save_chat/
    # register_autosave below) so a plugin node's own serialize/deserialize
    # hook is gated on its current Settings > Plugins grant on every real
    # save/load/autosave-tick, not just live-wire scene publishes - see
    # session_save.py's _serialize_plugin_node and session_load.py's
    # _restore_plugin_payload for the actual check. `None` (every existing
    # test call site of this function) preserves the exact prior ungated
    # behavior.
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

    # Adversarial review finding: loadChat/saveChat/newChat mutate the SAME
    # canvas_document, while renameChat advances that document's optimistic
    # save token; each awaits at least one asyncio.to_thread DB call - an
    # await point that yields control back to the event loop. A
    # session can have MULTIPLE attached WS connections at once (every tab/
    # window that doesn't pass its own ?session= query param shares
    # session="default" - see backend/app.py's ws_endpoint), so two tabs
    # racing Save/Load/New Chat could genuinely interleave mid-await and
    # silently overwrite or corrupt one another's work - there is no
    # per-window isolation here the way Qt's single-threaded-per-window
    # model gave legacy for free. This mutable flag - checked and set at
    # entry, cleared in a finally - serializes all four against each other,
    # the generalized (load/new/rename included, not just save)
    # counterpart of ChatSessionManager's own _is_saving reentrancy guard.
    #
    # OWNERSHIP (audit fix). The flag above was written when only a
    # user-initiated intent could ever hold it, which is what makes "drop the
    # second one and warn" an honest contract: the user really did start two
    # operations. R6.6 then had a BACKGROUND task claim the same flag, and the
    # asymmetry went unnoticed - an autosave tick that happened to be mid-write
    # made the user's own Save/Load/New Chat vanish, with a warning naming an
    # operation they never started. Rename now shares this guard as well: its
    # metadata write advances the open graph's optimistic-save token, which
    # must not race those operations. A background convenience feature must
    # never be able to beat the user to their own data.
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

    rename = make_rename_chat_intent(
        bus, resolved_path, canvas_document, notifications, _last_saved,
    )

    delete = make_delete_chat_intent(bus, resolved_path, canvas_document, _last_saved)

    load_chat = make_load_chat(
        bus, resolved_path, canvas_document, notifications, _record_saved, _last_saved, settings_manager,
    )
    # ADR-020 stage 20.4: built from the SAME `load_chat` closure just
    # above, not a second restore implementation - see make_load_graph_
    # and_focus_node's own docstring.
    load_graph_and_focus_node = make_load_graph_and_focus_node(resolved_path, canvas_document, load_chat)
    save_chat = make_save_chat(
        bus, resolved_path, canvas_document, notifications, _record_saved, _last_saved, settings_manager,
    )

    async def new_chat(workspace_id: int | None = None):
        # R6.5: the backend counterpart of legacy's "start with an empty
        # scene" - there is no legacy method this ports 1:1 (Qt's
        # ChatSessionManager has no "new chat" concept at all; a fresh scene
        # is just whatever exists before the first load/save of a session),
        # so this simply clears the live document and drops current_chat_id,
        # exactly like clear_for_load already does for a session LOAD.
        if canvas_document is None:
            return

        # ADR-020 stage 20.2: resolve/validate the caller's requested
        # workspace BEFORE clear_for_load() below (which itself resets
        # current_workspace_id to None) - an invalid/unknown workspaceId
        # (never existed, or since deleted - there is no deleteWorkspace
        # intent yet, but a caller could still send a stale/bogus id) falls
        # back to None here. None is exactly what save_chat_atomically_row's
        # own workspace_id=None default already resolves to the Default
        # workspace for, via graphs.workspace_id's own schema DEFAULT (see
        # that function's own docstring) - so "omitted or invalid ->
        # Default" is satisfied by ONE fallback path, not two. Every OTHER
        # caller of newChat in the codebase (e.g. commands.ts's palette "new
        # chat" command) keeps calling this with zero args, which is
        # `workspace_id=None` here - byte-identical pre-20.2 behavior.
        resolved_workspace_id: int | None = None
        if workspace_id is not None:
            try:
                candidate_workspace_id = int(workspace_id)
            except (TypeError, ValueError):
                candidate_workspace_id = None
            if candidate_workspace_id is not None:
                existing_workspace_ids = {
                    workspace["id"]
                    for workspace in await asyncio.to_thread(get_all_workspaces, resolved_path)
                }
                if candidate_workspace_id in existing_workspace_ids:
                    resolved_workspace_id = candidate_workspace_id

        canvas_document.clear_for_load()
        canvas_document.current_workspace_id = resolved_workspace_id
        # clear_for_load drops current_chat_id, so the save state that
        # described the old document no longer describes anything.
        _last_saved["digest"] = None
        _last_saved["chat_id"] = None
        _last_saved["updated_at"] = None
        await bus.publish("scene")

    # -- ADR-020 stage 20.2: the 6 new intents -------------------------------
    #
    # Mirror rename/delete's own shape immediately above: a single
    # asyncio.to_thread call into the matching CRUD function, then republish
    # "app-chat-library" so every attached window's list/switcher picks up
    # the change - the same pattern every existing intent on this topic
    # already uses. None of these six go through _serialize_mutating_intent:
    # they mutate workspace/list metadata only. renameChat is deliberately
    # different from these metadata helpers because renaming the open graph
    # advances and refreshes its shared optimistic-save token.

    async def set_favorite(graph_id: int, favorite: bool):
        await asyncio.to_thread(set_graph_favorite, resolved_path, int(graph_id), bool(favorite))
        await bus.publish("app-chat-library")

    async def set_archived(graph_id: int, archived: bool):
        await asyncio.to_thread(set_graph_archived, resolved_path, int(graph_id), bool(archived))
        await bus.publish("app-chat-library")

    async def set_tags(graph_id: int, tags: list[str]):
        # Server-side trim/dedupe/case-collapse happens inside
        # set_graph_tags itself (_normalize_tags) - never trusting the
        # client's own list, matching this stage's own wire-contract
        # requirement.
        await asyncio.to_thread(set_graph_tags, resolved_path, int(graph_id), list(tags))
        await bus.publish("app-chat-library")

    async def create_ws(name: str):
        # create_workspace's own empty/whitespace-only guard returns None
        # without mutating anything - branching on that return value (rather
        # than re-checking `name` here too) keeps the single source of
        # truth for "what counts as a valid workspace name" in one place.
        created = await asyncio.to_thread(create_workspace, resolved_path, name)
        if created is None:
            if notifications is not None:
                notifications.show("Workspace name cannot be empty.", "warning")
                await bus.publish("notification")
            return
        await bus.publish("app-chat-library")

    async def rename_ws(workspace_id: int, name: str):
        # Same non-empty guard as rename (renameChat) immediately above -
        # an empty/whitespace name is ignored, no mutation, no error, no
        # republish.
        title = str(name or "").strip()
        if not title:
            return
        await asyncio.to_thread(rename_workspace, resolved_path, int(workspace_id), title)
        await bus.publish("app-chat-library")

    async def archive_ws(workspace_id: int, archived: bool):
        await asyncio.to_thread(archive_workspace, resolved_path, int(workspace_id), bool(archived))
        await bus.publish("app-chat-library")

    async def set_workspace_default_model_intent(workspace_id: int, provider: str, model_id: str):
        # ADR-020 stage 20.3: same shape as every other single-field
        # workspace mutation above - one asyncio.to_thread call into the
        # matching CRUD function, then republish so the switcher's own
        # settings affordance picks up the new value. Never touches
        # canvas_document (only a chats.db row), so - like createWorkspace/
        # renameWorkspace/archiveWorkspace above - it does not go through
        # _serialize_mutating_intent.
        await asyncio.to_thread(
            set_workspace_default_model, resolved_path, int(workspace_id), str(provider), str(model_id),
        )
        await bus.publish("app-chat-library")

    export_workspace = make_export_workspace(bus, resolved_path, notifications)

    bus.register_intent("app-chat-library", "renameChat", _serialize_mutating_intent(rename))
    # Serialized like every other mutating intent here. It was the one
    # exception, and that was a real hazard rather than an oversight with no
    # consequence: delete mutates canvas_document.current_chat_id and
    # _last_saved, the same two cells an in-flight save/autosave writes when
    # it completes. Interleaved - delete lands while a save for the very row
    # being deleted is still inside save_chat_atomically_row's post-commit
    # knowledge reindex - delete's continuation clears both cells, then the
    # save's continuation repopulates them, leaving the session pointed at a
    # deleted row with a digest claiming "already saved". That is exactly the
    # silent-no-autosave state the audit fix inside delete() above was written
    # to prevent, reachable by racing it rather than by the path it guarded.
    bus.register_intent("app-chat-library", "deleteChat", _serialize_mutating_intent(delete))
    bus.register_intent("app-chat-library", "loadChat", _serialize_mutating_intent(load_chat))
    bus.register_intent("app-chat-library", "saveChat", _serialize_mutating_intent(save_chat))
    bus.register_intent("app-chat-library", "newChat", _serialize_mutating_intent(new_chat))
    # ADR-020 stage 20.4: REQUEST/REPLY (the frontend uses transport.
    # request(...), not fireIntent) - see make_load_graph_and_focus_node's
    # own docstring for why this needs the SAME reentrancy guard loadChat/
    # saveChat/newChat get (it also mutates canvas_document, via load_chat,
    # on the non-fast-path) and why _serialize_mutating_intent's own recent
    # "return the handler's result" fix (see that factory's own docstring)
    # is what makes returning a real value through this wrapper work at all.
    bus.register_intent(
        "app-chat-library", "loadGraphAndFocusNode",
        _serialize_mutating_intent(load_graph_and_focus_node),
        args_schema=LoadGraphAndFocusNodeArgs,
    )
    bus.register_intent(
        "app-chat-library", "setGraphFavorite", set_favorite, args_schema=SetGraphFavoriteArgs,
    )
    bus.register_intent(
        "app-chat-library", "setGraphArchived", set_archived, args_schema=SetGraphArchivedArgs,
    )
    bus.register_intent("app-chat-library", "setGraphTags", set_tags, args_schema=SetGraphTagsArgs)
    bus.register_intent("app-chat-library", "createWorkspace", create_ws, args_schema=CreateWorkspaceArgs)
    bus.register_intent("app-chat-library", "renameWorkspace", rename_ws, args_schema=RenameWorkspaceArgs)
    bus.register_intent("app-chat-library", "archiveWorkspace", archive_ws, args_schema=ArchiveWorkspaceArgs)
    bus.register_intent(
        "app-chat-library", "setWorkspaceDefaultModel", set_workspace_default_model_intent,
        args_schema=SetWorkspaceDefaultModelArgs,
    )
    bus.register_intent("app-chat-library", "exportWorkspace", export_workspace, args_schema=ExportWorkspaceArgs)

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
            interval_seconds=autosave_interval_seconds, settings_manager=settings_manager,
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
            # ADR-020 stage 20.2: same reasoning as make_save_chat's own
            # save_chat closure - only consulted on the INSERT branch.
            workspace_id=canvas_document.current_workspace_id,
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
