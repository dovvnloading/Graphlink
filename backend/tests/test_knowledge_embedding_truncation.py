"""Oversized passages are never embedded partially and cached as whole (#1097)."""

from __future__ import annotations

import ollama
import pytest

from backend.knowledge_chunking import chunk_text
from backend.knowledge_embeddings import embed_pending_chunks
from backend.knowledge_ingest import ingest_text
from backend.knowledge_store import chunks_pending_embedding
from backend.providers import ollama_provider
from backend.providers.base import ProviderCapabilities
from backend.providers.ollama_provider import OllamaProvider
from backend.token_counter import TokenEstimator


def _provider(monkeypatch, embed):
    monkeypatch.setattr(ollama_provider.ollama, "embed", embed)
    provider = OllamaProvider.__new__(OllamaProvider)
    provider.model_id = "nomic-embed-text"
    provider.capabilities = ProviderCapabilities(embedding=True)
    return provider


def test_embedding_requests_disable_ollama_truncation(monkeypatch):
    calls = []

    def embed(**kwargs):
        calls.append(kwargs)
        return {"embeddings": [[1.0, 0.0] for _ in kwargs["input"]]}

    assert _provider(monkeypatch, embed).embed(["a", "b"]) == [[1.0, 0.0], [1.0, 0.0]]
    assert calls == [{"model": "nomic-embed-text", "input": ["a", "b"], "truncate": False}]


def test_a_rejected_oversized_passage_is_not_cached(monkeypatch, tmp_path):
    def embed(**_kwargs):
        raise ollama.ResponseError("the input length exceeds the context length", 400)

    db = tmp_path / "knowledge.db"
    ingest_text("A long passage about cryostat calibration.", source_uri="a.md", title="A", db_path=db)
    provider = _provider(monkeypatch, embed)
    with pytest.raises(ValueError, match="context window"):
        embed_pending_chunks(db, provider, "ollama:nomic-embed-text")
    assert len(chunks_pending_embedding(db, "ollama:nomic-embed-text")) == 1


def test_other_ollama_errors_are_not_relabelled(monkeypatch):
    def embed(**_kwargs):
        raise ollama.ResponseError("model not found", 404)

    with pytest.raises(ollama.ResponseError, match="not found"):
        _provider(monkeypatch, embed).embed(["a"])


def test_long_paragraph_chunks_stay_within_budget_with_exact_offsets():
    estimator = TokenEstimator()
    paragraph = " ".join(f"word{index}" for index in range(170))
    tail = "The distinctive zirconium-tail concept closes the document."
    text = "\n\n".join([paragraph] * 6 + [tail])
    chunks = chunk_text(text, target_tokens=400, overlap_tokens=50)
    for chunk in chunks:
        assert text[chunk.offset_start:chunk.offset_end] == chunk.text
        assert estimator.count_tokens(chunk.text) <= 400
    assert any("zirconium-tail" in chunk.text for chunk in chunks)
