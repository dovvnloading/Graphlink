"""ADR-017 stage 17.3: Provider.embed() (Ollama + OpenAI), the embedding
cache (embed_pending_chunks), and brute-force vector search.

Exit criterion this file proves (ADR-017 doc, stage 17.3 row): "Paraphrase
query retrieves the right chunk; cache prevents re-embedding."
"""

from __future__ import annotations

import types

import numpy as np
import pytest

from backend.knowledge_chunking import chunk_text
from backend.knowledge_embeddings import (
    _pack_vector,
    _unpack_vector,
    embed_pending_chunks,
    vector_search,
)
from backend.knowledge_store import add_document_with_chunks
from backend.providers.base import ProviderCapabilities


# -- fakes --------------------------------------------------------------------


class FakeEmbeddingProvider:
    """A controllable Provider stand-in: `vectors` maps exact input text ->
    the vector to return, so a test can construct known, deterministic
    embeddings rather than depending on any real model's actual output -
    exactly what similarity-ranking assertions need."""

    def __init__(self, vectors: dict, *, capable: bool = True):
        self.vectors = vectors
        self.capabilities = ProviderCapabilities(embedding=capable)
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [self.vectors[t] for t in texts]


def _ingest(db_path, *, text="Hello world.", **kwargs):
    return add_document_with_chunks(
        db_path,
        source_uri=kwargs.pop("source_uri", "doc.txt"),
        title=kwargs.pop("title", "Doc"),
        mime="text/plain",
        text=text,
        chunks=chunk_text(text, target_tokens=1000),
        **kwargs,
    )


# -- Ollama/OpenAI Provider.embed() ------------------------------------------


class TestOllamaEmbed:
    def test_embed_calls_ollamas_batch_endpoint_and_preserves_order(self, monkeypatch):
        from backend.providers import OllamaProvider

        captured = {}

        def fake_embed(**kwargs):
            captured.update(kwargs)
            return {"embeddings": [[1.0, 2.0], [3.0, 4.0]]}

        import ollama
        monkeypatch.setattr(ollama, "embed", fake_embed)

        provider = OllamaProvider(model="nomic-embed-text")
        result = provider.embed(["first", "second"])

        assert result == [[1.0, 2.0], [3.0, 4.0]]
        assert captured["model"] == "nomic-embed-text"
        assert captured["input"] == ["first", "second"]

    def test_embed_of_an_empty_list_is_a_no_op_with_no_network_call(self, monkeypatch):
        from backend.providers import OllamaProvider

        def fail_if_called(**kwargs):
            raise AssertionError("ollama.embed should not be called for an empty batch")

        import ollama
        monkeypatch.setattr(ollama, "embed", fail_if_called)

        assert OllamaProvider(model="nomic-embed-text").embed([]) == []

    def test_capabilities_embedding_is_a_real_per_model_probe(self, monkeypatch):
        from backend.providers import OllamaProvider
        import api_provider
        import ollama
        from unittest.mock import patch

        # A prior test in this module may have already constructed an
        # OllamaProvider for one of these exact model names with
        # ollama.show unmocked (a fast-failing "no daemon" real call),
        # caching a negative result under that model's key - cleared here
        # so this test's own patched show() is what actually answers,
        # mirroring test_tool_calling.py's own identical precedent.
        monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {})
        with patch.object(ollama, "show", return_value={"capabilities": ["completion", "embedding"]}):
            assert OllamaProvider(model="nomic-embed-text").capabilities.embedding is True
        monkeypatch.setattr(api_provider, "_OLLAMA_CAPABILITY_CACHE", {})
        with patch.object(ollama, "show", return_value={"capabilities": ["completion", "tools"]}):
            assert OllamaProvider(model="llama3").capabilities.embedding is False


