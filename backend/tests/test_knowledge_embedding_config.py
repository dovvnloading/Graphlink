"""Knowledge embedding choice: privacy, persistence, caching, and graceful fallback.

All tests use a temporary database and in-memory embedding fakes. Neither
Ollama nor an API provider is contacted by this suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend import knowledge_embedding_config as config
from backend.knowledge_embeddings import embed_pending_chunks
from backend.knowledge_ingest import ingest_text
from backend.knowledge_store import list_embeddings_for_search
from backend.providers.base import ProviderCapabilities
from graphlink_settings_store import SettingsManager


@pytest.fixture
def manager(tmp_path):
    return SettingsManager(tmp_path / "session.dat")


def _database(tmp_path):
    path = tmp_path / "knowledge.db"
    ingest_text(
        "Graphlink archives important internal research notes.",
        source_uri="notes.md", title="Research notes", db_path=path,
    )
    return path


def test_off_default_never_constructs_local_or_cloud_clients(tmp_path, manager, monkeypatch):
    path = _database(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Off cannot contact an embedding provider")

    monkeypatch.setattr(config.api_provider, "ollama_supports_embedding", forbidden)
    monkeypatch.setattr(config.api_provider, "_build_api_client", forbidden)

    assert manager.get_knowledge_embedding_settings()["source"] == "off"
    hits = config.configured_hybrid_search(path, "internal research", settings_manager=manager)
    assert len(hits) == 1
    assert hits[0]["document_title"] == "Research notes"
    assert not list_embeddings_for_search(path, "ollama:nomic-embed-text:latest")


def test_choice_persists_and_requires_consent_across_reload(tmp_path, manager):
    manager.set_knowledge_embedding_settings("ollama", "nomic-embed-text:latest")
    assert manager.get_knowledge_embedding_settings()["source"] == "ollama"
    restored = SettingsManager(manager.state_file)
    assert restored.get_knowledge_embedding_settings()["model"] == "nomic-embed-text:latest"

    with pytest.raises(ValueError, match="explicit consent"):
        restored.set_knowledge_embedding_settings("cloud", "text-embedding-3-small", "OpenAI", False)
    assert restored.get_knowledge_embedding_settings()["source"] == "ollama"

    with pytest.raises(ValueError, match="OpenAI-compatible"):
        restored.set_knowledge_embedding_settings("cloud", "embedding", "Anthropic Claude", True)
    with pytest.raises(ValueError, match="Unknown"):
        restored.set_knowledge_embedding_settings("unknown")

    restored.set_knowledge_embedding_settings("cloud", "text-embedding-3-small", "OpenAI", True)
    assert SettingsManager(manager.state_file).get_knowledge_embedding_settings() == {
        "source": "cloud", "model": "text-embedding-3-small",
        "cloud_provider": "OpenAI", "cloud_opt_in": True,
    }
    restored.set_knowledge_embedding_settings("off")
    assert SettingsManager(manager.state_file).get_knowledge_embedding_settings()["cloud_opt_in"] is False


def test_tampered_cloud_choice_without_consent_is_off(manager):
    manager.state.update(
        knowledge_embedding_source="cloud", knowledge_embedding_model="text-embedding-3-small",
        knowledge_cloud_provider="OpenAI", knowledge_cloud_opt_in=False,
    )
    assert manager.get_knowledge_embedding_settings()["source"] == "off"
    # A raw override cannot skip the runtime boundary check.
    with pytest.raises(ValueError, match="consent"):
        with config.embedding_provider_for_choice(manager, choice={
            "source": "cloud", "model": "text-embedding-3-small",
            "cloud_provider": "OpenAI", "cloud_opt_in": False,
        }):
            pass


class _FakeEmbeddingProvider:
    model_id = "nomic-embed-text:latest"
    capabilities = ProviderCapabilities(embedding=True)

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts]


def test_search_uses_background_cache_without_backfilling_inline(tmp_path, manager, monkeypatch):
    path = _database(tmp_path)
    manager.set_knowledge_embedding_settings("ollama", "nomic-embed-text:latest")
    fake = _FakeEmbeddingProvider()
    monkeypatch.setattr(config.api_provider, "ollama_supports_embedding", lambda _model: True)
    monkeypatch.setattr(config, "OllamaProvider", lambda **_kwargs: fake)

    # A missing cache uses keyword search; it must not upload or embed
    # documents in the latency-sensitive search path.
    first = config.configured_hybrid_search(path, "internal research", settings_manager=manager)
    assert first
    assert list_embeddings_for_search(path, "ollama:nomic-embed-text:latest") == []
    assert fake.calls == []

    # The worker has populated the cache. Searches now embed only their
    # query; previously indexed passages are never sent again.
    assert embed_pending_chunks(path, fake, "ollama:nomic-embed-text:latest") == 1
    second = config.configured_hybrid_search(path, "internal research", settings_manager=manager)
    third = config.configured_hybrid_search(path, "internal research", settings_manager=manager)
    assert second and third
    assert len(list_embeddings_for_search(path, "ollama:nomic-embed-text:latest")) == 1
    assert len(fake.calls) == 3
    assert fake.calls[0][0].startswith("Graphlink archives")


def test_failed_embedding_falls_back_to_keyword_search(tmp_path, manager, monkeypatch):
    path = _database(tmp_path)
    manager.set_knowledge_embedding_settings("ollama", "nomic-embed-text:latest")
    monkeypatch.setattr(config.api_provider, "ollama_supports_embedding", lambda _model: True)

    class Failing(_FakeEmbeddingProvider):
        def embed(self, texts):
            raise RuntimeError("Ollama offline")

    monkeypatch.setattr(config, "OllamaProvider", lambda **_kwargs: Failing())
    hits = config.configured_hybrid_search(path, "internal research", settings_manager=manager)
    assert len(hits) == 1
    assert hits[0]["rank_signals"] == {"fts5": 1}


def test_cloud_test_uses_only_synthetic_phrase_and_closes_client(manager, monkeypatch):
    manager.set_knowledge_embedding_settings("cloud", "text-embedding-3-small", "OpenAI", True)
    requests = []
    closed = []

    class Client:
        embeddings = SimpleNamespace(create=lambda **kw: (
            requests.append(kw) or SimpleNamespace(data=[
                SimpleNamespace(embedding=[1.0, 2.0, 3.0]) for _ in kw["input"]
            ])
        ))

        def close(self):
            closed.append(True)

    monkeypatch.setattr(config.api_provider, "_build_api_client", lambda *_args: (
        Client(), "secret", "https://api.openai.com/v1",
    ))
    dimensions = config.test_embedding_choice(manager, "cloud", "text-embedding-3-small", "OpenAI", True)
    assert dimensions == 3
    assert requests == [{"model": "text-embedding-3-small", "input": [config._TEST_TEXT]}]
    assert closed == [True]

    with pytest.raises(ValueError, match="consent"):
        config.test_embedding_choice(manager, "cloud", "text-embedding-3-small", "OpenAI", False)
    assert len(requests) == 1


def test_ollama_test_rejects_nonembedding_chat_model(manager, monkeypatch):
    monkeypatch.setattr(config.api_provider, "ollama_supports_embedding", lambda _: False)
    with pytest.raises(ValueError, match="does not support embeddings"):
        config.test_embedding_choice(manager, "ollama", "llama3:latest")


def test_cloud_opt_in_sends_indexed_passage_and_query_and_revocation_stops_calls(
    tmp_path, manager, monkeypatch,
):
    """The cloud choice uploads chunk text AND query text, but only after consent."""
    path = _database(tmp_path)
    requests = []

    class Client:
        def __init__(self):
            self.embeddings = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(data=[
                SimpleNamespace(embedding=[1.0, 0.25]) for _ in kwargs["input"]
            ])

        def close(self):
            pass

    monkeypatch.setattr(config.api_provider, "_build_api_client", lambda *_args: (
        Client(), "test-credential", "https://api.openai.com/v1",
    ))
    manager.set_knowledge_embedding_settings(
        "cloud", "text-embedding-3-small", "OpenAI", True,
    )
    results = config.configured_hybrid_search(
        path, "internal", collection_id=0, settings_manager=manager,
    )
    assert results
    payloads = [text for request in requests for text in request["input"]]
    assert any("Graphlink archives important internal research notes." in text for text in payloads)
    assert "internal" in payloads

    # A revoked opt-in must not embed either pending documents or new searches.
    manager.set_knowledge_embedding_settings("off")
    sent_count = len(requests)
    assert config.configured_hybrid_search(
        path, "research", collection_id=0, settings_manager=manager,
    )
    assert len(requests) == sent_count
