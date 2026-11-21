"""Resolve the separately opted-in knowledge embedding source.

Knowledge embeddings are independent from the chat provider mode. Selecting Off
does not instantiate an embedding client or make a network request; selecting a
cloud source requires a persisted, explicit consent bit. Background embedding
jobs populate missing vectors in each workspace; searches use only completed
cached batches. A failed embedding service never disables keyword-only retrieval.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import logging
import math
from pathlib import Path
from typing import Iterator

import api_provider
from graphlink_cloud_providers import CLOUD_PROVIDERS

from backend.knowledge_retrieval import hybrid_search
from backend.providers.base import ProviderCapabilities
from backend.providers.ollama_provider import OllamaProvider

logger = logging.getLogger(__name__)
_TEST_TEXT = "Graphlink knowledge embedding connection test."


class _OpenAICompatibleEmbedder:
    """Use the existing OpenAI SDK client without assuming chat capabilities."""

    def __init__(self, client: object, model: str) -> None:
        self.client = client
        self.model_id = model
        self.capabilities = ProviderCapabilities(embedding=True)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model_id, input=list(texts))
        return [item.embedding for item in response.data]


@contextmanager
def embedding_provider_for_choice(manager, *, choice: dict | None = None) -> Iterator[tuple[object | None, str | None]]:
    """Create a disposable provider for the selected model; never expose keys over WS."""
    selected = choice if choice is not None else manager.get_knowledge_embedding_settings()
    source = selected.get("source", "off")
    model = str(selected.get("model") or "").strip()
    if source == "off":
        yield None, None
        return
    if not model or len(model) > 200:
        raise ValueError("Choose a knowledge embedding model.")
    if source == "ollama":
        if not api_provider.ollama_supports_embedding(model):
            raise ValueError(f"Ollama model {model!r} is not installed or does not support embeddings.")
        yield OllamaProvider(model=model), f"ollama:{model}"
        return
    if source != "cloud":
        raise ValueError("Unknown knowledge embedding source.")
    # This check is made on *every* use, not just the save UI: a malformed
    # older session.dat or a direct intent cannot cause an unapproved upload.
    if selected.get("cloud_opt_in") is not True:
        raise ValueError("Cloud embeddings require explicit consent: document text leaves the machine.")
    provider_name = str(selected.get("cloud_provider") or "")
    profile = CLOUD_PROVIDERS.get(provider_name)
    if profile is None or profile.protocol != "openai":
        raise ValueError("Choose an OpenAI-compatible API provider.")
    endpoint = manager.get_api_base_url(provider_name)
    key = manager.get_provider_key(provider_name)
    client, _, resolved_url = api_provider._build_api_client(provider_name, key, endpoint)
    # The URL belongs in the cache identity: re-pointing the same model name
    # at a different endpoint must not compare vectors from different spaces.
    endpoint_id = sha256(resolved_url.encode("utf-8")).hexdigest()[:16]
    try:
        yield _OpenAICompatibleEmbedder(client, model), f"cloud:{provider_name}:{endpoint_id}:{model}"
    finally:
        client.close()


def test_embedding_choice(
    manager, source: str, model: str, cloud_provider: str = "", cloud_opt_in: bool = False,
) -> int:
    """Test a disposable model using synthetic text, never stored documents."""
    with embedding_provider_for_choice(
        manager,
        choice={
            "source": source, "model": model, "cloud_provider": cloud_provider,
            "cloud_opt_in": cloud_opt_in,
        },
    ) as (provider, _model_id):
        if provider is None:
            return 0
        vectors = provider.embed([_TEST_TEXT])
        if len(vectors) != 1 or not vectors[0] or not all(math.isfinite(float(v)) for v in vectors[0]):
            raise ValueError("Embedding model returned an invalid vector.")
        return len(vectors[0])


def configured_hybrid_search(
    db_path: Path, query: str, *, settings_manager=None,
    collection_id: int | None = None, k: int = 10,
) -> list[dict]:
    """Retrieve from cached vectors (no indexing on search); fall back to FTS5."""
    if settings_manager is not None:
        try:
            with embedding_provider_for_choice(settings_manager) as (provider, model_id):
                if provider is not None and model_id is not None:
                    return hybrid_search(
                        db_path, query, embedding_provider=provider,
                        embedding_model_id=model_id, collection_id=collection_id, k=k,
                    )
        except Exception:
            logger.warning("Embedding service unavailable; using keyword knowledge search", exc_info=True)
    return hybrid_search(db_path, query, collection_id=collection_id, k=k)
