"""Bounded memory and transaction regressions for large Knowledge Library workspaces."""

from __future__ import annotations

import pytest

from backend import knowledge_embeddings as ke
from backend import knowledge_store as ks
from backend.knowledge_chunking import chunk_text
from backend.providers.base import ProviderCapabilities


class FakeProvider:
    capabilities = ProviderCapabilities(embedding=True)

    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [self.vectors[text] for text in texts]


def test_embedding_job_uses_bounded_batches_and_a_pinned_high_water_mark(tmp_path):
    db = tmp_path / "knowledge.db"
    texts = [f"Unique research passage number {i}." for i in range(10)]
    for index, text in enumerate(texts[:9]):
        ks.add_document_with_chunks(
            db, source_uri=f"doc-{index}.md", title=f"doc-{index}",
            mime="text/markdown", text=text, chunks=chunk_text(text),
        )
    count, high_water = ks.pending_embedding_snapshot(db, "model", collection_id=0)
    assert count == 9

    # Simulate an import after the background job has already taken its
    # progress snapshot: this new chunk belongs to the next job, not this one.
    ks.add_document_with_chunks(
        db, source_uri="new.md", title="new", mime="text/markdown",
        text=texts[9], chunks=chunk_text(texts[9]),
    )
    provider = FakeProvider({text: [1.0, float(i)] for i, text in enumerate(texts)})
    assert ke.embed_pending_chunks(db, provider, "model", collection_id=0,
                                   max_chunk_id=high_water, batch_size=4) == 9
    assert [len(batch) for batch in provider.calls] == [4, 4, 1]
    assert len(ks.pending_embedding_batch(db, "model", collection_id=0, limit=10)) == 1

    # Resume must pick up just the new chunk instead of retransmitting the
    # nine already committed ones.
    assert ke.embed_pending_chunks(db, provider, "model", collection_id=0) == 1
    assert provider.calls[-1] == [texts[9]]
    assert ks.pending_embedding_snapshot(db, "model", collection_id=0)[0] == 0


def test_vector_search_streams_over_multiple_batches_then_reads_one_winner(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    conn = ks._connect(db)
    try:
        with conn:
            cursor = conn.execute(
                "INSERT INTO documents (collection_id, source_uri, title, mime, content_hash, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (77, "large.md", "Large Source", "text/markdown", "bulk-text", "2026-11-20T00:00:00Z"),
            )
            document_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO chunks (document_id, ordinal, text, token_count, offset_start, offset_end) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (document_id, n, f"passage {n}", 2, n * 32, n * 32 + len(f"passage {n}"))
                    for n in range(300)
                ],
            )
            chunk_ids = conn.execute(
                "SELECT id, ordinal FROM chunks WHERE document_id = ? ORDER BY ordinal",
                (document_id,),
            ).fetchall()
            conn.executemany(
                "INSERT INTO embeddings (chunk_id, model_id, dim, vector) VALUES (?, ?, ?, ?)",
                [
                    (chunk_id, "model", 2, ke._pack_vector([1, 0] if ordinal == 275 else [0, 1]))
                    for chunk_id, ordinal in chunk_ids
                ],
            )
    finally:
        conn.close()

    batch_sizes = []
    fetched_ids = []
    read_batches = ke.iter_embedding_vector_batches
    read_metadata = ke.embedding_hit_metadata

    def trace_batches(*args, **kwargs):
        for rows in read_batches(*args, **kwargs):
            batch_sizes.append(len(rows))
            yield rows

    def trace_metadata(db_path, ids, **kwargs):
        fetched_ids.extend(ids)
        return read_metadata(db_path, ids, **kwargs)

    monkeypatch.setattr(ke, "iter_embedding_vector_batches", trace_batches)
    monkeypatch.setattr(ke, "embedding_hit_metadata", trace_metadata)
    provider = FakeProvider({"needle": [1, 0]})
    [hit] = ke.vector_search(db, provider, "needle", model_id="model", collection_id=77, k=1)
    assert hit["text"] == "passage 275" and hit["document_title"] == "Large Source"
    assert hit["score"] == pytest.approx(1.0)
    assert batch_sizes == [256, 44]  # the entire corpus is never materialized
    assert fetched_ids == [chunk_ids[275][0]]  # passage text loaded for top-k only

    provider.calls.clear()
    assert ke.vector_search(db, provider, "needle", model_id="missing", collection_id=77) == []
    assert provider.calls == []  # an empty model index never contacts a provider
    assert ke.vector_search(db, provider, "needle", model_id="model", collection_id=999) == []


def test_reused_ingest_connection_preserves_per_file_rollback_and_aliases(tmp_path):
    db = tmp_path / "knowledge.db"
    conn = ks._connect(db)
    try:
        for index in range(10):
            text = f"Unique indexed file content {index}"
            outcome = ks.upsert_file_with_chunks(
                db, source_uri=f"/workspace/file-{index}.md", title=f"file-{index}.md",
                mime="text/markdown", text=text, chunks=chunk_text(text),
                collection_id=0, connection=conn,
            )
            assert outcome.action == "added"
        with pytest.raises(AttributeError):
            ks.upsert_file_with_chunks(
                db, source_uri="/workspace/broken.md", title="broken.md",
                mime="text/markdown", text="Rollback this file",
                chunks=[object()], collection_id=0, connection=conn,
            )
        assert len(ks.list_library_documents(db, collection_id=0)) == 10
        recovery = "Successful write after a failed import"
        assert ks.upsert_file_with_chunks(
            db, source_uri="/workspace/recovery.md", title="recovery.md",
            mime="text/markdown", text=recovery, chunks=chunk_text(recovery),
            collection_id=0, connection=conn,
        ).action == "added"
        assert len(ks.list_library_documents(db, collection_id=0)) == 11
        assert ks.search_chunks(db, "Rollback") == []
    finally:
        conn.close()
