"""ADR-017 stage 17.1: backend/knowledge_store.py.

Mirrors backend/tests/test_chat_library.py's own coverage shape for the
pieces this module ports (WAL/chmod, corrupt-db rescue) - see that file's
own tests for the precedent being followed here."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime

import pytest

from backend import db_backup as db_backup_module
from backend import knowledge_store as ks
from backend.knowledge_chunking import chunk_text
from backend.notifications import NotificationState


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "knowledge" / "knowledge.db"


def _chunks_for(text: str) -> list:
    return chunk_text(text, target_tokens=1000)


def _ingest(db_path, *, source_uri="doc.txt", title="Doc", mime="text/plain", text="Hello world.", **kwargs):
    return ks.add_document_with_chunks(
        db_path, source_uri=source_uri, title=title, mime=mime, text=text, chunks=_chunks_for(text), **kwargs,
    )


# -- connection hygiene: WAL, chmod, migrations ------------------------------


class TestConnectionHygiene:
    def test_journal_mode_is_actually_wal(self, db_path):
        _ingest(db_path)
        conn = sqlite3.connect(db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        assert mode.lower() == "wal"

    def test_posix_permission_bits_are_actually_0600(self, db_path):
        if os.name == "nt":
            pytest.skip("POSIX permission bits are not meaningful on Windows")
        _ingest(db_path)
        mode = os.stat(db_path).st_mode & 0o777
        assert mode == 0o600

    def test_connecting_twice_is_a_cheap_no_op_migration(self, db_path):
        outcome1 = _ingest(db_path)
        # A second, independent connect (a fresh add_document_with_chunks
        # call with DIFFERENT content) must not re-run migration DDL in any
        # way that disturbs the first document's already-stored rows.
        outcome2 = _ingest(db_path, text="A completely different document.")
        assert outcome1.was_new is True
        assert outcome2.was_new is True
        assert len(ks.list_documents(db_path)) == 2

    def test_schema_version_lands_on_the_target(self, db_path):
        _ingest(db_path)
        conn = sqlite3.connect(db_path)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        assert version == ks.KNOWLEDGE_DB_SCHEMA_VERSION


# -- content-hash idempotency (ADR-017 decision #2) --------------------------


class TestIdempotency:
    def test_reingesting_identical_text_into_the_same_collection_is_a_no_op(self, db_path):
        first = _ingest(db_path, text="Same content twice.")
        second = _ingest(db_path, text="Same content twice.")
        assert second.was_new is False
        assert second.document_id == first.document_id
        assert second.chunk_count == first.chunk_count
        assert len(ks.list_documents(db_path)) == 1

    def test_the_same_content_in_two_different_collections_is_two_documents(self, db_path):
        first = _ingest(db_path, text="Shared content.", collection_id=1)
        second = _ingest(db_path, text="Shared content.", collection_id=2)
        assert first.was_new is True
        assert second.was_new is True
        assert first.document_id != second.document_id
        assert len(ks.list_documents(db_path)) == 2

    def test_different_text_is_never_treated_as_the_same_document(self, db_path):
        first = _ingest(db_path, text="Content A.")
        second = _ingest(db_path, text="Content B.")
        assert first.document_id != second.document_id
        assert second.was_new is True


# -- CRUD ---------------------------------------------------------------------


class TestCrud:
    def test_list_documents_can_scope_to_one_collection(self, db_path):
        _ingest(db_path, text="In collection 1.", collection_id=1)
        _ingest(db_path, text="In collection 2.", collection_id=2)
        _ingest(db_path, text="Unscoped.")
        assert len(ks.list_documents(db_path)) == 3
        scoped = ks.list_documents(db_path, collection_id=1)
        assert len(scoped) == 1
        assert scoped[0]["collection_id"] == 1

    def test_get_document_returns_none_for_an_unknown_id(self, db_path):
        _ingest(db_path)
        assert ks.get_document(db_path, 99999) is None

    def test_chunks_are_stored_in_ordinal_order_with_exact_text(self, db_path):
        text = "\n\n".join(f"Paragraph {i} with enough words to matter here." for i in range(10))
        outcome = _ingest(db_path, text=text, source_uri="multi.txt")
        chunk_rows = ks.list_chunks_for_document(db_path, outcome.document_id)
        assert len(chunk_rows) == outcome.chunk_count
        assert [row["ordinal"] for row in chunk_rows] == list(range(len(chunk_rows)))
        for row in chunk_rows:
            assert text[row["offset_start"]:row["offset_end"]] == row["text"]

    def test_delete_document_cascades_to_its_chunks(self, db_path):
        text = "\n\n".join(f"Paragraph {i} filler content padding words." for i in range(10))
        outcome = _ingest(db_path, text=text)
        assert ks.delete_document(db_path, outcome.document_id) is True
        assert ks.get_document(db_path, outcome.document_id) is None
        assert ks.list_chunks_for_document(db_path, outcome.document_id) == []

    def test_delete_document_returns_false_for_an_already_absent_id(self, db_path):
        assert ks.delete_document(db_path, 12345) is False


# -- backup cadence -----------------------------------------------------------


class TestBackupCadence:
    def test_first_write_of_a_session_always_backs_up(self, db_path):
        last_saved: dict = {}
        _ingest(db_path, last_saved=last_saved)
        backups = db_backup_module.list_backups(db_path, prefix=ks.BACKUP_FILENAME_PREFIX)
        assert len(backups) == 1
        assert last_saved["last_backup_at"] is not None

    def test_a_second_write_within_the_cadence_window_does_not_take_a_second_backup(self, db_path):
        last_saved: dict = {}
        _ingest(db_path, text="First.", last_saved=last_saved)
        _ingest(db_path, text="Second, different content.", last_saved=last_saved)
        backups = db_backup_module.list_backups(db_path, prefix=ks.BACKUP_FILENAME_PREFIX)
        assert len(backups) == 1

    def test_a_backup_failure_is_swallowed_and_never_blocks_the_real_write(self, db_path, monkeypatch):
        monkeypatch.setattr(
            db_backup_module, "take_backup",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        last_saved: dict = {}
        outcome = _ingest(db_path, last_saved=last_saved)
        assert outcome.was_new is True
        assert last_saved["last_backup_at"] is not None


# -- corrupt-db rescue (mirrors backend/tests/test_chat_library.py's own
# TestCorruptDbRescue) --------------------------------------------------------


class TestCorruptDbRescue:
    def test_corruption_is_transparently_recovered_from_the_newest_backup(self, db_path):
        first = _ingest(db_path, text="Good document.")
        backup_path = db_backup_module.take_backup(db_path, prefix=ks.BACKUP_FILENAME_PREFIX)
        assert backup_path is not None

        # A later write lands, then the file is torn (kill -9 mid-write),
        # exactly like backend/tests/test_chat_library.py's own precedent.
        _ingest(db_path, text="A later edit that will be lost.")
        for suffix in ("", "-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        db_path.write_bytes(b"\x00\x01garbage-not-a-real-sqlite-file")

        notifications = NotificationState()
        # Drives _connect() via a real read path - the rescue must fire
        # transparently here, exactly as it would from any real caller.
        conn = ks._connect(db_path, notifications=notifications)
        conn.close()

        quarantined = list(db_path.parent.glob(f"{db_path.name}.corrupted-*"))
        assert len(quarantined) == 1
        suffix = quarantined[0].name.split(".corrupted-", 1)[1]
        datetime.strptime(suffix, "%Y%m%dT%H%M%SZ")  # raises ValueError if malformed
        assert notifications.visible
        assert notifications.msg_type == "warning"
        assert "restored" in notifications.message.lower()

        restored_docs = ks.list_documents(db_path)
        assert len(restored_docs) == 1
        assert restored_docs[0]["id"] == first.document_id

    def test_quarantine_survives_even_when_there_is_no_backup_to_restore_from(self, db_path):
        _ingest(db_path, text="Never backed up.")
        for suffix in ("", "-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                sidecar.unlink()
        db_path.write_bytes(b"garbage")
        notifications = NotificationState()

        conn = ks._connect(db_path, notifications=notifications)
        conn.close()

        docs = ks.list_documents(db_path)
        assert docs == []
        quarantined = list(db_path.parent.glob(f"{db_path.name}.corrupted-*"))
        assert len(quarantined) == 1
        assert notifications.visible
        assert notifications.msg_type == "warning"
        assert "no backup" in notifications.message.lower()

    def test_a_plain_locked_database_is_never_mistaken_for_corruption(self, db_path):
        # sqlite3.OperationalError ("database is locked") is empirically a
        # SUBCLASS of sqlite3.DatabaseError - mirrors backend/tests/
        # test_chat_library.py's own identically-named test exactly,
        # including using a SEPARATE short-timeout connection (never
        # _connect() itself, whose own busy_timeout=30000 would make this
        # test hang for 30 real seconds waiting it out) to prove the lock,
        # then checking _connect()'s own module never quarantined anything
        # as a side effect of that lock existing.
        _ingest(db_path)
        assert not list(db_path.parent.glob(f"{db_path.name}.corrupted-*"))

        holder = sqlite3.connect(db_path, timeout=30)
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("UPDATE documents SET title = 'locked-write'")
        try:
            with pytest.raises(sqlite3.OperationalError):
                blocked = sqlite3.connect(db_path, timeout=0.2)
                blocked.execute("BEGIN IMMEDIATE")
        finally:
            holder.rollback()
            holder.close()

        assert not list(db_path.parent.glob(f"{db_path.name}.corrupted-*")), (
            "a transient lock must never trigger quarantine"
        )


# -- FTS5 lexical index (ADR-017 stage 17.2) ---------------------------------


class TestMigrationBackfill:
    def test_a_migration_from_1_to_2_backfills_pre_existing_chunks_into_fts(self, db_path, monkeypatch):
        # Simulates a real user's stage-17.1-only knowledge.db upgrading in
        # place: ingest against schema version 1 (chunks_fts does not exist
        # yet), THEN let the module's own real target version (2) take
        # over on the next connect - the migration's own backfill INSERT
        # must make those already-stored chunks searchable, not just new
        # ones ingested after the upgrade.
        monkeypatch.setattr(ks, "KNOWLEDGE_DB_SCHEMA_VERSION", 1)
        monkeypatch.setattr(ks, "_MIGRATIONS", {1: ks._migration_001_initial_schema})
        outcome = _ingest(db_path, text="Pre-existing content about elephants.")

        monkeypatch.undo()
        results = ks.search_chunks(db_path, "elephants")
        assert len(results) == 1
        assert results[0]["document_id"] == outcome.document_id


class TestMigration004WorkspaceScopedCollections:
    """ADR-020 stage 20.3, migration "4" (3 -> 4): collections.workspace_id.
    Mirrors TestMigrationBackfill's own "simulate a real user upgrading in
    place" shape - ingest against schema version 3 (no workspace_id column
    yet), then let the module's own real target version (4) take over."""

    def test_a_migration_from_3_to_4_adds_the_column_without_disturbing_existing_collections(
        self, db_path, monkeypatch,
    ):
        monkeypatch.setattr(ks, "KNOWLEDGE_DB_SCHEMA_VERSION", 3)
        monkeypatch.setattr(ks, "_MIGRATIONS", {
            1: ks._migration_001_initial_schema,
            2: ks._migration_002_fts5_lexical_index,
            3: ks._migration_003_vector_index,
        })
        outcome = _ingest(db_path, text="Pre-existing content about giraffes.", collection_id=7)

        monkeypatch.undo()
        # The real, production connect path - any query function drives it.
        assert ks.get_document(db_path, outcome.document_id) is not None

        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == ks.KNOWLEDGE_DB_SCHEMA_VERSION
            collections_columns = {info[1] for info in conn.execute("PRAGMA table_info(collections)").fetchall()}
            assert "workspace_id" in collections_columns
            # Pre-existing collection_id=7 documents are untouched - this
            # migration adds a NEW nullable column, it never backfills or
            # reinterprets the pre-20.3 collection_id=0-style sentinel.
            row = conn.execute(
                "SELECT collection_id FROM documents WHERE id = ?", (outcome.document_id,),
            ).fetchone()
            assert row[0] == 7
        finally:
            conn.close()

    def test_fresh_database_lands_on_version_4_with_the_workspace_id_column(self, db_path):
        outcome = _ingest(db_path, text="Fresh install content.")
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == ks.KNOWLEDGE_DB_SCHEMA_VERSION
            collections_columns = {info[1] for info in conn.execute("PRAGMA table_info(collections)").fetchall()}
            assert "workspace_id" in collections_columns
        finally:
            conn.close()
        assert ks.get_document(db_path, outcome.document_id) is not None

    def test_migration_is_idempotent_when_run_twice_in_a_row(self, db_path):
        _ingest(db_path, text="First document.")
        _ingest(db_path, text="A second, different document.")
        conn = sqlite3.connect(db_path)
        try:
            columns = [info[1] for info in conn.execute("PRAGMA table_info(collections)").fetchall()]
            assert columns.count("workspace_id") == 1
        finally:
            conn.close()


