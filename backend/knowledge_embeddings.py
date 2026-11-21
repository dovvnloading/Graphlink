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

from heapq import heappush, heapreplace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol, cast

import numpy as np

from backend.knowledge_store import (
    embedding_hit_metadata,
    has_embeddings_for_search,
    iter_embedding_vector_batches,
    pending_embedding_batch,
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
    pending_rows: list[dict[str, Any]] | None = None,
    max_chunk_id: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_batch: Callable[[int], None] | None = None,
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
    pending is a legitimate, common outcome, not an error. The optional
    pinned pending rows, per-batch callback, and cancellation predicate let
    a background job report exact progress and stop between batches without
    ever repeating already committed work on resume.

    Raises ValueError up front (before any provider call) if `provider`
    was not constructed for an embedding-capable model - checked here,
    the dispatch layer, rather than inside `provider.embed()` itself,
    matching how ToolRegistry.invoke() checks scopes before calling a
    handler rather than trusting every handler to re-check."""
    if not provider.capabilities.embedding:
        raise ValueError(f"Provider for model {model_id!r} does not support embeddings.")
    if batch_size < 1:
        raise ValueError("batch_size must be positive.")

    def batches():
        if pending_rows is not None:
            for start in range(0, len(pending_rows), batch_size):
                yield pending_rows[start : start + batch_size]
            return
        after_chunk_id = 0
        while batch := pending_embedding_batch(
            db_path, model_id, collection_id=collection_id,
            after_chunk_id=after_chunk_id, max_chunk_id=max_chunk_id, limit=batch_size,
        ):
            yield batch
            after_chunk_id = batch[-1]["chunk_id"]

    embedded_count = 0
    for batch in batches():
        if should_cancel is not None and should_cancel():
            break
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
        # Cancellation can arrive during the provider's blocking call. Do
        # not persist an in-flight batch after the model/consent changed.
        if should_cancel is not None and should_cancel():
            break
        rows = [
            (row["chunk_id"], len(vector), _pack_vector(vector))
            for row, vector in zip(batch, vectors)
        ]
        upsert_embeddings(db_path, model_id, rows)
        embedded_count += len(rows)
        if on_batch is not None:
            on_batch(len(rows))
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
    """Stream a flat cosine scan and materialize only the best k passages.

    At 200,000 chunks, fetching all vector blobs *and* chunk text before
    scoring consumes hundreds of MiB. A fixed-size matrix and top-k heap
    preserve exact ranking, workspace boundaries and citation fields without
    making the peak allocation proportional to the corpus.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k!r}.")
    if not provider.capabilities.embedding:
        raise ValueError(f"Provider for model {model_id!r} does not support embeddings.")
    if not query.strip() or not has_embeddings_for_search(db_path, model_id, collection_id=collection_id):
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

    # A min-heap holds at most k (similarity, negative chunk id) pairs.
    # Negative IDs give deterministic ties without relying on SQLite scan
    # order or numpy's unstable default argsort.
    winners: list[tuple[float, int]] = []
    for batch in iter_embedding_vector_batches(db_path, model_id, collection_id=collection_id):
        mismatched = next(
            ((chunk_id, dim) for chunk_id, dim, vector in batch
             if dim != query_array.size or len(vector) != dim * 4), None,
        )
        if mismatched is not None:
            raise ValueError(
                f"model_id {model_id!r} has embeddings of mismatched dimension "
                f"(query dimension {query_array.size}, stored chunk {mismatched[0]} "
                f"has dim={mismatched[1]}) - re-embed stale chunks."
            )
        matrix = np.stack([_unpack_vector(vector) for _, _, vector in batch])
        norms = np.linalg.norm(matrix, axis=1)
        safe_norms = np.where(norms == 0.0, np.finfo(np.float32).eps, norms)
        similarities = (matrix @ query_array) / (safe_norms * query_norm)
        for (chunk_id, _, _), similarity in zip(batch, similarities):
            score = float(similarity)
            if not np.isfinite(score):
                score = -1.0
            rank = (score, -int(chunk_id))
            if len(winners) < k:
                heappush(winners, rank)
            elif rank > winners[0]:
                heapreplace(winners, rank)

    ranked = sorted(winners, reverse=True)
    metadata = embedding_hit_metadata(
        db_path, [-chunk_id for _, chunk_id in ranked], collection_id=collection_id,
    )
    return [
        {**metadata[-negative_id], "score": score}
        for score, negative_id in ranked
        if -negative_id in metadata
    ]
