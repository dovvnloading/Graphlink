"""Ollama embedding caches are scoped to the exact model revision (#1088)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend import knowledge_embedding_config as config
from backend.knowledge_embeddings import embed_pending_chunks, vector_search
from backend.knowledge_ingest import ingest_text
from backend.knowledge_store import chunks_pending_embedding
from backend.providers import ollama_provider
from backend.providers.base import ProviderCapabilities
from graphlink_settings_store import SettingsManager

MODEL = "nomic-embed-text"


class _Model:
    """Stands in for whichever weights are installed under the tag."""
    capabilities = ProviderCapabilities(embedding=True)

    def __init__(self, vectors):
        self.vectors = vectors
        self.model_id = MODEL

    def embed(self, texts):
        return [self.vectors[text] for text in texts]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    a = ingest_text("alpha passage", source_uri="a.md", title="A", db_path=db)
    ingest_text("beta passage", source_uri="b.md", title="B", db_path=db)
    manager = SettingsManager(tmp_path / "session.dat")
    manager.set_knowledge_embedding_settings("ollama", MODEL)
    installed = {"revision": "aaaaaaaaaaaaaaaa", "model": _Model({
        "alpha passage": [1.0, 0.0], "beta passage": [0.0, 1.0], "alpha": [1.0, 0.0]})}
    monkeypatch.setattr(config.api_provider, "ollama_supports_embedding", lambda _m: True)
    monkeypatch.setattr(config, "ollama_model_revision", lambda _m: installed["revision"])
    monkeypatch.setattr(config, "OllamaProvider", lambda **_k: installed["model"])
    return db, manager, installed, a.document_id


def _index(db, manager):
    with config.embedding_provider_for_choice(manager) as (provider, model_id):
        return model_id, embed_pending_chunks(db, provider, model_id), provider


def test_same_tag_replacement_uses_a_new_vector_space(setup):
    db, manager, installed, alpha = setup
    old_id, count, _ = _index(db, manager)
    assert old_id == "ollama:nomic-embed-text@aaaaaaaaaaaaaaaa" and count == 2
    # Same tag, same dimension, different weights: A and B swap vectors.
    installed.update(revision="bbbbbbbbbbbbbbbb", model=_Model({
        "alpha passage": [0.0, 1.0], "beta passage": [1.0, 0.0], "alpha": [0.0, 1.0]}))
    new_id, count, provider = _index(db, manager)
    assert new_id != old_id and count == 2
    # Mixing spaces would rank B first; the rebuilt space ranks A first.
    assert vector_search(db, provider, "alpha", model_id=new_id)[0]["document_id"] == alpha


def test_same_tag_replacement_with_a_new_dimension_rebuilds_cleanly(setup):
    db, manager, installed, alpha = setup
    _index(db, manager)
    installed.update(revision="cccccccccccccccc", model=_Model({
        "alpha passage": [1.0, 0.0, 0.0], "beta passage": [0.0, 1.0, 0.0], "alpha": [1.0, 0.0, 0.0]}))
    new_id, count, provider = _index(db, manager)
    assert count == 2
    assert vector_search(db, provider, "alpha", model_id=new_id)[0]["document_id"] == alpha


def test_unchanged_revision_reuses_the_cache(setup):
    db, manager, _, _ = setup
    first, count, _ = _index(db, manager)
    second, again, _ = _index(db, manager)
    assert first == second and count == 2 and again == 0


def test_replacement_during_indexing_leaves_the_batch_pending(setup):
    db, manager, installed, _ = setup
    original = installed["model"].embed

    def swap_mid_batch(texts):
        installed["revision"] = "dddddddddddddddd"
        return original(texts)

    installed["model"].embed = swap_mid_batch
    with config.embedding_provider_for_choice(manager) as (provider, model_id):
        with pytest.raises(ValueError, match="changed while indexing"):
            embed_pending_chunks(db, provider, model_id)
        assert len(chunks_pending_embedding(db, model_id)) == 2


def test_unreadable_revision_pauses_semantic_search_but_keyword_search_works(setup, monkeypatch):
    db, manager, _, alpha = setup
    monkeypatch.setattr(config, "ollama_model_revision", lambda _m: None)
    with pytest.raises(ValueError, match="keyword search still works"):
        with config.embedding_provider_for_choice(manager):
            pass
    hits = config.configured_hybrid_search(db, "alpha passage", settings_manager=manager)
    assert hits and hits[0]["document_id"] == alpha


@pytest.mark.parametrize(("listing", "model", "expected"), [
    ({"models": [{"model": "nomic-embed-text:latest", "digest": "sha256:0123456789ABCDEF99"}]},
     "nomic-embed-text", "0123456789abcdef"),
    (SimpleNamespace(models=[SimpleNamespace(model="lib/m:7b", digest="fedcba9876543210ff")]),
     "lib/m:7b", "fedcba9876543210"),
    ({"models": [{"model": "other:latest", "digest": "1111111111111111"}]}, "nomic-embed-text", None),
    ({"models": [{"model": "nomic-embed-text:latest", "digest": ""}]}, "nomic-embed-text", None),
])
def test_revision_lookup_matches_tags_and_reads_the_digest(monkeypatch, listing, model, expected):
    monkeypatch.setattr(ollama_provider.ollama, "list", lambda: listing)
    assert ollama_provider.ollama_model_revision(model) == expected


def test_revision_lookup_is_none_when_the_daemon_is_unreachable(monkeypatch):
    def down():
        raise ConnectionError("no daemon")
    monkeypatch.setattr(ollama_provider.ollama, "list", down)
    assert ollama_provider.ollama_model_revision(MODEL) is None
