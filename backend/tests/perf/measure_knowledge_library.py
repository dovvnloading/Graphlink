"""Reproducible Knowledge Library scale audit (not a timing ratchet).

Run from the repository root:
    python -m backend.tests.perf.measure_knowledge_library

Creates a disposable SQLite workspace with exactly 5,000 documents and
200,000 searchable, embedded chunks, and measures list, lexical/vector search,
verified citation navigation, and bounded ingestion/embedding throughput.
Results are machine/SSD/provider-dependent; no hard CI wall-time budget is
imposed. The fake provider measures local coordination, not cloud latency.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from backend import knowledge_embeddings as ke
from backend import knowledge_store as ks
from backend.knowledge_chunking import chunk_text
from backend.knowledge_retrieval import hybrid_search
from backend.providers.base import ProviderCapabilities

DOCUMENTS = 5_000
CHUNKS_PER_DOCUMENT = 40
EMBED_DIM = 16
THROUGHPUT_CHUNKS = 3_200
INGEST_FILES = 200


class SyntheticProvider:
    capabilities = ProviderCapabilities(embedding=True)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]


def timed(call):
    started = time.perf_counter()
    result = call()
    return result, round((time.perf_counter() - started) * 1_000, 2)


def seed(db: Path) -> None:
    """Use bulk inserts to create the corpus; ingestion is timed separately."""
    regular = ke._pack_vector([0.0, 1.0] + [0.0] * (EMBED_DIM - 2))
    standout = ke._pack_vector([1.0] + [0.0] * (EMBED_DIM - 1))
    conn = ks._connect(db)
    try:
        with conn:
            conn.executemany(
                "INSERT INTO documents (collection_id, source_uri, title, mime, content_hash, added_at) "
                "VALUES (0, ?, ?, 'text/markdown', ?, '2026-11-20T00:00:00Z')",
                ((f"/fixture/doc-{i:05}.md", f"doc-{i:05}.md", f"{i:064x}") for i in range(DOCUMENTS)),
            )
            document_ids = [row[0] for row in conn.execute("SELECT id FROM documents ORDER BY id")]
            for i, document_id in enumerate(document_ids):
                offset = 0
                chunks = []
                for ordinal in range(CHUNKS_PER_DOCUMENT):
                    marker = "benchmarkneedle" if (i, ordinal) == (2500, 12) else "ordinary"
                    text = f"# Part {ordinal // 10}\n\nDocument {i}, passage {ordinal}: {marker} retrieval evidence.\n\n"
                    chunks.append((document_id, ordinal, text, len(text.split()), offset, offset + len(text)))
                    offset += len(text)
                conn.executemany(
                    "INSERT INTO chunks (document_id, ordinal, text, token_count, offset_start, offset_end) "
                    "VALUES (?, ?, ?, ?, ?, ?)", chunks,
                )
            vector_cursor = conn.execute("SELECT id, document_id, ordinal FROM chunks ORDER BY id")
            while batch := vector_cursor.fetchmany(2_048):
                conn.executemany(
                    "INSERT INTO embeddings (chunk_id, model_id, dim, vector) VALUES (?, ?, ?, ?)",
                    [
                        (chunk_id, "benchmark-model", EMBED_DIM,
                         standout if (document_id, ordinal) == (document_ids[2500], 12) else regular)
                        for chunk_id, document_id, ordinal in batch
                    ],
                )
    finally:
        conn.close()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="graphlink-knowledge-scale-") as directory:
        db = Path(directory) / "knowledge.db"
        _, seed_ms = timed(lambda: seed(db))

        library, list_ms = timed(lambda: ks.list_library_documents(db, collection_id=0))
        if len(library) != DOCUMENTS or sum(row["chunks"] for row in library) != DOCUMENTS * CHUNKS_PER_DOCUMENT:
            raise AssertionError("Scale fixture did not preserve 5,000 documents / 200,000 chunks.")

        keyword, lexical_ms = timed(lambda: hybrid_search(db, "benchmarkneedle", collection_id=0, k=10))
        if not keyword:
            raise AssertionError("Fixture needle not found in FTS5.")
        first = keyword[0]
        cited, citation_ms = timed(lambda: ks.open_cited_document(
            db, document_id=first["document_id"], chunk_id=first["chunk_id"],
            citation_id=first["citation_id"], collection_id=0,
        ))
        if cited is None or cited["content"][cited["offsetStart"]:cited["offsetEnd"]] != cited["excerpt"]:
            raise AssertionError("Verified citation failed to resolve its exact passage.")

        provider = SyntheticProvider()
        semantic, vector_ms = timed(lambda: ke.vector_search(
            db, provider, "benchmarkneedle", model_id="benchmark-model", collection_id=0, k=10,
        ))
        if not semantic or semantic[0]["text"] != first["text"]:
            raise AssertionError("Flat vector scan returned the wrong most-similar passage.")

        conn = ks._connect(db)
        try:
            high_water = conn.execute(
                "SELECT id FROM chunks ORDER BY id LIMIT 1 OFFSET ?", (THROUGHPUT_CHUNKS - 1,),
            ).fetchone()[0]
        finally:
            conn.close()
        embedded, embed_ms = timed(lambda: ke.embed_pending_chunks(
            db, provider, "throughput-model", collection_id=0, batch_size=32, max_chunk_id=high_water,
        ))
        if embedded != THROUGHPUT_CHUNKS:
            raise AssertionError("Bounded embedding batch skipped or duplicated chunks.")

        def import_files():
            conn = ks._connect(db)
            try:
                for i in range(INGEST_FILES):
                    text = f"New file content, import throughput sample {i}."
                    ks.upsert_file_with_chunks(
                        db, source_uri=f"/fixture/new-{i}.md", title=f"new-{i}.md",
                        mime="text/markdown", text=text, chunks=chunk_text(text),
                        collection_id=0, connection=conn,
                    )
            finally:
                conn.close()

        _, ingest_ms = timed(import_files)
        metrics = {
            "documents": DOCUMENTS,
            "chunks": DOCUMENTS * CHUNKS_PER_DOCUMENT,
            "fixture_seed_ms": seed_ms,
            "document_list_ms": list_ms,
            "fts5_keyword_search_ms": lexical_ms,
            "vector_exact_top10_ms": vector_ms,
            "verified_citation_open_ms": citation_ms,
            "embedding_chunks": embedded,
            "embedding_chunks_per_second": round(embedded / (embed_ms / 1_000), 1),
            "ingested_files": INGEST_FILES,
            "ingestion_files_per_second": round(INGEST_FILES / (ingest_ms / 1_000), 1),
            "knowledge_db_bytes": db.stat().st_size,
        }
        sys.stdout.write(json.dumps(metrics, indent=2) + "\n")


if __name__ == "__main__":
    main()
