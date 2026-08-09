"""ADR-017 stage 17.1: the local knowledge store - documents/chunks/
collections persisted in their own SQLite file, `~/.graphlink/knowledge/
knowledge.db`, deliberately separate from backend/chat_library.py's
chats.db (see ADR-017's own schema sketch - this is a distinct store, not
new tables bolted onto the chat database).

Mirrors backend/chat_library.py's own `_connect()` shape byte-for-byte in
spirit (WAL mode, busy_timeout, chmod 0600, the migration-runner call, the
OperationalError-vs-DatabaseError corruption split, quarantine + restore-
from-backup on corruption) - see that module's own `_connect()` docstring
for the full empirical reasoning behind each piece; not re-derived here.
Deliberately NOT a shared helper the two modules both import: the two
differ in real, small ways (this store never held a "pre-migration legacy
shape" the way chats.db did, so its own migration is simpler; the backup
filename prefix differs) and this codebase's own established precedent
(chat_library.py's `_quarantine_corrupt_chats_db`'s own docstring) is to
duplicate ~100 lines with an explanatory comment over adding a new shared
dependency between the two stores for it.

CONTENT-HASH IDEMPOTENCY (ADR-017 decision #2, "Idempotent by content
hash"): `documents.content_hash` is SHA-256 of the extracted text (not the
raw file bytes - two different source files that happen to extract to
identical text, e.g. a .txt and a .md copy of the same content, are
legitimately the same document for retrieval purposes), and uniqueness is
scoped to `(content_hash, collection_id)` - the SAME content re-ingested
into the SAME collection is a no-op (returns the existing document's id,
no new chunks written), but the SAME content ingested into two DIFFERENT
collections is deliberately two separate document rows: this store has no
multi-collection-membership concept, and collapsing them would mean a
later "delete this collection" has to reason about whether some other
collection still needs the content it's about to remove. `collection_id`
uses `0` as its "no collection assigned" sentinel, never SQL NULL - a
plain `UNIQUE(content_hash, collection_id)` constraint on NULL columns
would not enforce what it looks like it enforces (SQL NULL is never equal
to another NULL, so two unscoped documents with identical content would
NOT collide), and `collections.id` is an AUTOINCREMENT primary key that
never produces 0, so the sentinel can never collide with a real row.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from backend import db_backup
from backend.notifications import NotificationState
from graphlink_migrations import run_sqlite_migrations

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".graphlink" / "knowledge" / "knowledge.db"

# Distinguishes this store's backups from chats.db's own in the SAME
# backend/db_backup.py module - see that module's own stage-17.1 comment on
# why the prefix needed parametrizing at all.
BACKUP_FILENAME_PREFIX = "knowledge-"

# Mirrors backend/chat_library.py's own BACKUP_CADENCE_SECONDS (600s) -
# ingestion is bursty (one folder-ingest action can insert many documents
# in a row) rather than a steady 30s-autosave-tick cadence, but the same
# "first write of a session always backs up, then at most once per cadence
# after that" policy applies for the same reason: cheap insurance against
# a mid-batch crash without re-snapshotting on every single document.
BACKUP_CADENCE_SECONDS = 600.0

KNOWLEDGE_DB_SCHEMA_VERSION = 1


def content_hash(text: str) -> str:
    """SHA-256 of the extracted text (UTF-8 encoded), matching
    backend/asset_store.py's own content_ref() convention (SHA-256 hex
    digest names the content) applied to text instead of binary bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _migration_001_initial_schema(conn: sqlite3.Connection) -> None:
    """0 -> 1: the whole 17.1 schema in one migration - this is a brand
    new database with no pre-existing legacy shape to accommodate (unlike
    chat_library.py's own migration 1, which had to be correct for an
    already-populated chats.db too), so every statement is a plain CREATE
    TABLE/INDEX, no guarded ALTER TABLE probing needed. `embeddings` and
    `chunks_fts` (ADR-017 stages 17.3/17.2) are NOT created here - they
    land in their own later migrations when the code that populates them
    exists, matching chat_library.py's own "add a new numbered step for
    the next real schema change, never fold it retroactively into an
    already-shipped one" precedent."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id INTEGER NOT NULL DEFAULT 0,
            source_uri TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            mime TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            added_at TEXT NOT NULL DEFAULT '',
            UNIQUE (content_hash, collection_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_collection_id ON documents (collection_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            text TEXT NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0,
            offset_start INTEGER NOT NULL,
            offset_end INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks (document_id)")


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migration_001_initial_schema,
}


def _connect(
    db_path: Path, *, notifications: NotificationState | None = None, _retry: bool = False,
) -> sqlite3.Connection:
    """Mirrors backend/chat_library.py's own `_connect()` - see that
    function's docstring for the full empirical reasoning behind each
    PRAGMA/ordering choice (WAL mode surfacing corruption on the FIRST real
    touch of the file, chmod happening after journal_mode so the sidecars
    it creates are caught, migrations running on every connect as a cheap
    no-op once already current). Not shared code: see this module's own
    docstring for why."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
    except sqlite3.OperationalError:
        if conn is not None:
            conn.close()
        raise
    except sqlite3.DatabaseError as exc:
        if conn is not None:
            conn.close()
        if _retry:
            raise
        _rescue_corrupt_knowledge_db(db_path, exc, notifications)
        return _connect(db_path, notifications=notifications, _retry=True)

    for path in (db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
        if path.exists():
            try:
                os.chmod(path, 0o600)
            except OSError:
                logger.warning("could not chmod %s to 0600 - continuing with existing permissions", path)

    try:
        run_sqlite_migrations(conn, KNOWLEDGE_DB_SCHEMA_VERSION, _MIGRATIONS)
    except sqlite3.OperationalError:
        conn.close()
        raise
    except sqlite3.DatabaseError as exc:
        conn.close()
        if _retry:
            raise
        _rescue_corrupt_knowledge_db(db_path, exc, notifications)
        return _connect(db_path, notifications=notifications, _retry=True)
    return conn


def _quarantine_corrupt_knowledge_db(db_path: Path, error: Exception) -> Path | None:
    """Mirrors backend/chat_library.py's own `_quarantine_corrupt_chats_db`
    exactly (same timestamp convention, same Path.replace atomic rename,
    same 0600 + WAL-sidecar cleanup) - see that function's own docstring."""
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

    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                logger.warning("could not remove stale sidecar %s - continuing", sidecar)

    logger.error("%s was corrupt (%s) - quarantined to %s", db_path, error, quarantine_path)
    return quarantine_path


def _rescue_corrupt_knowledge_db(
    db_path: Path, error: Exception, notifications: NotificationState | None,
) -> None:
    """Mirrors backend/chat_library.py's own `_rescue_corrupt_chats_db` -
    quarantine, then restore the newest backup (this store's own
    "knowledge-" prefix) if one exists, else start fresh."""
    quarantine_path = _quarantine_corrupt_knowledge_db(db_path, error)
    if quarantine_path is None:
        if notifications is not None:
            notifications.show(
                "Your knowledge store appears to be corrupted and could not be automatically "
                "repaired. See graphlink.log for details.",
                "error",
            )
        return

    restored_from = db_backup.restore_from_newest_backup(db_path, prefix=BACKUP_FILENAME_PREFIX)
    if restored_from is not None:
        message = (
            "Your knowledge store was corrupted and has been restored from a recent backup. "
            f"The corrupted file was saved as {quarantine_path.name} in your .graphlink/knowledge "
            "folder in case you need it."
        )
    else:
        message = (
            "Your knowledge store was corrupted and no backup was available, so an empty store "
            f"was started. The corrupted file was saved as {quarantine_path.name} in your "
            ".graphlink/knowledge folder in case it can be recovered."
        )
    logger.error(
        "%s corruption rescue complete: quarantined=%s restored_from_backup=%s",
        db_path, quarantine_path, restored_from,
    )
    if notifications is not None:
        notifications.show(message, "warning")


def maybe_backup_before_write(db_path: Path, last_saved: dict[str, Any]) -> None:
    """Mirrors backend/chat_library.py's own `_maybe_backup_before_write` -
    same shared-cell cadence policy (first write of a session always backs
    up; every write after that only once BACKUP_CADENCE_SECONDS have
    elapsed), same "failure is logged and swallowed, never blocks the
    actual write" posture. `last_saved` is a plain caller-owned dict with
    one key this function reads/writes, `"last_backup_at"` - callers that
    want independent cadences (e.g. one per ingestion session) simply pass
    separate dicts."""
    now = time.monotonic()
    last_backup_at = last_saved.get("last_backup_at")
    if last_backup_at is not None and (now - last_backup_at) < BACKUP_CADENCE_SECONDS:
        return
    try:
        db_backup.take_backup(db_path, prefix=BACKUP_FILENAME_PREFIX)
    except Exception:
        logger.exception("knowledge.db backup failed - continuing with the write anyway")
    last_saved["last_backup_at"] = now


# -- documents/chunks CRUD ---------------------------------------------------


class IngestOutcome(NamedTuple):
    document_id: int
    chunk_count: int
    was_new: bool


def get_document_by_hash(conn: sqlite3.Connection, *, content_hash_value: str, collection_id: int = 0) -> int | None:
    row = conn.execute(
        "SELECT id FROM documents WHERE content_hash = ? AND collection_id = ?",
        (content_hash_value, collection_id),
    ).fetchone()
    return row[0] if row is not None else None


def add_document_with_chunks(
    db_path: Path,
    *,
    source_uri: str,
    title: str,
    mime: str,
    text: str,
    chunks: list,
    collection_id: int = 0,
    notifications: NotificationState | None = None,
    last_saved: dict[str, Any] | None = None,
) -> IngestOutcome:
    """The one write entry point for stage 17.1's ingestion pipeline:
    content-hash idempotency check, then (only on a genuine miss) one
    document row + all of its chunk rows, all inside a single transaction -
    a crash mid-insert can never leave a document with only SOME of its
    chunks. `chunks` is a list of backend.knowledge_chunking.TextChunk (not
    type-annotated as such here to avoid this module needing to import a
    dataclass purely for a type hint - duck-typed on
    `.text`/`.ordinal`/`.token_count`/`.offset_start`/`.offset_end`).

    Returns the EXISTING document's id with `was_new=False` and
    `chunk_count` read from the already-stored rows (never re-chunks or
    re-inserts) when `(content_hash(text), collection_id)` already exists -
    see this module's own docstring for the exact idempotency scope.

    `last_saved`/`notifications` are optional and threaded straight to
    maybe_backup_before_write/the corruption-rescue path respectively -
    omitted (None) by any caller that does not want either (e.g. a unit
    test using a throwaway tmp_path db)."""
    conn = _connect(db_path, notifications=notifications)
    try:
        hash_value = content_hash(text)
        with conn:
            existing_id = get_document_by_hash(conn, content_hash_value=hash_value, collection_id=collection_id)
            if existing_id is not None:
                existing_count = conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,),
                ).fetchone()[0]
                return IngestOutcome(document_id=existing_id, chunk_count=existing_count, was_new=False)

            if last_saved is not None:
                maybe_backup_before_write(db_path, last_saved)

            cursor = conn.execute(
                "INSERT INTO documents (collection_id, source_uri, title, mime, content_hash, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (collection_id, source_uri, title, mime, hash_value, _now_iso()),
            )
            document_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO chunks (document_id, ordinal, text, token_count, offset_start, offset_end) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (document_id, chunk.ordinal, chunk.text, chunk.token_count, chunk.offset_start, chunk.offset_end)
                    for chunk in chunks
                ],
            )
            return IngestOutcome(document_id=document_id, chunk_count=len(chunks), was_new=True)
    finally:
        conn.close()