class TestOpenAIEmbed:
    def _fake_client(self, vector_for_text):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return types.SimpleNamespace(
                data=[
                    types.SimpleNamespace(embedding=vector_for_text[text])
                    for text in kwargs["input"]
                ]
            )

        client = types.SimpleNamespace(embeddings=types.SimpleNamespace(create=create))
        return client, captured

    def test_embed_calls_the_sdks_batch_endpoint_and_preserves_order(self):
        from backend.providers import OpenAIProvider

        client, captured = self._fake_client({"a": [0.1, 0.2], "b": [0.3, 0.4]})
        provider = OpenAIProvider(client=client, model="text-embedding-3-small")

        result = provider.embed(["a", "b"])

        assert result == [[0.1, 0.2], [0.3, 0.4]]
        assert captured["model"] == "text-embedding-3-small"
        assert captured["input"] == ["a", "b"]

    def test_embed_of_an_empty_list_is_a_no_op_with_no_network_call(self):
        from backend.providers import OpenAIProvider

        def fail_if_called(**kwargs):
            raise AssertionError("embeddings.create should not be called for an empty batch")

        client = types.SimpleNamespace(embeddings=types.SimpleNamespace(create=fail_if_called))
        assert OpenAIProvider(client=client, model="text-embedding-3-small").embed([]) == []

    def test_capabilities_embedding_is_derived_from_the_client_not_asserted(self):
        from backend.providers import OpenAIProvider

        assert OpenAIProvider(client=None, model="gpt-5").capabilities.embedding is False
        client, _ = self._fake_client({})
        assert OpenAIProvider(client=client, model="text-embedding-3-small").capabilities.embedding is True


class TestRemainingProvidersDeclareNoEmbedding:
    def test_anthropic_gemini_llama_cpp_all_report_embedding_false(self):
        from backend.providers import AnthropicProvider, GeminiProvider, LlamaCppProvider

        assert AnthropicProvider(client=None, api_key="k", model="claude-opus-5").capabilities.embedding is False
        assert GeminiProvider(api_key="k", model="gemini-2.5-pro").capabilities.embedding is False
        assert LlamaCppProvider(settings={"chat_model_path": "m.gguf"}).capabilities.embedding is False


# -- vector pack/unpack --------------------------------------------------------


def test_pack_and_unpack_vector_round_trips_exactly():
    original = [0.1, -0.2, 3.5, 0.0]
    packed = _pack_vector(original)
    assert isinstance(packed, bytes)
    unpacked = _unpack_vector(packed)
    np.testing.assert_allclose(unpacked, original, rtol=1e-6)


# -- embed_pending_chunks: the cache ------------------------------------------


