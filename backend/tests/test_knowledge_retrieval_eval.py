"""The checked-in default must be backed by a deterministic quality gain."""

from __future__ import annotations

from backend.evals.knowledge_retrieval import ranking_improved, run_evaluation, score_at_5
from backend.knowledge_retrieval import DEFAULT_RANKING


def test_rank_metrics_count_unique_gold_sources_and_first_hit():
    hits = [{"source_uri": "wrong.md"}, {"source_uri": "right.md"}, {"source_uri": "right.md"}]
    assert score_at_5(hits, ["right.md"]) == (1.0, 0.5)
    assert score_at_5(hits, ["missing.md"]) == (0.0, 0.0)


def test_retrieval_quality_gate_and_default():
    result = run_evaluation()
    assert result["documents"] >= 10
    assert result["questions"] >= 5
    assert ranking_improved(result), result["metrics"]
    assert DEFAULT_RANKING == "reranked"
    assert result["metrics"]["reranked"]["mrr"] > result["metrics"]["legacy"]["mrr"]
    assert result["metrics"]["reranked"]["recall_at_5"] >= result["metrics"]["legacy"]["recall_at_5"]


def test_gate_rejects_regression_and_tie():
    same = {"metrics": {"legacy": {"recall_at_5": 0.6, "mrr": 0.5},
                        "reranked": {"recall_at_5": 0.6, "mrr": 0.5}}}
    worse = {"metrics": {"legacy": {"recall_at_5": 0.6, "mrr": 0.5},
                         "reranked": {"recall_at_5": 0.8, "mrr": 0.4}}}
    assert not ranking_improved(same)
    assert not ranking_improved(worse)
