"""ADR-017 stage 17.3: embed pending chunks and brute-force vector search.

Owns the two pieces backend/knowledge_store.py's own "no ML dependency"
comment deliberately keeps out of it: packing/unpacking a vector to/from
the `embeddings.vector` BLOB (numpy, explicit little-endian float32), and
the actual `Provider.embed()` calls plus the cosine-similarity scan over
them.

Brute-force, not sqlite-vec or an HNSW library: ADR-017's own "Alternatives
considered" names "a flat/HNSW index file" as the accepted alternative to a
loadable sqlite-vec extension - this is the flat option, backed by numpy
(already a hard dependency; no new one added). A local single-user
knowledge base's chunk count is thousands, not millions, where an O(n)
numpy scan is genuinely fast enough that reaching for approximate-nearest-
neighbor machinery would be solving a problem this app doesn't have.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from backend.knowledge_store import (
    chunks_pending_embedding,
    list_embeddings_for_search,
    upsert_embeddings,
)

DEFAULT_BATCH_SIZE = 32
_VECTOR_DTYPE = "<f4"  # little-endian float32 - explicit, platform-independent


def _pack_vector(vector) -> bytes:
    return np.asarray(vector, dtype=_VECTOR_DTYPE).tobytes()


def _unpack_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=_VECTOR_DTYPE)


def embed_pending_chunks(
    db_path: Path,
    provider,
    model_id: str,
    *,
    collection_id: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Embeds every chunk with no `(chunk_id, model_id)` row yet, via
    `provider.embed()` in batches of `batch_size`. This IS the cache the
    ADR's own stage-17.3 exit criterion names ("cache prevents
    re-embedding"): a chunk already embedded under `model_id` is never
    re-sent to the provider, whether this is a fresh ingest batch or a
    resumed run after a partial failure (a crash mid-batch leaves the
    already-`upsert_embeddings`-committed batches in place; re-running
    picks up exactly where it left off via the same pending-query).
    Returns the number of chunks newly embedded - 0 when nothing was
    pending is a legitimate, common outcome, not an error.

    Raises ValueError up front (before any provider call) if `provider`
    was not constructed for an embedding-capable model - checked here,
    the dispatch layer, rather than inside `provider.embed()` itself,
    matching how ToolRegistry.invoke() checks scopes before calling a
    handler rather than trusting every handler to re-check."""
    if not provider.capabilities.embedding:
        raise ValueError(f"Provider for model {model_id!r} does not support embeddings.")

    pending = chunks_pending_embedding(db_path, model_id, collection_id=collection_id)
    embedded_count = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = provider.embed([row["text"] for row in batch])
        rows = [
            (row["chunk_id"], len(vector), _pack_vector(vector))
            for row, vector in zip(batch, vectors)
        ]
        upsert_embeddings(db_path, model_id, rows)
        embedded_count += len(rows)
    return embedded_count


def vector_search(
    db_path: Path,
    provider,
    query: str,
    *,
    model_id: str,
    collection_id: int | None = None,
    k: int = 10,
) -> list[dict]:
    """Embeds `query` through the SAME `model_id` every stored vector was
    embedded with (mixing embedding models would compare vectors from
    different embedding spaces - a meaningless similarity number, not
    just a less-accurate one), then ranks every stored chunk by cosine
    similarity, best match first. Returns the same citation shape
    search_chunks() (FTS5, stage 17.2) returns, plus `score` - HIGHER is
    better here (cosine similarity), the OPPOSITE convention from FTS5's
    `bm25()` (lower is better) - stage 17.4's fusion must rank each list
    by its own ordering, never compare the two `score` values directly.

    Returns `[]` (no provider call, no error) for a blank query or a
    `model_id` with nothing embedded yet - both are legitimate "nothing to
    search" states, not failures."""
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k!r}.")
    if not provider.capabilities.embedding:
        raise ValueError(f"Provider for model {model_id!r} does not support embeddings.")
    if not query.strip():
        return []

    rows = list_embeddings_for_search(db_path, model_id, collection_id=collection_id)
    if not rows:
        return []

    [query_vector] = provider.embed([query])
    query_array = np.asarray(query_vector, dtype=_VECTOR_DTYPE)
    query_norm = np.linalg.norm(query_array)
    if query_norm == 0.0:
        return []

    matrix = np.stack([_unpack_vector(row["vector"]) for row in rows])
    matrix_norms = np.linalg.norm(matrix, axis=1)
    # A zero-norm stored vector (a pathological all-zero embedding) would
    # divide-by-zero into nan/inf rather than a real similarity score -
    # clamped to a tiny epsilon so such a row scores as "unrelated" (~0)
    # and never breaks the sort with a nan.
    safe_norms = np.where(matrix_norms == 0.0, np.finfo(np.float32).eps, matrix_norms)
    similarities = (matrix @ query_array) / (safe_norms * query_norm)

    order = np.argsort(-similarities)[:k]
    return [
        {
            "chunk_id": rows[i]["chunk_id"], "document_id": rows[i]["document_id"],
            "ordinal": rows[i]["ordinal"], "text": rows[i]["text"],
            "offset_start": rows[i]["offset_start"], "offset_end": rows[i]["offset_end"],
            "document_title": rows[i]["document_title"], "source_uri": rows[i]["source_uri"],
            "score": float(similarities[i]),
        }
        for i in order
    ]