class TestEmbedPendingChunks:
    def test_embeds_every_chunk_with_no_row_yet(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        outcome = _ingest(db_path, text="Hello world.")
        provider = FakeEmbeddingProvider({"Hello world.": [1.0, 0.0]})

        count = embed_pending_chunks(db_path, provider, "fake-model")

        assert count == outcome.chunk_count == 1
        assert provider.calls == [["Hello world."]]

    def test_a_second_call_embeds_nothing_new_the_cache_prevents_re_embedding(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path, text="Hello world.")
        provider = FakeEmbeddingProvider({"Hello world.": [1.0, 0.0]})

        first_count = embed_pending_chunks(db_path, provider, "fake-model")
        second_count = embed_pending_chunks(db_path, provider, "fake-model")

        assert first_count == 1
        assert second_count == 0
        assert len(provider.calls) == 1  # the provider was never called again

    def test_a_different_model_id_re_embeds_independently(self, tmp_path):
        # Switching embedding models must not be blocked by the OTHER
        # model's cache rows - (chunk_id, model_id) is the real key.
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path, text="Hello world.")
        provider = FakeEmbeddingProvider({"Hello world.": [1.0, 0.0]})

        embed_pending_chunks(db_path, provider, "model-a")
        second_count = embed_pending_chunks(db_path, provider, "model-b")

        assert second_count == 1

    def test_only_new_chunks_are_embedded_when_more_are_ingested_later(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path, text="First document.", source_uri="a.txt")
        provider = FakeEmbeddingProvider(
            {"First document.": [1.0, 0.0], "Second document.": [0.0, 1.0]}
        )
        embed_pending_chunks(db_path, provider, "fake-model")

        _ingest(db_path, text="Second document.", source_uri="b.txt")
        second_count = embed_pending_chunks(db_path, provider, "fake-model")

        assert second_count == 1
        assert provider.calls[-1] == ["Second document."]

    def test_batching_splits_into_multiple_provider_calls(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        vectors = {}
        for i in range(5):
            text = f"Document number {i}."
            _ingest(db_path, text=text, source_uri=f"doc{i}.txt")
            vectors[text] = [float(i), 0.0]
        provider = FakeEmbeddingProvider(vectors)

        count = embed_pending_chunks(db_path, provider, "fake-model", batch_size=2)

        assert count == 5
        assert len(provider.calls) == 3  # 2 + 2 + 1
        assert [len(c) for c in provider.calls] == [2, 2, 1]

    def test_raises_up_front_for_a_non_embedding_capable_provider(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path)
        provider = FakeEmbeddingProvider({}, capable=False)

        with pytest.raises(ValueError, match="does not support embeddings"):
            embed_pending_chunks(db_path, provider, "chat-model")
        assert provider.calls == []  # never even tried

    def test_raises_instead_of_silently_mispairing_when_the_provider_returns_too_few_vectors(self, tmp_path):
        # Adversarial-review finding: zip(batch, vectors) alone would
        # silently truncate to the shorter length, pairing chunk_id[0]
        # with vectors[0] but leaving chunk_id[1] unembedded with NO
        # error - this proves the length guard fires instead.
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path, text="First document.", source_uri="a.txt")
        _ingest(db_path, text="Second document.", source_uri="b.txt")

        class DroppingProvider(FakeEmbeddingProvider):
            def embed(self, texts):
                super().embed(texts)
                return [self.vectors[texts[0]]]  # drops every entry after the first

        provider = DroppingProvider(
            {"First document.": [1.0, 0.0], "Second document.": [0.0, 1.0]}
        )
        with pytest.raises(ValueError, match="returned 1 vector"):
            embed_pending_chunks(db_path, provider, "fake-model")


# -- vector_search: brute-force cosine similarity -----------------------------