def get_document(db_path: Path, document_id: int) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, collection_id, source_uri, title, mime, content_hash, added_at "
            "FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "collection_id": row[1], "source_uri": row[2],
            "title": row[3], "mime": row[4], "content_hash": row[5], "added_at": row[6],
        }
    finally:
        conn.close()


def list_documents(db_path: Path, *, collection_id: int | None = None) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        if collection_id is None:
            rows = conn.execute(
                "SELECT id, collection_id, source_uri, title, mime, content_hash, added_at "
                "FROM documents ORDER BY added_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, collection_id, source_uri, title, mime, content_hash, added_at "
                "FROM documents WHERE collection_id = ? ORDER BY added_at DESC",
                (collection_id,),
            ).fetchall()
        return [
            {
                "id": row[0], "collection_id": row[1], "source_uri": row[2],
                "title": row[3], "mime": row[4], "content_hash": row[5], "added_at": row[6],
            }
            for row in rows
        ]
    finally:
        conn.close()


def list_chunks_for_document(db_path: Path, document_id: int) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, ordinal, text, token_count, offset_start, offset_end "
            "FROM chunks WHERE document_id = ? ORDER BY ordinal ASC",
            (document_id,),
        ).fetchall()
        return [
            {
                "id": row[0], "ordinal": row[1], "text": row[2],
                "token_count": row[3], "offset_start": row[4], "offset_end": row[5],
            }
            for row in rows
        ]
    finally:
        conn.close()


def delete_document(db_path: Path, document_id: int) -> bool:
    """Deletes a document and (via ON DELETE CASCADE) every chunk that
    belonged to it. Returns whether a row was actually deleted - False for
    an already-absent id is a normal outcome, not an error."""
    conn = _connect(db_path)
    try:
        with conn:
            cursor = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            return cursor.rowcount > 0
    finally:
        conn.close()
