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
from typing import TYPE_CHECKING, Protocol, cast

import numpy as np

from backend.knowledge_store import (
    chunks_pending_embedding,
    list_embeddings_for_search,
    upsert_embeddings,
)

if TYPE_CHECKING:
    from backend.providers.base import Provider

DEFAULT_BATCH_SIZE = 32
_VECTOR_DTYPE = "<f4"  # little-endian float32 - explicit, platform-independent


class _EmbeddingCapable(Protocol):
    """The `embed()` half of the provider seam, as this module needs it.

    backend/providers/base.py's `Provider` protocol declares only stream();
    `embed()` exists on exactly the providers that also set
    capabilities.embedding (OllamaProvider, OpenAIProvider), which is why
    both entry points below reject a provider whose capabilities say
    otherwise BEFORE calling it. This narrow protocol is what lets that
    runtime-checked invariant be stated to the type checker at the two call
    sites, without widening the shared `Provider` protocol into something
    the three non-embedding providers would no longer satisfy."""

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _pack_vector(vector) -> bytes:
    return np.asarray(vector, dtype=_VECTOR_DTYPE).tobytes()


def _unpack_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=_VECTOR_DTYPE)


def embed_pending_chunks(
    db_path: Path,
    provider: "Provider",
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
        vectors = cast(_EmbeddingCapable, provider).embed([row["text"] for row in batch])
        # Adversarial-review finding: zip() alone silently truncates/
        # mispairs if a provider ever returns a different-length list than
        # it was given (a non-conforming proxy, a future provider) -
        # chunk_id N would get pickled up against whatever vector landed
        # at position N instead of the one actually computed for its own
        # text, and the mistake is invisible from here on (the chunk is
        # marked "embedded" and never retried). A hard length check turns
        # that silent data corruption into an immediate, loud failure -
        # both OllamaProvider.embed() and OpenAIProvider.embed() are
        # documented as trusted for order, but trusted is not the same as
        # verified, and the check costs nothing on the happy path.
        if len(vectors) != len(batch):
            raise ValueError(
                f"provider.embed() returned {len(vectors)} vector(s) for a batch of "
                f"{len(batch)} text(s) - refusing to pair vectors to chunk_ids by "
                "position when the counts disagree."
            )
        rows = [
            (row["chunk_id"], len(vector), _pack_vector(vector))
            for row, vector in zip(batch, vectors)
        ]
        upsert_embeddings(db_path, model_id, rows)
        embedded_count += len(rows)
    return embedded_count


def vector_search(
    db_path: Path,
    provider: "Provider",
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

    query_vectors = cast(_EmbeddingCapable, provider).embed([query])
    if len(query_vectors) != 1:
        raise ValueError(
            f"provider.embed() returned {len(query_vectors)} vector(s) for a single query "
            f"(model_id={model_id!r}) - expected exactly 1."
        )
    query_array = np.asarray(query_vectors[0], dtype=_VECTOR_DTYPE)
    query_norm = np.linalg.norm(query_array)
    if query_norm == 0.0:
        return []

    # Adversarial-review finding: knowledge_store.py's own migration-003
    # docstring names `dim` specifically so a dimension mismatch (the same
    # model_id backing two different vector lengths - a re-pulled Ollama
    # tag with a different architecture, a repointed OpenAI-compatible
    # base_url) is "a cheap integer comparison, not a silent shape error
    # deep in a numpy call" - checked here, since np.stack() below is
    # exactly that silent-shape-error call the comment warns against.
    mismatched = [row for row in rows if row["dim"] != query_array.shape[0]]
    if mismatched:
        raise ValueError(
            f"model_id {model_id!r} has embeddings of mismatched dimension "
            f"(query embedded to {query_array.shape[0]}, but {len(mismatched)} stored "
            f"row(s) have dim={mismatched[0]['dim']}) - re-embed the affected chunks or "
            "delete their stale embedding rows before searching."
        )

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
            "token_count": rows[i]["token_count"],
            "offset_start": rows[i]["offset_start"], "offset_end": rows[i]["offset_end"],
            "document_title": rows[i]["document_title"], "source_uri": rows[i]["source_uri"],
            # ADR-020 stage 20.4: propagated straight through from
            # list_embeddings_for_search's own row shape - see that
            # function's own docstring for why a fused hybrid_search()
            # result needs this even when found only via this vector path.
            "source_node_id": rows[i]["source_node_id"],
            "score": float(similarities[i]),
        }
        for i in order
    ]
