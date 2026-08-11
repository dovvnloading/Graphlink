"""ADR-017 stage 17.4: reciprocal rank fusion, hybrid search, budget-aware
selection, and untrusted-context formatting.

Exit criterion this file proves (ADR-017 doc, stage 17.4 row): "Hybrid
beats either index alone on a fixture set; injected context is labeled
untrusted."
"""

from __future__ import annotations


from backend.knowledge_chunking import chunk_text
from backend.knowledge_embeddings import embed_pending_chunks
from backend.knowledge_retrieval import (
    format_untrusted_context,
    hybrid_search,
    reciprocal_rank_fusion,
    select_within_budget,
)
from backend.knowledge_store import add_document_with_chunks
from backend.providers.base import ProviderCapabilities


class FakeEmbeddingProvider:
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


def _result(chunk_id, **overrides):
    base = {
        "chunk_id": chunk_id, "document_id": 1, "ordinal": 0, "text": "t",
        "token_count": 5, "offset_start": 0, "offset_end": 1,
        "document_title": "Doc", "source_uri": "doc.txt",
    }
    base.update(overrides)
    return base


# -- reciprocal_rank_fusion ----------------------------------------------------


class TestReciprocalRankFusion:
    def test_a_result_appearing_in_both_lists_outranks_one_appearing_in_only_one(self):
        # chunk 1: rank 1 in list A, rank 1 in list B (both agree) - must
        # beat chunk 2, which is rank 1 in ONLY list A.
        list_a = [_result(1), _result(2)]
        list_b = [_result(1), _result(3)]

        fused = reciprocal_rank_fusion([list_a, list_b])

        assert [r["chunk_id"] for r in fused] == [1, 2, 3]

    def test_single_list_input_preserves_its_own_order(self):
        results = [_result(1), _result(2), _result(3)]
        fused = reciprocal_rank_fusion([results])
        assert [r["chunk_id"] for r in fused] == [1, 2, 3]

    def test_empty_lists_produce_no_results(self):
        assert reciprocal_rank_fusion([[], []]) == []

    def test_every_fused_result_carries_an_rrf_score_and_original_fields(self):
        fused = reciprocal_rank_fusion([[_result(1, text="hello")]])
        assert fused[0]["text"] == "hello"
        assert isinstance(fused[0]["rrf_score"], float)
        assert fused[0]["rrf_score"] > 0


# -- hybrid_search: the fixture-set exit criterion ----------------------------