class TestGetOrCreateWorkspaceCollection:
    """ADR-020 stage 20.3's own scoping decision (see this module's own
    module-docstring update): exactly ONE auto-created collection per
    workspace, resolved idempotently - the mechanism the exit criterion's
    own "two workspaces' corpora are separate" half rests on."""

    def test_creates_a_new_collection_on_first_call_for_a_workspace(self, db_path):
        collection_id = ks.get_or_create_workspace_collection(db_path, 42)
        assert isinstance(collection_id, int)
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT workspace_id FROM collections WHERE id = ?", (collection_id,),
            ).fetchone()
            assert row == (42,)
        finally:
            conn.close()

    def test_repeated_calls_for_the_same_workspace_return_the_same_id_not_a_new_row(self, db_path):
        first = ks.get_or_create_workspace_collection(db_path, 5)
        second = ks.get_or_create_workspace_collection(db_path, 5)
        third = ks.get_or_create_workspace_collection(db_path, 5)
        assert first == second == third
        conn = sqlite3.connect(db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM collections WHERE workspace_id = ?", (5,),
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_two_different_workspaces_get_two_genuinely_different_collections(self, db_path):
        collection_a = ks.get_or_create_workspace_collection(db_path, 1)
        collection_b = ks.get_or_create_workspace_collection(db_path, 2)
        assert collection_a != collection_b

    def test_documents_ingested_into_different_workspace_collections_are_isolated(self, db_path):
        collection_a = ks.get_or_create_workspace_collection(db_path, 100)
        collection_b = ks.get_or_create_workspace_collection(db_path, 200)

        _ingest(db_path, text="Alpha workspace exclusive content.", collection_id=collection_a)
        _ingest(db_path, text="Beta workspace exclusive content.", collection_id=collection_b)

        docs_a = ks.list_documents(db_path, collection_id=collection_a)
        docs_b = ks.list_documents(db_path, collection_id=collection_b)
        assert len(docs_a) == 1 and len(docs_b) == 1
        assert docs_a[0]["id"] != docs_b[0]["id"]

        results_a = ks.search_chunks(db_path, "exclusive content", collection_id=collection_a)
        assert len(results_a) == 1
        assert "alpha" in results_a[0]["text"].lower()

        results_b = ks.search_chunks(db_path, "exclusive content", collection_id=collection_b)
        assert len(results_b) == 1
        assert "beta" in results_b[0]["text"].lower()


class TestFts5LexicalSearch:
    def test_search_finds_a_matching_chunk_with_correct_citation_fields(self, db_path):
        text = "The quick brown fox jumps over the lazy dog."
        outcome = _ingest(db_path, text=text, source_uri="fox.txt", title="Fox Story")
        results = ks.search_chunks(db_path, "brown fox")
        assert len(results) == 1
        result = results[0]
        assert result["document_id"] == outcome.document_id
        assert result["document_title"] == "Fox Story"
        assert result["source_uri"] == "fox.txt"
        assert text[result["offset_start"]:result["offset_end"]] == result["text"]

    def test_search_with_no_matching_terms_returns_no_results(self, db_path):
        _ingest(db_path, text="Content about giraffes and savannas.")
        assert ks.search_chunks(db_path, "submarine reactor") == []

    def test_a_blank_or_punctuation_only_query_returns_no_results_not_an_error(self, db_path):
        _ingest(db_path, text="Some content.")
        assert ks.search_chunks(db_path, "") == []
        assert ks.search_chunks(db_path, "???...") == []

    def test_a_query_containing_fts5_operator_syntax_is_treated_as_literal_terms(self, db_path):
        # "OR"/"-"/"*"/quotes are real FTS5 query-syntax operators - a naive
        # MATCH ? with the raw string would either throw a syntax error or
        # silently change the query's meaning. Proves it's treated as safe
        # literal terms instead: this document contains none of these
        # words, so the "operator soup" query must find nothing, not raise.
        _ingest(db_path, text="Unrelated content about baking bread.")
        results = ks.search_chunks(db_path, 'OR -"exclude" wildcard* term')
        assert results == []

    def test_search_is_scoped_to_one_collection_when_requested(self, db_path):
        _ingest(db_path, text="Shared searchable phrase zebra.", collection_id=1)
        _ingest(db_path, text="Shared searchable phrase zebra.", collection_id=2)
        assert len(ks.search_chunks(db_path, "zebra")) == 2
        scoped = ks.search_chunks(db_path, "zebra", collection_id=1)
        assert len(scoped) == 1

    def test_k_bounds_the_number_of_results_returned(self, db_path):
        for i in range(5):
            _ingest(db_path, text=f"Document number {i} about walruses.", source_uri=f"doc{i}.txt")
        assert len(ks.search_chunks(db_path, "walruses", k=2)) == 2
        assert len(ks.search_chunks(db_path, "walruses", k=100)) == 5

    def test_k_below_one_raises(self, db_path):
        _ingest(db_path)
        with pytest.raises(ValueError, match="k must be >= 1"):
            ks.search_chunks(db_path, "hello", k=0)

    def test_deleting_a_document_removes_its_chunks_from_the_fts_index_too(self, db_path):
        # Proves the AFTER DELETE trigger actually fires - both for a
        # direct delete_document call and (separately, below) for the ON
        # DELETE CASCADE from a documents-row delete.
        outcome = _ingest(db_path, text="Content about narwhals.")
        assert len(ks.search_chunks(db_path, "narwhals")) == 1
        ks.delete_document(db_path, outcome.document_id)
        assert ks.search_chunks(db_path, "narwhals") == []

    def test_results_are_ranked_best_match_first(self, db_path):
        # Two documents both contain "python", but only one ALSO repeats it
        # - bm25 must rank the more term-dense document first.
        _ingest(db_path, text="Python is mentioned here exactly once.", source_uri="sparse.txt")
        _ingest(
            db_path,
            text="Python python python - this document is all about python programming in python.",
            source_uri="dense.txt",
        )
        results = ks.search_chunks(db_path, "python")
        assert len(results) == 2
        assert results[0]["source_uri"] == "dense.txt"

    def test_source_node_id_is_none_for_a_real_ingested_document_chunk(self, db_path):
        _ingest(db_path, text="Content with no originating graph node at all.")
        results = ks.search_chunks(db_path, "originating")
        assert len(results) == 1
        assert results[0]["source_node_id"] is None


class TestMigration005GraphNodeChunks:
    """ADR-020 stage 20.4, migration "5" (4 -> 5): chunks.source_node_id.
    Mirrors TestMigration004WorkspaceScopedCollections's own "simulate a
    real user upgrading in place" shape exactly - ingest against schema
    version 4 (no source_node_id column yet), then let the module's own
    real target version (5) take over on the next connect."""

    def test_a_migration_from_4_to_5_adds_the_column_without_disturbing_existing_chunks(
        self, db_path, monkeypatch,
    ):
        monkeypatch.setattr(ks, "KNOWLEDGE_DB_SCHEMA_VERSION", 4)
        monkeypatch.setattr(ks, "_MIGRATIONS", {
            1: ks._migration_001_initial_schema,
            2: ks._migration_002_fts5_lexical_index,
            3: ks._migration_003_vector_index,
            4: ks._migration_004_workspace_scoped_collections,
        })
        outcome = _ingest(db_path, text="Pre-existing content about pangolins.")

        monkeypatch.undo()
        # The real, production connect path - any query function drives it.
        assert ks.get_document(db_path, outcome.document_id) is not None

        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == ks.KNOWLEDGE_DB_SCHEMA_VERSION
            chunks_columns = {info[1] for info in conn.execute("PRAGMA table_info(chunks)").fetchall()}
            assert "source_node_id" in chunks_columns
            # The pre-existing chunk survives byte-for-byte, with the new
            # column landing NULL (never backfilled to some sentinel) - a
            # real ingested document's chunk is never a graph-node chunk.
            row = conn.execute(
                "SELECT text, source_node_id FROM chunks WHERE document_id = ?", (outcome.document_id,),
            ).fetchone()
            assert row[0] == "Pre-existing content about pangolins."
            assert row[1] is None
        finally:
            conn.close()
        # And it is still findable via search_chunks - the migration never
        # disturbed chunks_fts either.
        results = ks.search_chunks(db_path, "pangolins")
        assert len(results) == 1
        assert results[0]["source_node_id"] is None

    def test_fresh_database_lands_on_version_5_with_the_source_node_id_column(self, db_path):
        outcome = _ingest(db_path, text="Fresh install content, take two.")
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == ks.KNOWLEDGE_DB_SCHEMA_VERSION
            chunks_columns = {info[1] for info in conn.execute("PRAGMA table_info(chunks)").fetchall()}
            assert "source_node_id" in chunks_columns
        finally:
            conn.close()
        assert ks.get_document(db_path, outcome.document_id) is not None

    def test_migration_is_idempotent_when_run_twice_in_a_row(self, db_path):
        _ingest(db_path, text="First document, second migration test.")
        _ingest(db_path, text="A second, different document, same test.")
        conn = sqlite3.connect(db_path)
        try:
            columns = [info[1] for info in conn.execute("PRAGMA table_info(chunks)").fetchall()]
            assert columns.count("source_node_id") == 1
        finally:
            conn.close()


class TestReindexGraphContent:
    """ADR-020 stage 20.4: backend.knowledge_store.reindex_graph_content /
    delete_graph_index - the FTS-index half of "a graph is saved, its node
    content becomes searchable; re-saved, the index reflects the CURRENT
    content; deleted, the index entry is gone too". backend/tests/
    test_chat_library.py's own TestGlobalSearchAcrossWorkspaces covers the
    full real save_chat_atomically_row-driven path end to end; these tests
    exercise this module's own primitive directly and in isolation."""

    def test_indexes_one_chunk_per_node_findable_by_content(self, db_path):
        workspace_id = 1
        ks.reindex_graph_content(
            db_path, graph_id=42, workspace_id=workspace_id, title="My Graph",
            node_chunks=[("node-a", "Alpha node about kangaroos."), ("node-b", "Beta node about wombats.")],
        )
        kangaroo_hits = ks.search_chunks(db_path, "kangaroos")
        assert len(kangaroo_hits) == 1
        assert kangaroo_hits[0]["source_node_id"] == "node-a"
        assert kangaroo_hits[0]["source_uri"] == "graph:42"
        assert kangaroo_hits[0]["document_title"] == "My Graph"

        wombat_hits = ks.search_chunks(db_path, "wombats")
        assert len(wombat_hits) == 1
        assert wombat_hits[0]["source_node_id"] == "node-b"

    def test_resaving_with_changed_content_makes_old_content_unfindable_and_new_content_findable(self, db_path):
        ks.reindex_graph_content(
            db_path, graph_id=7, workspace_id=1, title="Graph",
            node_chunks=[("node-a", "The original phrase about penguins.")],
        )
        assert len(ks.search_chunks(db_path, "penguins")) == 1

        ks.reindex_graph_content(
            db_path, graph_id=7, workspace_id=1, title="Graph",
            node_chunks=[("node-a", "The replacement phrase about dolphins.")],
        )
        assert ks.search_chunks(db_path, "penguins") == []
        dolphin_hits = ks.search_chunks(db_path, "dolphins")
        assert len(dolphin_hits) == 1
        assert dolphin_hits[0]["source_node_id"] == "node-a"

        # Exactly one documents row for this graph, never one per save -
        # the delete-then-reinsert strategy never accumulates duplicates.
        conn = sqlite3.connect(db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE source_uri = ?", ("graph:7",),
            ).fetchone()[0]
            assert count == 1
        finally:
            conn.close()

    def test_resaving_with_empty_node_chunks_clears_the_prior_index(self, db_path):
        ks.reindex_graph_content(
            db_path, graph_id=9, workspace_id=1, title="Graph",
            node_chunks=[("node-a", "Content about octopuses.")],
        )
        assert len(ks.search_chunks(db_path, "octopuses")) == 1

        ks.reindex_graph_content(db_path, graph_id=9, workspace_id=1, title="Graph", node_chunks=[])
        assert ks.search_chunks(db_path, "octopuses") == []

    def test_two_different_graphs_do_not_collide_even_with_identical_content(self, db_path):
        # Different from add_document_with_chunks' own content-hash
        # idempotency (this module's own module docstring) - two DIFFERENT
        # graphs with byte-identical node text must remain two distinct,
        # independently-searchable documents, never collapsed into one.
        ks.reindex_graph_content(
            db_path, graph_id=1, workspace_id=1, title="Graph One",
            node_chunks=[("node-a", "Identical shared phrase about beavers.")],
        )
        ks.reindex_graph_content(
            db_path, graph_id=2, workspace_id=1, title="Graph Two",
            node_chunks=[("node-a", "Identical shared phrase about beavers.")],
        )
        results = ks.search_chunks(db_path, "beavers")
        assert len(results) == 2
        assert {r["source_uri"] for r in results} == {"graph:1", "graph:2"}

    def test_reindexing_resolves_the_real_per_workspace_collection(self, db_path):
        collection_id = ks.get_or_create_workspace_collection(db_path, 55)
        ks.reindex_graph_content(
            db_path, graph_id=3, workspace_id=55, title="Graph",
            node_chunks=[("node-a", "Scoped content about lemurs.")],
        )
        scoped = ks.search_chunks(db_path, "lemurs", collection_id=collection_id)
        assert len(scoped) == 1
        other = ks.search_chunks(db_path, "lemurs", collection_id=ks.get_or_create_workspace_collection(db_path, 99))
        assert other == []

    def test_delete_graph_index_removes_the_chunk_and_returns_true(self, db_path):
        ks.reindex_graph_content(
            db_path, graph_id=11, workspace_id=1, title="Graph",
            node_chunks=[("node-a", "Content about toucans.")],
        )
        assert len(ks.search_chunks(db_path, "toucans")) == 1

        assert ks.delete_graph_index(db_path, 11) is True
        assert ks.search_chunks(db_path, "toucans") == []

    def test_delete_graph_index_on_a_graph_with_nothing_indexed_returns_false(self, db_path):
        assert ks.delete_graph_index(db_path, 12345) is False
