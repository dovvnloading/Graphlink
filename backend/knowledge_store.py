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

WORKSPACE-SCOPED KNOWLEDGE (ADR-020 stage 20.3) - SCOPING DECISION, made
explicit here rather than left to be inferred: this stage gives every
backend/chat_library.py workspace EXACTLY ONE collection (`collections.
workspace_id`, migration "4" below), created lazily on first real ingest/
search via get_or_create_workspace_collection - NOT a general, user-facing
"create/rename/delete many named collections per workspace" feature. That
larger surface (a collection browser, assigning documents to specific
collections, etc.) is deliberately out of scope for this stage - the exit
criterion this stage is built around is "two workspaces' corpora are
separate", not "users can manage arbitrarily many collections" - and is
left for a future stage to build only if it turns out to be needed. The pre-20.3 `collection_id=0` global/unscoped pool is migrated into
the Default workspace's collection during session initialization (see
move_unscoped_documents_to_workspace). Standalone callers with no
workspace context may still use that sentinel; app sessions always have
a current workspace.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import stat
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from backend import db_backup
from backend.notifications import NotificationState
from graphlink_migrations import run_sqlite_migrations
from graphlink_token_estimator import TokenEstimator

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

KNOWLEDGE_DB_SCHEMA_VERSION = 9


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