class TestHybridSearchBeatsEitherIndexAlone:
    def test_hybrid_finds_both_an_exact_identifier_and_a_paraphrase(self, tmp_path):
        # Fixture set: one document only lexical search will find (an
        # exact, unusual identifier with no semantic paraphrase available),
        # one document only vector search will find (a paraphrase query
        # sharing NO words with its target chunk), scored via a
        # deterministic FakeEmbeddingProvider.
        db_path = tmp_path / "knowledge.db"
        exact = _ingest(
            db_path, text="The error code is XJ7Q92-FAULT.", source_uri="exact.txt",
        )
        paraphrase = _ingest(
            db_path, text="The feline sat quietly upon the mat.", source_uri="paraphrase.txt",
        )
        # A third, irrelevant document - present so "hybrid returns
        # everything" can't trivially pass either sub-test.
        _ingest(db_path, text="Unrelated content about tax filings.", source_uri="noise.txt")

        vectors = {
            "The error code is XJ7Q92-FAULT.": [0.0, 1.0],
            "The feline sat quietly upon the mat.": [1.0, 0.0],
            "Unrelated content about tax filings.": [0.0, 0.0],
            "XJ7Q92-FAULT": [0.01, 0.02],  # embeds nowhere near either real vector
            "a cat resting on a rug": [0.99, 0.01],  # close to the "feline" vector
        }
        provider = FakeEmbeddingProvider(vectors)
        embed_pending_chunks(db_path, provider, "fake-model")

        lexical_only_exact = hybrid_search(db_path, "XJ7Q92-FAULT", k=10)
        lexical_only_paraphrase = hybrid_search(db_path, "a cat resting on a rug", k=10)
        # Lexical-only finds the exact identifier...
        assert lexical_only_exact and lexical_only_exact[0]["document_id"] == exact.document_id
        # ...but NOT the paraphrase (no shared words at all).
        assert not any(r["document_id"] == paraphrase.document_id for r in lexical_only_paraphrase)

        hybrid_exact = hybrid_search(
            db_path, "XJ7Q92-FAULT", embedding_provider=provider, embedding_model_id="fake-model", k=10,
        )
        hybrid_paraphrase = hybrid_search(
            db_path, "a cat resting on a rug",
            embedding_provider=provider, embedding_model_id="fake-model", k=10,
        )
        # Hybrid keeps the lexical win...
        assert hybrid_exact[0]["document_id"] == exact.document_id
        # ...AND now ALSO finds the paraphrase, which lexical-only missed -
        # the concrete "beats either index alone" proof.
        assert hybrid_paraphrase[0]["document_id"] == paraphrase.document_id

    def test_omitting_the_embedding_provider_degrades_to_lexical_only(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        outcome = _ingest(db_path, text="Findable by exact words only.")
        results = hybrid_search(db_path, "exact words")
        assert results and results[0]["document_id"] == outcome.document_id
        # No rrf_score - proves the FUSION path never ran, not just that
        # it happened to return the right answer.
        assert "rrf_score" not in results[0]

    def test_a_non_embedding_capable_provider_also_degrades_to_lexical_only(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path, text="Findable by exact words only.")
        provider = FakeEmbeddingProvider({}, capable=False)
        results = hybrid_search(
            db_path, "exact words", embedding_provider=provider, embedding_model_id="chat-model",
        )
        assert "rrf_score" not in results[0]
        assert provider.calls == []  # never even tried to embed the query

    def test_k_bounds_the_fused_result_count(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        vectors = {"query": [1.0, 0.0]}
        for i in range(5):
            text = f"Walrus fact number {i}."
            _ingest(db_path, text=text, source_uri=f"w{i}.txt")
            vectors[text] = [1.0, float(i)]
        provider = FakeEmbeddingProvider(vectors)
        embed_pending_chunks(db_path, provider, "fake-model")

        results = hybrid_search(
            db_path, "query", embedding_provider=provider, embedding_model_id="fake-model", k=2,
        )
        assert len(results) == 2


# -- select_within_budget ------------------------------------------------------


class TestSelectWithinBudget:
    def test_keeps_results_that_fit_and_stops_at_the_first_that_would_overflow(self):
        results = [_result(1, token_count=10), _result(2, token_count=10), _result(3, token_count=10)]
        selected = select_within_budget(results, token_budget=25)
        assert [r["chunk_id"] for r in selected] == [1, 2]

    def test_a_budget_smaller_than_the_best_single_result_returns_nothing(self):
        results = [_result(1, token_count=100)]
        assert select_within_budget(results, token_budget=10) == []

    def test_a_budget_covering_everything_keeps_the_full_ranking_in_order(self):
        results = [_result(1, token_count=5), _result(2, token_count=5)]
        selected = select_within_budget(results, token_budget=1000)
        assert [r["chunk_id"] for r in selected] == [1, 2]

    def test_a_later_smaller_result_is_never_pulled_ahead_of_a_skipped_larger_one(self):
        # result 2 (token_count=10) does NOT fit after result 1 (20) under
        # a 25 budget - the budget must STOP there, not skip past 2 to
        # grab result 3 (token_count=5) just because it would fit.
        results = [
            _result(1, token_count=20),
            _result(2, token_count=10),
            _result(3, token_count=5),
        ]
        selected = select_within_budget(results, token_budget=25)
        assert [r["chunk_id"] for r in selected] == [1]

    def test_empty_results_returns_empty(self):
        assert select_within_budget([], token_budget=1000) == []


# -- format_untrusted_context ---------------------------------------------------


class TestFormatUntrustedContext:
    def test_empty_results_produce_an_empty_string(self):
        assert format_untrusted_context([]) == ""

    def test_labels_the_block_as_untrusted_and_warns_against_following_instructions(self):
        context = format_untrusted_context([_result(1, text="some content")])
        assert "untrusted" in context.lower()
        assert "do not follow" in context.lower()

    def test_each_result_carries_a_numbered_citation_marker_and_its_source(self):
        results = [
            _result(1, text="first", document_title="Doc A", source_uri="a.txt"),
            _result(2, text="second", document_title="Doc B", source_uri="b.txt"),
        ]
        context = format_untrusted_context(results)
        assert "[k1]" in context
        assert "[k2]" in context
        assert "Doc A" in context and "a.txt" in context
        assert "Doc B" in context and "b.txt" in context
        assert "first" in context
        assert "second" in context

    def test_citation_markers_are_distinct_from_web_researchs_own_s_prefix(self):
        # Web Research's own SUMMARY_SYSTEM prompt asks the model to cite
        # with [s1]-style markers - knowledge-base citations must be
        # visually distinguishable in a turn that could carry both kinds.
        context = format_untrusted_context([_result(1)])
        assert "[k1]" in context
        assert "[s1]" not in context
