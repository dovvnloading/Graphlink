"""Retained non-file sources keep ownership of deduplicated content (#1085)."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from backend import knowledge_store
from backend.knowledge_chunking import chunk_text
from backend.knowledge_ingest import ingest_text
from backend.knowledge_retrieval import hybrid_search

PASSAGE = "The reference cryostat holds the sample chamber at four kelvin during calibration."
URL = "https://example.org/cryostat"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "knowledge.db"


def _file(db, path, text):
    return knowledge_store.upsert_file_with_chunks(
        db, source_uri=str(path), title=path.name, mime="text/plain", text=text,
        chunks=chunk_text(text), collection_id=0,
    )


def _retain(db, url=URL, title="Cryostat page"):
    return ingest_text(PASSAGE, source_uri=url, title=title, db_path=db)


def _sources(db):
    return sorted(hit["source_file"] for hit in hybrid_search(db, "cryostat calibration", k=5))


def _chunk_rows(db):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


@pytest.mark.parametrize("order", ["file-then-url", "url-then-file"])
def test_retained_url_survives_changed_file_in_either_import_order(db, tmp_path, order):
    path = tmp_path / "notes.txt"
    if order == "file-then-url":
        first = _file(db, path, PASSAGE)
        retained = _retain(db)
        assert retained.document_id == first.document_id and not retained.was_new
    else:
        retained = _retain(db)
        assert _file(db, path, PASSAGE).document_id == retained.document_id
    chunks = _chunk_rows(db)
    # The local file changes: it gets its own content, the page keeps the old.
    _file(db, path, "Unrelated replacement text about the pump schedule.")
    hits = hybrid_search(db, "cryostat calibration", k=5)
    assert [hit["source_file"] for hit in hits] == [URL]
    assert hits[0]["citation_id"]
    document = knowledge_store.get_document(db, retained.document_id)
    assert document["source_uri"] == URL and document["title"] == "Cryostat page"
    # Content stayed deduplicated: only the new file's chunks were added.
    assert _chunk_rows(db) == chunks + len(chunk_text("Unrelated replacement text about the pump schedule."))


def test_retained_url_survives_watched_file_disappearance(db, tmp_path):
    folder = tmp_path / "watched"
    folder.mkdir()
    path = folder / "notes.txt"
    path.write_text(PASSAGE)
    watch = knowledge_store.add_watched_folder(db, 1, str(folder))
    _file(db, path, PASSAGE)
    knowledge_store.track_watched_file(db, watch["id"], str(path), size_bytes=1, mtime_ns=1)
    _retain(db)
    path.unlink()
    knowledge_store.reconcile_watched_files(db, watch["id"], 0, set(), prune=True)
    assert _sources(db) == [URL]


def test_several_retained_urls_keep_one_copy_and_a_deterministic_citation(db, tmp_path):
    path = tmp_path / "notes.txt"
    document_id = _file(db, path, PASSAGE).document_id
    _retain(db, "https://example.org/b", "Mirror B")
    _retain(db, "https://example.org/a", "Mirror A")
    chunks = _chunk_rows(db)
    _file(db, path, "Different file contents now.")
    document = knowledge_store.get_document(db, document_id)
    assert (document["source_uri"], document["title"]) == ("https://example.org/a", "Mirror A")
    assert _sources(db) == ["https://example.org/a"]
    assert _chunk_rows(db) == chunks + len(chunk_text("Different file contents now."))


def test_file_without_a_retained_owner_is_still_removed(db, tmp_path):
    path = tmp_path / "notes.txt"
    _file(db, path, PASSAGE)
    _file(db, path, "Different file contents now.")
    assert _sources(db) == []


def test_migration_adds_the_table_without_inventing_owners(db, tmp_path):
    path = tmp_path / "notes.txt"
    _file(db, path, PASSAGE)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == knowledge_store.KNOWLEDGE_DB_SCHEMA_VERSION == 10
        assert conn.execute("SELECT COUNT(*) FROM retained_sources").fetchone()[0] == 0