class TestVectorSearch:
    def test_finds_the_closest_chunk_by_cosine_similarity_not_lexical_overlap(self, tmp_path):
        # The whole point of vector search: NO word overlap between the
        # query and the winning chunk's text, only vector proximity - what
        # an FTS5-only search could never do (the ADR's own "paraphrase
        # query retrieves the right chunk" exit criterion, stage 17.3 row).
        db_path = tmp_path / "knowledge.db"
        near = _ingest(db_path, text="The feline sat on the mat.", source_uri="near.txt")
        _ingest(db_path, text="Stock markets fell sharply today.", source_uri="far.txt")

        provider = FakeEmbeddingProvider(
            {
                "The feline sat on the mat.": [1.0, 0.0],
                "Stock markets fell sharply today.": [0.0, 1.0],
                "a cat on a rug": [0.99, 0.01],
            }
        )
        embed_pending_chunks(db_path, provider, "fake-model")

        results = vector_search(db_path, provider, "a cat on a rug", model_id="fake-model")

        assert len(results) == 2
        assert results[0]["document_id"] == near.document_id
        assert results[0]["source_uri"] == "near.txt"
        assert results[0]["score"] > results[1]["score"]

    def test_a_different_model_ids_vectors_are_never_mixed_into_the_scan(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path, text="Content A.")
        provider = FakeEmbeddingProvider({"Content A.": [1.0, 0.0], "query": [1.0, 0.0]})
        embed_pending_chunks(db_path, provider, "model-a")

        results = vector_search(db_path, provider, "query", model_id="model-b")
        assert results == []

    def test_search_is_scoped_to_one_collection_when_requested(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path, text="Zebra content one.", collection_id=1, source_uri="a.txt")
        _ingest(db_path, text="Zebra content two.", collection_id=2, source_uri="b.txt")
        provider = FakeEmbeddingProvider(
            {
                "Zebra content one.": [1.0, 0.0],
                "Zebra content two.": [1.0, 0.0],
                "zebra query": [1.0, 0.0],
            }
        )
        embed_pending_chunks(db_path, provider, "fake-model")

        assert len(vector_search(db_path, provider, "zebra query", model_id="fake-model")) == 2
        scoped = vector_search(db_path, provider, "zebra query", model_id="fake-model", collection_id=1)
        assert len(scoped) == 1
        assert scoped[0]["source_uri"] == "a.txt"

    def test_k_bounds_the_number_of_results_returned(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        vectors = {"query": [1.0, 0.0]}
        for i in range(5):
            text = f"Walrus fact number {i}."
            _ingest(db_path, text=text, source_uri=f"w{i}.txt")
            vectors[text] = [1.0, float(i)]
        provider = FakeEmbeddingProvider(vectors)
        embed_pending_chunks(db_path, provider, "fake-model")

        assert len(vector_search(db_path, provider, "query", model_id="fake-model", k=2)) == 2
        assert len(vector_search(db_path, provider, "query", model_id="fake-model", k=100)) == 5

    def test_k_below_one_raises(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        provider = FakeEmbeddingProvider({})
        with pytest.raises(ValueError, match="k must be >= 1"):
            vector_search(db_path, provider, "query", model_id="fake-model", k=0)

    def test_a_blank_query_returns_no_results_without_calling_the_provider(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path)
        provider = FakeEmbeddingProvider({})
        assert vector_search(db_path, provider, "   ", model_id="fake-model") == []
        assert provider.calls == []

    def test_nothing_embedded_yet_returns_no_results_without_calling_the_provider(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path)
        provider = FakeEmbeddingProvider({})
        assert vector_search(db_path, provider, "anything", model_id="never-embedded") == []
        assert provider.calls == []

    def test_raises_up_front_for_a_non_embedding_capable_provider(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path)
        provider = FakeEmbeddingProvider({}, capable=False)
        with pytest.raises(ValueError, match="does not support embeddings"):
            vector_search(db_path, provider, "query", model_id="chat-model")

    def test_citation_fields_are_exact(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        text = "A single short chunk of text."
        outcome = _ingest(db_path, text=text, source_uri="cited.txt", title="Cited Doc")
        provider = FakeEmbeddingProvider({text: [1.0, 0.0], "query": [1.0, 0.0]})
        embed_pending_chunks(db_path, provider, "fake-model")

        [result] = vector_search(db_path, provider, "query", model_id="fake-model")
        assert result["document_id"] == outcome.document_id
        assert result["document_title"] == "Cited Doc"
        assert result["source_uri"] == "cited.txt"
        assert text[result["offset_start"]:result["offset_end"]] == result["text"]

    def test_a_dimension_mismatch_raises_a_clear_error_instead_of_a_numpy_crash(self, tmp_path):
        # Adversarial-review finding: knowledge_store.py's own migration-003
        # docstring says `dim` exists precisely so this is "a cheap integer
        # comparison, not a silent shape error deep in a numpy call" - this
        # proves that promise holds. Simulates the same model_id backing two
        # different vector lengths (e.g. a re-pulled Ollama tag with a
        # different architecture) by embedding under one dim, then directly
        # corrupting one stored row's dim/vector to a different length.
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path, text="Good vector doc.", source_uri="a.txt")
        provider = FakeEmbeddingProvider(
            {"Good vector doc.": [1.0, 0.0, 0.0], "query": [1.0, 0.0, 0.0]}
        )
        embed_pending_chunks(db_path, provider, "fake-model")

        from backend.knowledge_embeddings import _pack_vector
        from backend.knowledge_store import upsert_embeddings, list_embeddings_for_search

        [row] = list_embeddings_for_search(db_path, "fake-model")
        upsert_embeddings(db_path, "fake-model", [(row["chunk_id"], 4, _pack_vector([1.0, 0.0, 0.0, 0.0]))])

        with pytest.raises(ValueError, match="mismatched dimension"):
            vector_search(db_path, provider, "query", model_id="fake-model")