def _migration_002_fts5_lexical_index(conn: sqlite3.Connection) -> None:
    """1 -> 2 (ADR-017 stage 17.2): an FTS5 external-content index over
    `chunks.text`, kept in sync by triggers rather than by every write
    call site remembering to double-write - the standard SQLite pattern
    for `content=`/`content_rowid=` FTS5 tables (see the SQLite docs' own
    "External Content Tables" section). Only INSERT/DELETE triggers exist:
    `chunks` rows are never UPDATEd anywhere in this codebase (an ingest
    that changes content produces a NEW document+chunks via the content-
    hash path - backend.knowledge_store's own module docstring), so an
    UPDATE trigger would be untested dead code.

    The DELETE trigger fires for both a direct `delete_document` call and
    a `documents` row's ON DELETE CASCADE (SQLite's cascade is itself
    implemented as real DELETE statements against the child table, so the
    child table's own triggers still run) - chunks_fts never accumulates
    orphaned rows either way.

    The final INSERT backfills any chunk rows that predate this migration
    (an already-populated stage-17.1-only knowledge.db upgrading in
    place) - a no-op SELECT on a brand new database."""
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text,
            content='chunks',
            content_rowid='id'
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
        END
        """
    )
    conn.execute("INSERT INTO chunks_fts(rowid, text) SELECT id, text FROM chunks")


def _migration_003_vector_index(conn: sqlite3.Connection) -> None:
    """2 -> 3 (ADR-017 stage 17.3): the vector-embedding cache/index - the
    ADR's own schema sketch's `embeddings` table. Keyed by
    `(chunk_id, model_id)`, NOT a bare `chunk_id`: switching embedding
    models (or trying a second one alongside the first) must not collide
    with or overwrite a still-valid vector for the model that produced it,
    and re-running an embed pass after a partial failure must skip chunks
    that already have a row for THIS model - the exit criterion's own
    "cache prevents re-embedding" (ADR-017 doc, stage 17.3 row). Ordinary
    FK ON DELETE CASCADE (not a trigger, unlike chunks_fts - this is a
    plain table, not an FTS5 external-content one) means a document delete
    or content-hash-idempotent skip never orphans embedding rows.

    `vector` is a packed little-endian float32 BLOB (struct.pack via
    backend.knowledge_embeddings' own pack/unpack helpers) rather than JSON
    text - a 768-dim vector is 3KB as float32 bytes vs. ~10x that as a JSON
    array of decimal strings, and this table is written once per chunk per
    model then read back in bulk for every vector search. `dim` is stored
    redundantly (recoverable from `len(vector) // 4`) so a dimension
    mismatch from a swapped embedding model is a cheap integer comparison,
    not a silent shape error deep in a numpy call."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            chunk_id INTEGER NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
            model_id TEXT NOT NULL,
            dim INTEGER NOT NULL,
            vector BLOB NOT NULL,
            PRIMARY KEY (chunk_id, model_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_model_id ON embeddings (model_id)")


def _migration_004_workspace_scoped_collections(conn: sqlite3.Connection) -> None:
    """3 -> 4 (ADR-020 stage 20.3): `collections.workspace_id` - scopes each
    collection (and, transitively, every document/chunk ingested into it)
    to a real backend/chat_library.py `workspaces` row - a DIFFERENT SQLite
    database file (chats.db, not this module's own knowledge.db), so no
    declared SQL FOREIGN KEY is possible across the two; this is a
    conceptual FK enforced by this codebase's own CRUD only, same posture
    as backend/chat_library.py's own graphs.workspace_id -> workspaces.id
    link (see that migration's own docstring for the identical empirically-
    grounded reasoning about undeclared cross-store/ADD-COLUMN FKs).

    NULL (not 0) is "no workspace assigned" here - deliberately NOT the
    same sentinel as documents.collection_id's own `0` ("no collection
    assigned" - this module's own docstring), which already means
    something real and different: every document ingested before this
    stage (and every one ingested by a caller that still has no workspace
    context of its own - see backend/api/intents_knowledge.py's own
    fallback) keeps landing in collection_id 0, the pre-20.3 global/
    unscoped pool, completely untouched by this migration. A collections
    row with workspace_id NULL is simply one this stage's own code never
    produces (get_or_create_workspace_collection below always supplies a
    real int) - nullable rather than defaulting to some sentinel int so a
    later migration can tell "predates workspace-scoping" apart from
    "deliberately global" without overloading one column for both.

    Guarded exactly like every migration in this module and its sibling
    backend/chat_library.py: a PRAGMA table_info probe before the ALTER
    TABLE, so a second run against an already-migrated database is a pure
    no-op."""
    collections_columns = [info[1] for info in conn.execute("PRAGMA table_info(collections)").fetchall()]
    if "workspace_id" not in collections_columns:
        conn.execute("ALTER TABLE collections ADD COLUMN workspace_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_collections_workspace_id ON collections (workspace_id)")


def _migration_005_graph_node_chunks(conn: sqlite3.Connection) -> None:
    """4 -> 5 (ADR-020 stage 20.4): `chunks.source_node_id` - the column
    that lets a search HIT be traced back to which node, inside which
    graph, produced it. NULL for every chunk that predates this stage (a
    real ingested document's chunk - backend.knowledge_ingest's own
    add_document_with_chunks call path never sets it) and for every chunk
    a real document ingest produces from here on too; populated ONLY by
    backend/chat_library.py's own graph-reindexing path
    (knowledge_store.reindex_graph_content, below), one row per node in a
    saved chat graph.

    `documents.source_uri` already uniquely identifies WHICH graph a chunk
    came from (the synthetic `f"graph:{graph_id}"` scheme reindex_graph_
    content writes - see that function's own docstring), so no analogous
    "source_graph_id" column is needed on either table: the graph id is
    always one `int(source_uri.removeprefix("graph:"))` away from a
    document row already joined into every real query here (search_chunks,
    below).

    Guarded exactly like every migration in this module and its sibling
    backend/chat_library.py: a PRAGMA table_info probe before the ALTER
    TABLE, so a second run against an already-migrated database is a pure
    no-op."""
    chunks_columns = [info[1] for info in conn.execute("PRAGMA table_info(chunks)").fetchall()]
    if "source_node_id" not in chunks_columns:
        conn.execute("ALTER TABLE chunks ADD COLUMN source_node_id TEXT")


def _migration_006_unique_workspace_collections(conn: sqlite3.Connection) -> None:
    """5 -> 6: closes a real race in get_or_create_workspace_collection - a
    plain SELECT-then-INSERT with no unique constraint on workspace_id, so
    two concurrent first-time calls for the same brand-new workspace
    (reachable via asyncio.to_thread from several independent call sites -
    a chat's own reindex-on-save, a search call, a branch-indexing toggle -
    with no lock anywhere serializing them) could both observe no existing
    row and both INSERT, silently splitting that workspace's knowledge base
    across two collections rows with no error anywhere. Proven with a
    forced two-thread race before this fix: both INSERTs succeeded,
    producing two rows for one workspace_id.

    Two parts, in order: first de-duplicate any collections rows a
    database ALREADY carries from before this fix existed (this stage
    cannot assume a fresh install - a real user's knowledge.db may already
    have hit the race), then add the constraint that prevents it from ever
    recurring. Guarded like every migration in this module: the dedup pass
    is a no-op when there is nothing to deduplicate, and the index creation
    uses IF NOT EXISTS, so a second run against an already-migrated
    database does nothing either time."""
    duplicate_groups = conn.execute(
        """
        SELECT workspace_id, MIN(id) AS survivor_id
        FROM collections
        WHERE workspace_id IS NOT NULL
        GROUP BY workspace_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for workspace_id, survivor_id in duplicate_groups:
        loser_ids = [
            row[0] for row in conn.execute(
                "SELECT id FROM collections WHERE workspace_id = ? AND id != ?",
                (workspace_id, survivor_id),
            ).fetchall()
        ]
        for loser_id in loser_ids:
            # Re-point this loser's documents to the survivor ONE ROW AT A
            # TIME, not a bulk UPDATE: documents carries its own
            # UNIQUE(content_hash, collection_id), and a document under the
            # loser can legitimately share a content_hash with one already
            # under the survivor - the same real content, ingested twice
            # because of the exact race this migration exists to close. A
            # bulk UPDATE would abort the whole statement on that one
            # collision. Per row: re-point when there is no clash; when
            # there IS one, the survivor already holds this exact content
            # (content_hash equality means byte-identical text), so the
            # loser's duplicate document - and its chunks, via ON DELETE
            # CASCADE - is simply dropped. No real content is lost either
            # way.
            for doc_id, content_hash in conn.execute(
                "SELECT id, content_hash FROM documents WHERE collection_id = ?", (loser_id,)
            ).fetchall():
                clash = conn.execute(
                    "SELECT 1 FROM documents WHERE collection_id = ? AND content_hash = ?",
                    (survivor_id, content_hash),
                ).fetchone()
                if clash is not None:
                    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                else:
                    conn.execute("UPDATE documents SET collection_id = ? WHERE id = ?", (survivor_id, doc_id))
            conn.execute("DELETE FROM collections WHERE id = ?", (loser_id,))

    # Partial (WHERE workspace_id IS NOT NULL), not a plain UNIQUE index:
    # workspace_id is nullable (pre-20.3 collections rows, and any future
    # caller with no workspace context), and multiple NULLs must stay
    # legal - a plain unique index would reject the second NULL row, which
    # is not the invariant this migration is protecting.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_workspace_id_unique "
        "ON collections (workspace_id) WHERE workspace_id IS NOT NULL"
    )



def _migration_007_file_sources(conn: sqlite3.Connection) -> None:
    """Track every imported file path, even when several paths share one document.

    The documents table deduplicates extracted content. This mapping makes a
    change to one aliased path independent of the other files that still refer
    to the old content, without duplicating chunks or their FTS entries.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS file_sources (
            collection_id INTEGER NOT NULL,
            source_uri TEXT NOT NULL,
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            PRIMARY KEY (collection_id, source_uri)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_file_sources_document ON file_sources(document_id)")


def _migration_008_document_size(conn: sqlite3.Connection) -> None:
    """Keep the actual source byte size of newly imported files, not a guess from text chunks."""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()]
    if "size_bytes" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN size_bytes INTEGER")


def _migration_009_watched_folders(conn: sqlite3.Connection) -> None:
    """Opt-in, persistent folder subscriptions and successfully indexed file stamps.

    Membership is separate from file_sources so deleting a watched path cannot
    remove another watched folder's alias or a non-file document sharing its text.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watched_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            last_sync_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            UNIQUE (workspace_id, path)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watched_folder_files (
            folder_id INTEGER NOT NULL REFERENCES watched_folders(id) ON DELETE CASCADE,
            source_uri TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            PRIMARY KEY (folder_id, source_uri)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watched_folder_files_uri ON watched_folder_files (source_uri)")


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migration_001_initial_schema,
    2: _migration_002_fts5_lexical_index,
    3: _migration_003_vector_index,
    4: _migration_004_workspace_scoped_collections,
    5: _migration_005_graph_node_chunks,
    6: _migration_006_unique_workspace_collections,
    7: _migration_007_file_sources,
    8: _migration_008_document_size,
    9: _migration_009_watched_folders,
}


def _secure_knowledge_paths(db_path: Path) -> None:
    """Make the directory private and the DB/sidecars 0600 before SQLite opens.

    sqlite3.connect and journal_mode=WAL can create files before a later
    chmod. With a permissive process umask this briefly exposes knowledge,
    including during a crash. Open without following symlinks and set modes
    on file descriptors; repeat after WAL creation to cover its sidecars.
    """
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        # Windows access control uses inherited ACLs, not POSIX mode bits.
        return
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory_flag:
        raise OSError("Private knowledge storage requires no-follow file opens.")
    parent_fd = os.open(db_path.parent, os.O_RDONLY | directory_flag | nofollow)
    try:
        os.fchmod(parent_fd, 0o700)
        for suffix in ("", "-wal", "-shm"):
            name = db_path.name + suffix
            flags = os.O_RDWR | nofollow if not suffix else os.O_RDONLY | nofollow
            if not suffix:
                flags |= os.O_CREAT
            else:
                # Check even dangling links before SQLite can follow them.
                sidecar = db_path.with_name(name)
                if sidecar.is_symlink():
                    raise OSError("Knowledge SQLite sidecar must not be a symlink.")
                if not sidecar.exists():
                    continue
            fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("Knowledge SQLite path is not a regular file.")
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
    finally:
        os.close(parent_fd)


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
    _secure_knowledge_paths(db_path)
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

    try:
        _secure_knowledge_paths(db_path)
    except OSError:
        conn.close()
        raise

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

    restored_from = db_backup.restore_from_newest_backup(db_path, prefix=BACKUP_FILENAME_PREFIX, required_table="documents")
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

            try:
                cursor = conn.execute(
                    "INSERT INTO documents (collection_id, source_uri, title, mime, content_hash, added_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (collection_id, source_uri, title, mime, hash_value, _now_iso()),
                )
            except sqlite3.IntegrityError:
                # REVIEW-FIX: the get_document_by_hash check above and this
                # INSERT are not one atomic step - a second, fully separate
                # connection (every real caller reaches this via
                # asyncio.to_thread, genuine OS threads) can run the
                # identical "no existing row" SELECT, then INSERT sequence
                # concurrently for the same (content_hash, collection_id).
                # Before this fix, the LOSING connection's INSERT hit the
                # documents table's own UNIQUE(content_hash, collection_id)
                # constraint and raised sqlite3.IntegrityError unhandled -
                # this function's own docstring promises "always a no-op"
                # for a content_hash that already exists, so an unhandled
                # exception here broke that contract instead of the losing
                # call simply returning the winner's already-committed
                # document, same fix shape as get_or_create_workspace_
                # collection's own identical race, above.
                existing_id = get_document_by_hash(conn, content_hash_value=hash_value, collection_id=collection_id)
                if existing_id is None:
                    raise  # the constraint rejected this INSERT for some other reason - do not mask it
                existing_count = conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing_id,),
                ).fetchone()[0]
                return IngestOutcome(document_id=existing_id, chunk_count=existing_count, was_new=False)

            # lastrowid is Optional only because it is None before the first
            # INSERT on a cursor; this reads it directly after one.
            document_id: int = cursor.lastrowid  # type: ignore[assignment]
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


def replace_branch_document_with_chunks(
    db_path: Path, *, source_uri: str, title: str, text: str, chunks: list,
    collection_id: int = 0,
) -> IngestOutcome:
    """Atomically replace one mutable branch source, never an unrelated file.

    Unlike immutable-file ingestion, branch identity is a durable source URI,
    not its content hash. Include the URI in this row's hash so two different
    branches with identical text never collapse into one attribution record.
    BEGIN IMMEDIATE locks the read/replace sequence against concurrent
    indexing of the same source, just like graph reindexing does.
    """
    if not source_uri.startswith("branch:"):
        raise ValueError("mutable branch source must use the branch: scheme")
    hash_value = content_hash(f"{source_uri}\n{text}")
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT id, content_hash FROM documents WHERE collection_id = ? AND source_uri = ?",
                (collection_id, source_uri),
            ).fetchall()
            if len(existing) == 1 and existing[0][1] == hash_value:
                doc_id = int(existing[0][0])
                count = conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (doc_id,),
                ).fetchone()[0]
                conn.commit()
                return IngestOutcome(document_id=doc_id, chunk_count=count, was_new=False)

            conn.execute(
                "DELETE FROM documents WHERE collection_id = ? AND source_uri = ?",
                (collection_id, source_uri),
            )
            cursor = conn.execute(
                "INSERT INTO documents (collection_id, source_uri, title, mime, content_hash, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (collection_id, source_uri, title, "text/branch", hash_value, _now_iso()),
            )
            new_doc_id = cursor.lastrowid
            if new_doc_id is None:
                raise RuntimeError("SQLite did not return the inserted branch document id")
            conn.executemany(
                "INSERT INTO chunks (document_id, ordinal, text, token_count, offset_start, offset_end) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (new_doc_id, part.ordinal, part.text, part.token_count, part.offset_start, part.offset_end)
                    for part in chunks
                ],
            )
            conn.commit()
            return IngestOutcome(document_id=new_doc_id, chunk_count=len(chunks), was_new=True)
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()



class FileIngestOutcome(NamedTuple):
    document_id: int
    chunk_count: int
    action: str  # added | updated | duplicate | unchanged


def upsert_file_with_chunks(
    db_path: Path, *, source_uri: str, title: str, mime: str, text: str,
    chunks: list, collection_id: int = 0, size_bytes: int | None = None,
    connection: sqlite3.Connection | None = None,
) -> FileIngestOutcome:
    """Ingest a real file by path, atomically replacing its old content.

    Identical text is stored once per collection; file_sources records all
    paths sharing that document. On a changed path, drop only its old mapping,
    retaining the old document while any other file still uses it. Keep
    canonical citation paths up to date when the first alias moves away.
    BEGIN IMMEDIATE serializes competing jobs on the same source.
    """
    digest = content_hash(text)
    # Folder imports reuse one connection while retaining one atomic transaction per file.
    conn = connection if connection is not None else _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT d.id, d.content_hash, d.source_uri FROM file_sources f "
                "JOIN documents d ON d.id = f.document_id "
                "WHERE f.collection_id = ? AND f.source_uri = ?",
                (collection_id, source_uri),
            ).fetchone()
            if old is not None and old[1] == digest:
                if size_bytes is not None:
                    conn.execute("UPDATE documents SET size_bytes = ? WHERE id = ?", (size_bytes, old[0]))
                count = conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (old[0],),
                ).fetchone()[0]
                conn.commit()
                return FileIngestOutcome(int(old[0]), int(count), "unchanged")

            existing = get_document_by_hash(
                conn, content_hash_value=digest, collection_id=collection_id,
            )
            created = existing is None
            if created:
                cursor = conn.execute(
                    "INSERT INTO documents (collection_id, source_uri, title, mime, content_hash, added_at, size_bytes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (collection_id, source_uri, title, mime, digest, _now_iso(), size_bytes),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not return the ingested document id")
                existing = int(cursor.lastrowid)
                conn.executemany(
                    "INSERT INTO chunks (document_id, ordinal, text, token_count, offset_start, offset_end) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (existing, part.ordinal, part.text, part.token_count, part.offset_start, part.offset_end)
                        for part in chunks
                    ],
                )

            if not created and size_bytes is not None:
                conn.execute(
                    "UPDATE documents SET size_bytes = COALESCE(size_bytes, ?) WHERE id = ?",
                    (size_bytes, existing),
                )

            # A document indexed by the old ingest_file API predates the
            # mapping table. Adopt its source on identical content and clean
            # up its stale chunks when that path now carries different text.
            if old is None:
                legacy = conn.execute(
                    "SELECT id FROM documents WHERE collection_id = ? AND source_uri = ? AND id != ?",
                    (collection_id, source_uri, existing),
                ).fetchall()
                for (legacy_id,) in legacy:
                    references = conn.execute(
                        "SELECT COUNT(*) FROM file_sources WHERE document_id = ?", (legacy_id,),
                    ).fetchone()[0]
                    if not references:
                        conn.execute("DELETE FROM documents WHERE id = ?", (legacy_id,))

            if old is not None:
                old_id = int(old[0])
                conn.execute(
                    "DELETE FROM file_sources WHERE collection_id = ? AND source_uri = ?",
                    (collection_id, source_uri),
                )
                if old_id != existing:
                    survivor = conn.execute(
                        "SELECT source_uri FROM file_sources WHERE document_id = ? ORDER BY source_uri LIMIT 1",
                        (old_id,),
                    ).fetchone()
                    if survivor is None:
                        # Never delete a non-file document that happened to
                        # share content with a file (e.g. a retained URL).
                        if old[2] == source_uri:
                            conn.execute("DELETE FROM documents WHERE id = ?", (old_id,))
                    elif old[2] == source_uri:
                        alternate = str(survivor[0])
                        conn.execute(
                            "UPDATE documents SET source_uri = ?, title = ? WHERE id = ?",
                            (alternate, Path(alternate).name, old_id),
                        )

            conn.execute(
                "INSERT INTO file_sources (collection_id, source_uri, document_id) VALUES (?, ?, ?)",
                (collection_id, source_uri, existing),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (existing,),
            ).fetchone()[0]
            conn.commit()
            action = "updated" if old is not None else ("added" if created else "duplicate")
            return FileIngestOutcome(int(existing), int(count), action)
        except BaseException:
            conn.rollback()
            raise
    finally:
        if connection is None:
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


def get_or_create_workspace_collection(db_path: Path, workspace_id: int) -> int:
    """ADR-020 stage 20.3: the ONE collection-creation code path this stage
    builds - per the ADR's own scoping decision, every workspace gets
    EXACTLY one collection, created lazily on first real use, never
    surfaced as a separately manageable entity (no create_collection(name,
    ...) API for users - see this module's own module docstring update for
    the full reasoning). Idempotent by construction: a SELECT first, an
    INSERT only on a genuine miss, so every real call site (backend/api/
    intents_knowledge.py's search()/branch-indexing, graphlink_plugins/
    web_research/service.py's retention) can simply call this on EVERY
    real ingest/search for a workspace with no "did I already create this
    workspace's collection" bookkeeping of its own to carry - and it is
    self-healing: a workspace whose collection row is missing (never
    created, or knowledge.db was restored from a backup that predates it)
    just gets a fresh one on the very next call, rather than erroring.

    `name`/`scope` are cosmetic only - nothing in this codebase's own UI
    ever displays a collection by name (the ADR's own "invisible, not a
    general collection-management UI" scoping decision) - `name` is a
    stable, deterministic f"workspace-{workspace_id}" (useful only for a
    human inspecting the raw .db file directly), `scope` reuses the
    pre-20.3 column literally ("workspace") purely for that same manual-
    inspection value; neither is read back by any real code path in this
    codebase."""
    conn = _connect(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT id FROM collections WHERE workspace_id = ?", (workspace_id,)
            ).fetchone()
            if row is not None:
                return int(row[0])
            try:
                cursor = conn.execute(
                    "INSERT INTO collections (name, scope, created_at, workspace_id) VALUES (?, ?, ?, ?)",
                    (f"workspace-{workspace_id}", "workspace", _now_iso(), workspace_id),
                )
                # lastrowid is Optional only because it is None before the
                # first INSERT on a cursor; this reads it directly after one.
                return int(cursor.lastrowid)  # type: ignore[arg-type]
            except sqlite3.IntegrityError:
                # REVIEW-FIX: the SELECT above and this INSERT are not one
                # atomic step - a second, fully separate connection (every
                # real caller reaches this via asyncio.to_thread, genuine
                # OS threads) can run the identical SELECT-sees-nothing,
                # INSERT sequence concurrently. Before migration 6 added
                # idx_collections_workspace_id_unique, both INSERTs simply
                # succeeded, silently splitting this workspace's knowledge
                # base across two collections rows - proven with a forced
                # two-thread race. Now the LOSING connection's INSERT hits
                # that unique index and raises here instead: re-read the
                # row the WINNING connection just committed and return its
                # id, rather than erroring out or creating a duplicate.
                row = conn.execute(
                    "SELECT id FROM collections WHERE workspace_id = ?", (workspace_id,)
                ).fetchone()
                if row is None:
                    raise  # the index rejected this INSERT for some other reason - do not mask it
                return int(row[0])
    finally:
        conn.close()



def move_unscoped_documents_to_workspace(db_path: Path, workspace_id: int) -> int:
    """Adopt documents from the legacy collection 0 into the Default workspace.

    Run when a session initializes its Default workspace, before it can use
    the Knowledge Library. This is idempotent across sessions and atomic:
    a crash cannot leave half the documents or file aliases relocated.
    Moving a unique document in place preserves its chunk/citation IDs and
    cached embeddings. If the destination already contains the same hash,
    reuse that indexed copy and merge the old file aliases instead of
    violating UNIQUE(content_hash, collection_id). Existing destination
    aliases take priority if the same path was separately reindexed there.
    """
    collection_id = get_or_create_workspace_collection(db_path, workspace_id)
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM documents WHERE collection_id = 0 LIMIT 1").fetchone() is None:
            return 0
        # Take a recoverable snapshot before changing existing user data.
        maybe_backup_before_write(db_path, {"last_backup_at": None})
        # Serializes competing session startups and concurrent ingests.
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT id, content_hash, size_bytes FROM documents WHERE collection_id = 0 ORDER BY id"
            ).fetchall()
            for document_id, digest, size_bytes in rows:
                existing_id = get_document_by_hash(
                    conn, content_hash_value=digest, collection_id=collection_id,
                )
                destination_id = int(existing_id) if existing_id is not None else int(document_id)
                if existing_id is not None and size_bytes is not None:
                    conn.execute(
                        "UPDATE documents SET size_bytes = COALESCE(size_bytes, ?) WHERE id = ?",
                        (size_bytes, destination_id),
                    )
                # Map all original paths, including aliases, to the surviving
                # document. Keep a newer destination mapping on path conflicts.
                conn.execute(
                    "INSERT OR IGNORE INTO file_sources (collection_id, source_uri, document_id) "
                    "SELECT ?, source_uri, ? FROM file_sources "
                    "WHERE collection_id = 0 AND document_id = ?",
                    (collection_id, destination_id, document_id),
                )
                conn.execute(
                    "DELETE FROM file_sources WHERE collection_id = 0 AND document_id = ?",
                    (document_id,),
                )
                if existing_id is None:
                    conn.execute(
                        "UPDATE documents SET collection_id = ? WHERE id = ?",
                        (collection_id, document_id),
                    )
                else:
                    # The destination already has identical content. Its chunks
                    # and embeddings survive; deleting the duplicate cascades
                    # only the now-redundant chunks and their FTS entries.
                    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
            conn.commit()
            return len(rows)
        except BaseException:
            conn.rollback()
            raise
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


def list_library_documents(db_path: Path, *, collection_id: int) -> list[dict[str, Any]]:
    """Workspace-scoped rows for the Knowledge Library, with chunk counts and source availability.

    A document may have multiple file aliases, so count chunks independently
    instead of joining both tables (which would multiply chunk counts).
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT d.id, d.title, d.mime, d.source_uri, d.size_bytes, d.added_at,
                   (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id),
                   (SELECT COUNT(*) FROM file_sources f WHERE f.document_id = d.id)
            FROM documents d WHERE d.collection_id = ?
            ORDER BY d.added_at DESC, d.id DESC
            """, (collection_id,),
        ).fetchall()
        return [
            {
                "documentId": int(row[0]), "title": row[1], "type": row[2] or "text/plain",
                "sourceUri": row[3], "sizeBytes": row[4], "chunks": int(row[6]),
                "addedAt": row[5], "status": "indexed", "canReindex": bool(row[7]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def file_sources_for_document(db_path: Path, *, collection_id: int, document_id: int) -> list[str]:
    """Resolve only sources that already belong to a document in this workspace."""
    conn = _connect(db_path)
    try:
        return [str(row[0]) for row in conn.execute(
            "SELECT f.source_uri FROM file_sources f "
            "JOIN documents d ON d.id = f.document_id "
            "WHERE d.id = ? AND d.collection_id = ? AND f.collection_id = ? "
            "ORDER BY f.source_uri", (document_id, collection_id, collection_id),
        ).fetchall()]
    finally:
        conn.close()


def remove_library_document(db_path: Path, *, collection_id: int, document_id: int) -> bool:
    """Remove only the selected workspace document, cascading its aliases and chunks."""
    conn = _connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM documents WHERE id = ? AND collection_id = ?", (document_id, collection_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def library_document_text(db_path: Path, *, collection_id: int, document_id: int) -> dict | None:
    """Return bounded text for the existing docked Document View.

    Original source bodies are not retained by the knowledge DB. Assemble
    offset-exact chunks without repeating their overlaps; paragraph
    separators lost during chunking are represented by blank lines.
    """
    conn = _connect(db_path)
    try:
        doc = conn.execute(
            "SELECT title, source_uri FROM documents WHERE id = ? AND collection_id = ?",
            (document_id, collection_id),
        ).fetchone()
        if doc is None:
            return None
        rows = conn.execute(
            "SELECT text, offset_start, offset_end FROM chunks "
            "WHERE document_id = ? ORDER BY ordinal", (document_id,),
        ).fetchall()
        pieces: list[str] = []
        end = 0
        length = 0
        limit = 2_000_000
        for value, start, stop in rows:
            if str(doc[1]).startswith("graph:") or start < 0 or stop < start:
                part = ("\n\n" if pieces else "") + value
            elif start > end:
                part = ("\n\n" if pieces else "") + value
            else:
                part = value[max(end - start, 0):]
            if not part:
                continue
            remaining = limit - length
            if remaining <= 0:
                break
            pieces.append(part[:remaining])
            length += min(len(part), remaining)
            end = max(end, stop)
        content = "".join(pieces)
        if length >= limit:
            content += "\n\n[Document View truncated to 2,000,000 characters]"
        return {"title": doc[0], "content": content}
    finally:
        conn.close()


# -- opt-in workspace folder subscriptions ----------------------------------

def _watched_folder_row(row: tuple) -> dict[str, Any]:
    return {
        "id": int(row[0]), "workspaceId": int(row[1]), "path": str(row[2]),
        "lastSyncAt": row[3], "lastError": str(row[4]),
    }


def list_watched_folders(db_path: Path, workspace_id: int | None = None) -> list[dict[str, Any]]:
    """Return only explicitly configured subscriptions; never discover folders."""
    conn = _connect(db_path)
    try:
        sql = "SELECT id, workspace_id, path, last_sync_at, last_error FROM watched_folders"
        if workspace_id is None:
            rows = conn.execute(sql + " ORDER BY id").fetchall()
        else:
            rows = conn.execute(sql + " WHERE workspace_id = ? ORDER BY id", (workspace_id,)).fetchall()
        return [_watched_folder_row(row) for row in rows]
    finally:
        conn.close()


def add_watched_folder(db_path: Path, workspace_id: int, path: str) -> dict[str, Any]:
    """Native-picker paths are canonicalized; adding the same watch is idempotent."""
    if workspace_id <= 0:
        raise ValueError("Select a workspace before watching a folder.")
    selected = Path(path).expanduser().absolute()
    resolved = selected.resolve(strict=True)
    if os.path.normcase(str(selected)) != os.path.normcase(str(resolved)):
        raise ValueError("Watched folder was redirected; reselect it.")
    if not resolved.is_dir():
        raise ValueError("The selected path is not a directory.")
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO watched_folders (workspace_id, path) VALUES (?, ?) "
                "ON CONFLICT (workspace_id, path) DO NOTHING",
                (workspace_id, str(resolved)),
            )
            row = conn.execute(
                "SELECT id, workspace_id, path, last_sync_at, last_error FROM watched_folders "
                "WHERE workspace_id = ? AND path = ?",
                (workspace_id, str(resolved)),
            ).fetchone()
            if row is None:
                raise RuntimeError("Failed to read back the watched folder.")
            return _watched_folder_row(row)
    finally:
        conn.close()


def remove_watched_folder(db_path: Path, workspace_id: int, folder_id: int) -> bool:
    """Stop watching without deleting previously imported knowledge."""
    conn = _connect(db_path)
    try:
        with conn:
            result = conn.execute(
                "DELETE FROM watched_folders WHERE id = ? AND workspace_id = ?",
                (folder_id, workspace_id),
            )
            return result.rowcount > 0
    finally:
        conn.close()


def known_watched_files(db_path: Path, folder_id: int, collection_id: int) -> dict[str, tuple[int, int]]:
    """Only trust cached stamps for paths still indexed in this collection."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT w.source_uri, w.size_bytes, w.mtime_ns "
            "FROM watched_folder_files w JOIN file_sources f "
            "ON f.source_uri = w.source_uri AND f.collection_id = ? "
            "WHERE w.folder_id = ?",
            (collection_id, folder_id),
        ).fetchall()
        return {str(uri): (int(size), int(mtime)) for uri, size, mtime in rows}
    finally:
        conn.close()


def track_watched_file(
    db_path: Path, folder_id: int, source_uri: str, *, size_bytes: int, mtime_ns: int,
) -> None:
    """Record a successfully ingested source; a removed watch cannot be revived."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT INTO watched_folder_files (folder_id, source_uri, size_bytes, mtime_ns) "
                "SELECT id, ?, ?, ? FROM watched_folders WHERE id = ? "
                "ON CONFLICT (folder_id, source_uri) DO UPDATE SET "
                "size_bytes = excluded.size_bytes, mtime_ns = excluded.mtime_ns",
                (source_uri, size_bytes, mtime_ns, folder_id),
            )
    finally:
        conn.close()


def _unlink_file_source(conn: sqlite3.Connection, collection_id: int, source_uri: str) -> None:
    """Unlink one disappeared path while retaining shared-content documents."""
    row = conn.execute(
        "SELECT d.id, d.source_uri FROM file_sources f "
        "JOIN documents d ON d.id = f.document_id "
        "WHERE f.collection_id = ? AND f.source_uri = ?",
        (collection_id, source_uri),
    ).fetchone()
    if row is None:
        return
    document_id, canonical_uri = int(row[0]), str(row[1])
    conn.execute(
        "DELETE FROM file_sources WHERE collection_id = ? AND source_uri = ?",
        (collection_id, source_uri),
    )
    if canonical_uri != source_uri:
        return  # a retained web/branch/other document owns this content
    other = conn.execute(
        "SELECT source_uri FROM file_sources WHERE document_id = ? ORDER BY source_uri LIMIT 1",
        (document_id,),
    ).fetchone()
    if other is None:
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    else:
        replacement = str(other[0])
        conn.execute(
            "UPDATE documents SET source_uri = ?, title = ? WHERE id = ?",
            (replacement, Path(replacement).name, document_id),
        )


def reconcile_watched_files(
    db_path: Path, folder_id: int, collection_id: int, present: set[str],
    *, prune: bool,
) -> int:
    """Unindex vanished *tracked* files only after a complete, safe scan.

    A missing/unreadable folder, a cancelled scan or an excluded/oversized
    entry must never masquerade as an empty folder. Concurrent removals of a
    subscription are harmless. Overlapping watches keep shared file sources.
    """
    if not prune:
        return 0
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            folder = conn.execute(
                "SELECT workspace_id FROM watched_folders WHERE id = ?", (folder_id,),
            ).fetchone()
            if folder is None:
                conn.commit()
                return 0
            workspace_id = int(folder[0])
            tracked = conn.execute(
                "SELECT source_uri FROM watched_folder_files WHERE folder_id = ?", (folder_id,),
            ).fetchall()
            deleted = 0
            for (source_uri,) in tracked:
                if source_uri in present:
                    continue
                conn.execute(
                    "DELETE FROM watched_folder_files WHERE folder_id = ? AND source_uri = ?",
                    (folder_id, source_uri),
                )
                other = conn.execute(
                    "SELECT 1 FROM watched_folder_files wf "
                    "JOIN watched_folders w ON w.id = wf.folder_id "
                    "WHERE w.workspace_id = ? AND wf.source_uri = ? LIMIT 1",
                    (workspace_id, source_uri),
                ).fetchone()
                if other is None:
                    _unlink_file_source(conn, collection_id, str(source_uri))
                deleted += 1
            conn.commit()
            return deleted
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()


def set_watched_folder_sync_result(
    db_path: Path, folder_id: int, *, completed: bool, error: str,
) -> None:
    """Record the latest completed scan time, leaving it unchanged on failure."""
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE watched_folders SET "
                "last_sync_at = CASE WHEN ? THEN ? ELSE last_sync_at END, last_error = ? "
                "WHERE id = ?",
                (int(completed), _now_iso(), error[:2048], folder_id),
            )
    finally:
        conn.close()


# -- graph node indexing (ADR-020 stage 20.4) --------------------------------
#
# One `documents` row per GRAPH (source_uri = f"graph:{graph_id}", a
# synthetic scheme that can never collide with a real file/branch/web-
# research source_uri - the CALLING code is what feeds `node_chunks`,
# already normalized per-node text - see backend/chat_library.py's own
# node-content normalization), one `chunks` row per NODE inside it. Unlike
# add_document_with_chunks's content-hash idempotency (built for immutable-
# once-ingested files), a graph is mutated and re-saved constantly - see
# reindex_graph_content's own docstring for why this is delete-then-
# reinsert on every call instead.


def _graph_source_uri(graph_id: int) -> str:
    return f"graph:{graph_id}"


def reindex_graph_content(
    db_path: Path,
    *,
    graph_id: int,
    workspace_id: int,
    title: str,
    node_chunks: list[tuple[str, str]],
    notifications: NotificationState | None = None,
    last_saved: dict[str, Any] | None = None,
) -> None:
    """The graph re-indexing strategy this stage's own design settled on:
    graphs are MUTABLE (unlike a real ingested file - this module's own
    content-hash-idempotency docstring explicitly assumes immutable-once-
    ingested content), so a content-hash idempotent-insert would never hit
    (a graph's own content_hash changes on every save) and would just
    accumulate one stale document+chunks row per save, forever. Instead:
    DELETE every existing documents+chunks row for this graph (looked up by
    `source_uri = f"graph:{graph_id}"`, never by content_hash), then INSERT
    a fresh documents row + fresh chunks rows reflecting the graph's CURRENT
    content - called on EVERY save, insert or resave alike (see backend/
    chat_library.py's save_chat_atomically_row, this function's one real
    caller). `chunks_fts`'s own DELETE trigger (migration "2") keeps the
    FTS index in sync automatically when the old chunks rows are deleted
    (ON DELETE CASCADE fires the child table's own triggers - see that
    migration's own docstring, already verified empirically there); this
    function never touches chunks_fts directly.

    `node_chunks` is `[(node_id, text), ...]`, already normalized and
    already filtered to non-empty text by the caller - this function trusts
    what it is given, matching this module's own "the caller resolves
    content, this function only writes" division of labor (see e.g. how
    add_document_with_chunks already trusts its own `chunks` param). An
    empty list (a graph with no indexable node content at all right now)
    still runs the DELETE below - clearing any STALE prior index for this
    same graph_id, which matters when a graph's last node with real content
    was just removed or emptied - but skips the INSERT, since there is
    nothing left to write a document row for.

    `workspace_id` resolves this graph's real knowledge collection via
    get_or_create_workspace_collection - called BEFORE this function's own
    write transaction opens (not nested inside it), specifically so it
    never has to wait out its own separate connection's write lock against
    the one this function is about to open on the SAME db file (a nested
    call here would self-contend under WAL's own single-writer rule until
    busy_timeout expired).

    `content_hash` is still populated (the schema's own NOT NULL) even
    though this path deliberately never CONSULTS it for idempotency (unlike
    add_document_with_chunks) - computed from `source_uri` PLUS the graph's
    own concatenated node text, not the text alone: `documents` declares
    `UNIQUE(content_hash, collection_id)`, and two DIFFERENT graphs in the
    SAME workspace/collection with byte-identical node content (a real,
    reachable case - e.g. two graphs both created from a duplicated
    starting template) are deliberately NOT the same document the way two
    real files with identical extracted text are (add_document_with_chunks'
    own content-hash-idempotency docstring) - each graph is its own
    document, tracked by its own unique source_uri, regardless of what its
    nodes happen to say. Folding source_uri into the hash input keeps this
    per-graph-unique by construction (empirically verified - see this
    function's own test_two_different_graphs_do_not_collide test) rather
    than colliding on the UNIQUE constraint the moment two graphs' content
    happens to match.

    REVIEW-FIX: the SELECT below and the DELETE/INSERT that follow it are
    not one atomic step under a plain `with conn:` - Python's sqlite3
    module only implicitly opens a transaction before INSERT/UPDATE/DELETE,
    never before a bare SELECT, so the SELECT ran unlocked before this fix.
    Two concurrent reindex_graph_content calls for the SAME graph_id
    (reachable with no lock serializing them - see this function's own
    calling-path docstring above) could both read the same pre-race
    existing_ids and both INSERT a fresh documents row, leaving two rows
    for one graph with no error raised - proven with a forced two-thread
    race. Worse, a reindex racing a concurrent delete_graph_index call for
    the same graph could read existing_ids BEFORE the delete's own DELETE
    committed, then run its own (by-then-stale, no-op) DELETE and INSERT
    AFTER the delete committed - silently resurrecting a just-deleted
    graph's index entry that no future reindex will ever clean up, since
    the graph no longer exists to trigger one.

    `BEGIN IMMEDIATE` below acquires the write lock BEFORE the SELECT runs
    (instead of leaving it deferred until the first DML), so the entire
    SELECT-then-DELETE-then-INSERT sequence serializes against ANY other
    writer on this db file - including delete_graph_index's own single
    DELETE statement, which already atomically holds the write lock for
    its own duration (no preceding SELECT of its own) and needs no change
    of its own to be safe against this. Either the delete fully lands
    first (this call's own SELECT then correctly sees no existing row and
    the graph reappears with fresh content, no different from a normal
    reindex-after-open-elsewhere race, not this bug) or this call fully
    lands first (the later delete then correctly removes what was just
    inserted) - the two can no longer straddle each other."""
    collection_id = get_or_create_workspace_collection(db_path, workspace_id)
    source_uri = _graph_source_uri(graph_id)
    estimator = TokenEstimator()

    conn = _connect(db_path, notifications=notifications)
    try:
        if last_saved is not None:
            maybe_backup_before_write(db_path, last_saved)

        conn.execute("BEGIN IMMEDIATE")
        try:
            existing_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM documents WHERE source_uri = ?", (source_uri,)
                ).fetchall()
            ]
            for existing_id in existing_ids:
                # ON DELETE CASCADE removes this document's own chunks rows,
                # which fires chunks_fts' own AFTER DELETE trigger for each
                # one - chunks_fts needs no direct touch here.
                conn.execute("DELETE FROM documents WHERE id = ?", (existing_id,))

            if not node_chunks:
                conn.commit()
                return

            full_text = "\n\n".join(text for _, text in node_chunks)
            cursor = conn.execute(
                "INSERT INTO documents (collection_id, source_uri, title, mime, content_hash, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    collection_id, source_uri, title, "text/graph",
                    content_hash(f"{source_uri}\n{full_text}"), _now_iso(),
                ),
            )
            document_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO chunks "
                "(document_id, ordinal, text, token_count, offset_start, offset_end, source_node_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (document_id, ordinal, text, estimator.count_tokens(text), 0, len(text), node_id)
                    for ordinal, (node_id, text) in enumerate(node_chunks)
                ],
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()


def delete_graph_index(db_path: Path, graph_id: int) -> bool:
    """The deletion-side mirror of reindex_graph_content: removes this
    graph's own documents+chunks rows (ON DELETE CASCADE + chunks_fts' own
    DELETE trigger keep chunks/chunks_fts in sync, same as reindex_graph_
    content's own DELETE above) - called from backend/chat_library.py's
    delete_chat, so a deleted graph's content stops being a real, jump-to-
    node-able global-search hit rather than lingering as an orphaned index
    entry for a graph that no longer exists. Returns whether a row was
    actually deleted - False for a graph with nothing indexed (never saved
    with any indexable node content, or already deleted) is a normal
    outcome, not an error - mirrors delete_document's own return contract.

    Needs no locking of its own against reindex_graph_content's race fix
    (see that function's own REVIEW-FIX docstring): this DELETE is the
    first and only statement in its transaction, so it already atomically
    acquires the write lock the moment it runs - there is no preceding
    SELECT here for a concurrent writer to interleave around. It was
    reindex_graph_content's own SELECT-before-DELETE/INSERT that could run
    unlocked ahead of this DELETE and later resurrect what this call just
    removed; reindex_graph_content's own BEGIN IMMEDIATE closes that
    window from its side, which is sufficient since SQLite allows only one
    writer at a time on this db file."""
    conn = _connect(db_path)
    try:
        with conn:
            cursor = conn.execute(
                "DELETE FROM documents WHERE source_uri = ?", (_graph_source_uri(graph_id),)
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


# -- embedding cache/index CRUD (ADR-017 stage 17.3) -------------------------
#
# Deliberately byte-blob level - no numpy import in this module. Packing a
# vector into bytes and back is backend.knowledge_embeddings' job (the
# module that also owns the actual Provider.embed() calls and the
# similarity math); this module stays what every other store.py in this
# codebase is, plain CRUD with no ML-library dependency.


def upsert_embeddings(db_path: Path, model_id: str, rows: list[tuple[int, int, bytes]]) -> None:
    """`rows`: `(chunk_id, dim, vector_blob)` tuples, all for the SAME
    `model_id` (the cache key's other half). ON CONFLICT DO UPDATE rather
    than INSERT OR IGNORE - defensive, not load-bearing today: every real
    caller (knowledge_embeddings.embed_pending_chunks) only ever passes
    chunk_ids that chunks_pending_embedding() just confirmed have no row
    yet, so this is a plain insert in practice, but a caller that DOES
    pass an already-embedded chunk_id (e.g. a forced re-embed) gets a
    correct overwrite instead of a silently-ignored no-op or a UNIQUE-
    constraint crash."""
    if not rows:
        return
    conn = _connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO embeddings (chunk_id, model_id, dim, vector) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chunk_id, model_id) DO UPDATE SET dim = excluded.dim, vector = excluded.vector",
                [(chunk_id, model_id, dim, vector_blob) for chunk_id, dim, vector_blob in rows],
            )
    finally:
        conn.close()


def cold_collection_embedding_batch(
    db_path: Path, model_id: str, *, collection_id: int, max_chunks: int,
) -> list[dict[str, Any]]:
    """Return one small collection's chunks only if its model cache is empty.

    A collection larger than max_chunks, or one with any committed vectors,
    must be indexed by the normal background job rather than by search.
    The bound is applied in SQLite so a cold search never loads the corpus.
    """
    if max_chunks < 1:
        raise ValueError("max_chunks must be positive.")
    conn = _connect(db_path)
    try:
        cached = conn.execute(
            "SELECT 1 FROM embeddings e "
            "JOIN chunks c ON c.id = e.chunk_id "
            "JOIN documents d ON d.id = c.document_id "
            "WHERE e.model_id = ? AND d.collection_id = ? LIMIT 1",
            (model_id, collection_id),
        ).fetchone()
        if cached is not None:
            return []
        rows = conn.execute(
            "SELECT c.id, c.text FROM chunks c "
            "JOIN documents d ON d.id = c.document_id "
            "WHERE d.collection_id = ? ORDER BY c.id LIMIT ?",
            (collection_id, max_chunks + 1),
        ).fetchall()
        if len(rows) > max_chunks:
            return []
        return [{"chunk_id": row[0], "text": row[1]} for row in rows]
    finally:
        conn.close()


def chunks_pending_embedding(
    db_path: Path, model_id: str, *, collection_id: int | None = None,
) -> list[dict[str, Any]]:
    """Every chunk with no `(chunk_id, model_id)` row yet - the embedding
    CACHE's read side (ADR-017 stage 17.3's own exit criterion: "cache
    prevents re-embedding"). A LEFT JOIN ... WHERE e.chunk_id IS NULL
    anti-join, not a NOT IN subquery - the standard SQLite idiom for "rows
    in A with no matching row in B", and avoids a NOT IN's own NULL
    pitfall entirely (moot here since chunk_id is never NULL, but the
    anti-join form is the one with no such trap to reason about)."""
    conn = _connect(db_path)
    try:
        query = (
            "SELECT c.id, c.text FROM chunks c "
            "JOIN documents d ON d.id = c.document_id "
            "LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model_id = ? "
            "WHERE e.chunk_id IS NULL"
        )
        params: list[Any] = [model_id]
        if collection_id is not None:
            query += " AND d.collection_id = ?"
            params.append(collection_id)
        rows = conn.execute(query, params).fetchall()
        return [{"chunk_id": row[0], "text": row[1]} for row in rows]
    finally:
        conn.close()


def pending_embedding_snapshot(
    db_path: Path, model_id: str, *, collection_id: int | None = None,
) -> tuple[int, int]:
    """Count outstanding vectors and pin their highest chunk ID without loading text.

    A background job must not absorb documents imported after it starts: the
    high-water mark keeps its progress total and cancellation checkpoints
    meaningful while later imports can schedule their own embedding pass.
    """
    conn = _connect(db_path)
    try:
        query = (
            "SELECT COUNT(*), COALESCE(MAX(c.id), 0) FROM chunks c "
            "JOIN documents d ON d.id = c.document_id "
            "LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model_id = ? "
            "WHERE e.chunk_id IS NULL"
        )
        params: list[Any] = [model_id]
        if collection_id is not None:
            query += " AND d.collection_id = ?"
            params.append(collection_id)
        count, high_water = conn.execute(query, params).fetchone()
        return int(count), int(high_water)
    finally:
        conn.close()


def pending_embedding_batch(
    db_path: Path, model_id: str, *, collection_id: int | None = None,
    after_chunk_id: int = 0, max_chunk_id: int | None = None, limit: int = 32,
) -> list[dict[str, Any]]:
    """Read only the next pending batch using the chunk primary-key cursor."""
    if limit < 1:
        raise ValueError("limit must be positive.")
    conn = _connect(db_path)
    try:
        query = (
            "SELECT c.id, c.text FROM chunks c "
            "JOIN documents d ON d.id = c.document_id "
            "LEFT JOIN embeddings e ON e.chunk_id = c.id AND e.model_id = ? "
            "WHERE e.chunk_id IS NULL AND c.id > ?"
        )
        params: list[Any] = [model_id, after_chunk_id]
        if max_chunk_id is not None:
            query += " AND c.id <= ?"
            params.append(max_chunk_id)
        if collection_id is not None:
            query += " AND d.collection_id = ?"
            params.append(collection_id)
        rows = conn.execute(query + " ORDER BY c.id LIMIT ?", (*params, limit)).fetchall()
        return [{"chunk_id": row[0], "text": row[1]} for row in rows]
    finally:
        conn.close()


def has_embeddings_for_search(
    db_path: Path, model_id: str, *, collection_id: int | None = None,
) -> bool:
    """Avoid a provider request when the selected model/workspace has no vectors."""
    conn = _connect(db_path)
    try:
        query = (
            "SELECT 1 FROM embeddings e "
            "JOIN chunks c ON c.id = e.chunk_id "
            "JOIN documents d ON d.id = c.document_id "
            "WHERE e.model_id = ?"
        )
        params: list[Any] = [model_id]
        if collection_id is not None:
            query += " AND d.collection_id = ?"
            params.append(collection_id)
        return conn.execute(query + " LIMIT 1", params).fetchone() is not None
    finally:
        conn.close()


def iter_embedding_vector_batches(
    db_path: Path, model_id: str, *, collection_id: int | None = None,
    batch_size: int = 256,
):
    """Stream compact (id, dimension, vector) records, not the corpus's text.

    Keeping one SQLite read cursor and fetching fixed-size batches avoids
    allocating 200,000 BLOBs and passages before a single cosine score exists.
    Metadata is fetched only for the final top-k results.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive.")
    conn = _connect(db_path)
    try:
        query = (
            "SELECT e.chunk_id, e.dim, e.vector FROM embeddings e "
            "JOIN chunks c ON c.id = e.chunk_id "
            "JOIN documents d ON d.id = c.document_id "
            "WHERE e.model_id = ?"
        )
        params: list[Any] = [model_id]
        if collection_id is not None:
            query += " AND d.collection_id = ?"
            params.append(collection_id)
        cursor = conn.execute(query, params)
        while batch := cursor.fetchmany(batch_size):
            yield batch
    finally:
        conn.close()


def embedding_hit_metadata(
    db_path: Path, chunk_ids: list[int], *, collection_id: int | None = None,
) -> dict[int, dict[str, Any]]:
    """Retrieve passage text and citation fields for top-k IDs only."""
    if not chunk_ids:
        return {}
    conn = _connect(db_path)
    try:
        placeholders = ",".join("?" for _ in chunk_ids)
        query = (
            "SELECT c.id, c.document_id, c.ordinal, c.text, c.token_count, "
            "c.offset_start, c.offset_end, d.title, d.source_uri, c.source_node_id "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            f"WHERE c.id IN ({placeholders})"
        )
        params: list[Any] = list(chunk_ids)
        if collection_id is not None:
            query += " AND d.collection_id = ?"
            params.append(collection_id)
        rows = conn.execute(query, params).fetchall()
        return {
            int(row[0]): {
                "chunk_id": row[0], "document_id": row[1], "ordinal": row[2],
                "text": row[3], "token_count": row[4], "offset_start": row[5],
                "offset_end": row[6], "document_title": row[7], "source_uri": row[8],
                "source_node_id": row[9],
            }
            for row in rows
        }
    finally:
        conn.close()


def list_embeddings_for_search(
    db_path: Path, model_id: str, *, collection_id: int | None = None,
) -> list[dict[str, Any]]:
    """Every embedded chunk for `model_id`, joined with enough of its
    parent chunk/document to build a citation-ready vector_search() result
    directly - the same fields search_chunks() (FTS5) already returns,
    matching shapes so stage 17.4's fusion can treat both result lists
    uniformly. `vector` is the raw packed BLOB - unpacking is
    knowledge_embeddings.vector_search's job, not this module's.

    ADR-020 stage 20.4: also carries `source_node_id` (migration "5") - a
    fused hybrid_search() result found ONLY via this vector path still
    needs to be traceable back to its origin node the same way a
    search_chunks() (FTS5) hit already is, so a graph-derived chunk that
    happens to have been embedded is never mistaken for a real ingested-
    document hit downstream."""
    conn = _connect(db_path)
    try:
        query = (
            "SELECT e.chunk_id, e.dim, e.vector, c.document_id, c.ordinal, c.text, c.token_count, "
            "c.offset_start, c.offset_end, d.title, d.source_uri, c.source_node_id "
            "FROM embeddings e "
            "JOIN chunks c ON c.id = e.chunk_id "
            "JOIN documents d ON d.id = c.document_id "
            "WHERE e.model_id = ?"
        )
        params: list[Any] = [model_id]
        if collection_id is not None:
            query += " AND d.collection_id = ?"
            params.append(collection_id)
        rows = conn.execute(query, params).fetchall()
        return [
            {
                "chunk_id": row[0], "dim": row[1], "vector": row[2], "document_id": row[3],
                "ordinal": row[4], "text": row[5], "token_count": row[6], "offset_start": row[7],
                "offset_end": row[8], "document_title": row[9], "source_uri": row[10],
                "source_node_id": row[11],
            }
            for row in rows
        ]
    finally:
        conn.close()


# -- FTS5 lexical search (ADR-017 stage 17.2) --------------------------------


# SECURITY-FIX: FTS5 MATCH cost over the implicit-AND phrase list grows
# quadratically with the number of quoted terms - measured 93s of CPU on
# one worker thread (holding a knowledge.db connection the whole time) for
# a single 200k-term/1.5MB query, and the query text is never length-
# bounded anywhere on any of its three entry paths (the globalSearch/search
# and knowledge/search WS intents, and the auto-approved LLM tool
# knowledge.search - none of them cap `query`, only `k`). Capping the TERM
# COUNT here, in the one function every caller funnels through, bounds the
# actual cost driver regardless of how a caller's text is separated, well
# below where the measured blowup even starts (50k terms was already 2.4s).
_MAX_FTS5_QUERY_TERMS = 256


def _fts5_match_expression(query: str) -> str:
    """Turns free-form user text into a safe FTS5 MATCH expression: every
    \\w+ token double-quoted (an FTS5 string literal, immune to the
    query-syntax operators - `AND`/`OR`/`NOT`/`NEAR`/`-`/`*`/`^` - raw user
    text might otherwise contain and fail to parse or silently change
    meaning), joined with a space, which FTS5 treats as an implicit AND.
    Returns "" for a query with no word characters at all (blank, or pure
    punctuation) - callers treat that as "no results", not a query to run.
    Silently truncates past _MAX_FTS5_QUERY_TERMS - matching every other
    query-shaping choice in this function, a caller with an implausibly
    long query gets a best-effort search over its first N terms rather than
    an error or an unbounded CPU burn."""
    terms = re.findall(r"\w+", query, flags=re.UNICODE)[:_MAX_FTS5_QUERY_TERMS]
    return " ".join('"' + term.replace('"', '""') + '"' for term in terms)


def search_chunks(
    db_path: Path, query: str, *, collection_id: int | None = None, k: int = 10,
) -> list[dict[str, Any]]:
    """Lexical (BM25) search over every ingested chunk's text, ranked best
    match first. Returns a list of dicts carrying enough to both display a
    result and cite it exactly: `chunk_id`, `document_id`, `document_title`,
    `source_uri`, `ordinal`, `text`, `token_count`, `offset_start`,
    `offset_end`, `score` (raw bm25() value - more negative is a better
    match, per FTS5's own convention; ordering, not the magnitude, is the
    contract callers should rely on). `token_count` is the chunk's own
    already-computed count (backend.knowledge_chunking's TextChunk, stored
    at ingest time) - included so a caller doing budget-aware selection
    (ADR-017 stage 17.4's own backend.knowledge_retrieval.select_within_
    budget) never needs a second round-trip just to learn each result's
    size. Returns `[]` for a query with no indexable terms rather than
    matching everything (an empty FTS5 MATCH string is itself invalid
    syntax, and "no terms" has no reasonable non-empty answer).

    ADR-020 stage 20.4: also carries `source_node_id` (migration "5",
    NULL for every real ingested-document chunk, a real node id for a
    chunk backend.chat_library.reindex_graph_content wrote) - this is what
    lets a caller (backend/api/intents_global_search.py's own result
    formatter) tell a "graph/node" hit apart from a real document hit
    without a second query: `document_id` -> `documents.source_uri` is
    already joined into every row here, and a graph-sourced document's own
    `source_uri` is always the synthetic `f"graph:{graph_id}"` scheme
    reindex_graph_content writes.

    `k` bounds the result count outright, not a suggestion - a caller doing
    budget-aware selection still needs a hard upper bound on rows actually
    pulled from SQLite before it starts trimming by token budget."""
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k!r}.")
    match_expression = _fts5_match_expression(query)
    if not match_expression:
        return []

    conn = _connect(db_path)
    try:
        params: tuple[Any, ...] = (match_expression,)
        collection_filter = ""
        if collection_id is not None:
            collection_filter = "AND d.collection_id = ?"
            params = (match_expression, collection_id)
        rows = conn.execute(
            f"""
            SELECT c.id, c.document_id, c.ordinal, c.text, c.token_count, c.offset_start, c.offset_end,
                   d.title, d.source_uri, bm25(chunks_fts) AS score, c.source_node_id
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            JOIN documents d ON d.id = c.document_id
            WHERE chunks_fts MATCH ? {collection_filter}
            ORDER BY score
            LIMIT ?
            """,
            (*params, k),
        ).fetchall()
        return [
            {
                "chunk_id": row[0], "document_id": row[1], "ordinal": row[2], "text": row[3],
                "token_count": row[4], "offset_start": row[5], "offset_end": row[6],
                "document_title": row[7], "source_uri": row[8], "score": row[9],
                "source_node_id": row[10],
            }
            for row in rows
        ]
    finally:
        conn.close()

# -- stable retrieval provenance (no migration, works for existing data) ------

# A heading is attributed to chunks starting at or below its source offset.
# Both ATX and setext Markdown styles are supported; plain text has no
# invented heading, so the honest fallback is "Unsectioned".
_ATX_SECTION_RE = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$")
_SETEXT_SECTION_RE = re.compile(r"(?m)^([^\n]+)\n[ \t]*[=-]{3,}[ \t]*$")


def _headings_in_chunk(text: str, offset_start: int) -> list[tuple[int, str]]:
    headings = []
    for match in _ATX_SECTION_RE.finditer(text):
        title = match.group(1).strip()
        if title:
            headings.append((offset_start + match.start(), title))
    for match in _SETEXT_SECTION_RE.finditer(text):
        title = match.group(1).strip()
        if title:
            headings.append((offset_start + match.start(), title))
    return headings


def _stable_chunk_citation(result: dict) -> str:
    """Identifier derived from source and exact excerpt, not SQLite row IDs.

    Delete/reingest and stable graph reindexing can allocate new chunk IDs;
    an unchanged excerpt should still retain its citation identity.
    """
    parts = (
        str(result["source_uri"]), str(result.get("source_node_id") or ""),
        str(result["offset_start"]), str(result["offset_end"]),
        str(result["text"]),
    )
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"kb:{digest}"


def provenance_for_hits(db_path: Path, results: list[dict]) -> dict[int, dict[str, str]]:
    """Return source file, nearest preceding section, and stable citation.

    Uses existing chunk offsets/Markdown headings; no database migration or
    cloud service is needed. A single SQLite read obtains headings for every
    document represented in the hit set, including older ingested files.
    Graph nodes share a document but each has offsets relative to its own
    node text; their headings are scoped by source_node_id so one node's
    heading cannot label another node's search result.
    Where a chunk begins at a heading, that heading takes precedence. When
    there are no headings, report `Unsectioned` rather than guessing.
    """
    if not results:
        return {}
    document_ids = sorted({int(result["document_id"]) for result in results})
    placeholders = ",".join("?" for _ in document_ids)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT document_id, offset_start, text, source_node_id FROM chunks "
            f"WHERE document_id IN ({placeholders}) "
            "ORDER BY document_id, source_node_id, offset_start, ordinal",
            document_ids,
        ).fetchall()
    finally:
        conn.close()

    headings: dict[tuple[int, str | None], list[tuple[int, str]]] = {}
    for document_id, start, text, source_node_id in rows:
        key = (int(document_id), source_node_id)
        headings.setdefault(key, []).extend(_headings_in_chunk(text, int(start)))
    for key, items in headings.items():
        # Overlap between adjacent chunks may repeat one heading; the offset
        # is absolute, so collapsing it is safe and order-independent.
        headings[key] = sorted(set(items))

    metadata: dict[int, dict[str, str]] = {}
    for result in results:
        start = int(result["offset_start"])
        end = int(result["offset_end"])
        options = headings.get((int(result["document_id"]), result.get("source_node_id")), [])
        before = [item for item in options if item[0] <= start]
        if before:
            section = before[-1][1]
        else:
            # Only a heading at the very start of the hit (up to leading
            # whitespace) can label this chunk; later headings belong to a
            # subsequent section and must not retroactively label it.
            lead = len(result["text"]) - len(result["text"].lstrip())
            first = next((name for pos, name in options if start <= pos <= min(end, start + lead)), None)
            section = first or "Unsectioned"
        metadata[result["chunk_id"]] = {
            "source_file": str(result["source_uri"]),
            "section": section,
            "citation_id": _stable_chunk_citation(result),
        }
    return metadata


def open_cited_document(
    db_path: Path, *, document_id: int, chunk_id: int,
    citation_id: str, collection_id: int,
) -> dict[str, Any] | None:
    """Read an indexed source and its cited span, strictly scoped to a workspace.

    The caller-supplied IDs are only selectors: recompute the stable citation
    from the stored chunk before returning any text. The knowledge index stores
    offset-exact overlapping chunks, not the original file bytes; reconstruct
    the indexed text, filling paragraph separators with newlines so original
    character offsets still identify the passage. Graph-indexed chunks have
    per-node offsets, so reconstruct only the cited source node's text.
    """
    if not db_path.exists() or not isinstance(citation_id, str):
        return None
    conn = _connect(db_path)
    try:
        cited = conn.execute(
            "SELECT d.title, d.source_uri, c.text, c.offset_start, c.offset_end, c.source_node_id "
            "FROM chunks c JOIN documents d ON d.id = c.document_id "
            "WHERE d.id = ? AND c.id = ? AND d.collection_id = ?",
            (document_id, chunk_id, collection_id),
        ).fetchone()
        if cited is None:
            return None
        title, source_uri, excerpt, start, end, source_node_id = cited
        expected = _stable_chunk_citation({
            "source_uri": source_uri, "source_node_id": source_node_id,
            "offset_start": start, "offset_end": end, "text": excerpt,
        })
        if citation_id != expected:
            return None

        rows = conn.execute(
            "SELECT text, offset_start, offset_end FROM chunks "
            "WHERE document_id = ? AND source_node_id IS ? "
            "ORDER BY offset_start, ordinal",
            (document_id, source_node_id),
        ).fetchall()
    finally:
        conn.close()

    parts: list[str] = []
    position = 0
    for text, offset_start, offset_end in rows:
        first, last = int(offset_start), int(offset_end)
        if first < 0 or last < first or len(text) != last - first:
            return None
        if first > position:
            parts.append("\n" * (first - position))
            position = first
        if last > position:
            parts.append(text[position - first:])
            position = last
    content = "".join(parts)
    if content[int(start):int(end)] != excerpt:
        return None
    return {
        "title": title, "sourceUri": source_uri, "content": content,
        "offsetStart": int(start), "offsetEnd": int(end), "excerpt": excerpt,
    }
