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
left for a future stage to build only if it turns out to be needed. The
pre-20.3 `collection_id=0` global/unscoped pool is untouched by this
stage: every document ingested before this migration, and every document
ingested by a caller with no real workspace context of its own (see e.g.
backend/api/intents_knowledge.py's own None-workspace fallback), keeps
landing there exactly as it always has.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
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

KNOWLEDGE_DB_SCHEMA_VERSION = 6


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


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migration_001_initial_schema,
    2: _migration_002_fts5_lexical_index,
    3: _migration_003_vector_index,
    4: _migration_004_workspace_scoped_collections,
    5: _migration_005_graph_node_chunks,
    6: _migration_006_unique_workspace_collections,
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
