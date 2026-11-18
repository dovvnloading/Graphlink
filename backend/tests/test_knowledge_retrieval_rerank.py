"""Local regression tests for hybrid reranking, diversity and citations."""

from __future__ import annotations

import re

import pytest

from backend.knowledge_chunking import TextChunk
from backend.knowledge_retrieval import (
    DEFAULT_RANKING,
    format_untrusted_context,
    hybrid_search,
    reciprocal_rank_fusion,
    remove_near_duplicates,
    rerank_results,
)
from backend.knowledge_store import add_document_with_chunks, provenance_for_hits


def _hit(chunk_id: int, text: str, *, source: str = "test.md", start: int = 0):
    return {
        "chunk_id": chunk_id, "document_id": 1, "ordinal": chunk_id,
        "text": text, "token_count": 30, "offset_start": start,
        "offset_end": start + len(text), "document_title": "Example",
        "source_uri": source, "source_node_id": None,
    }


def test_duplicate_in_one_index_not_counted_twice_and_ties_stable():
    first = [_hit(11, "one"), _hit(11, "one"), _hit(12, "two")]
    second = [_hit(12, "two")]
    fused = reciprocal_rank_fusion([first, second])
    assert [row["chunk_id"] for row in fused] == [12, 11]
    assert fused[1]["rrf_score"] == pytest.approx(1 / 61)


def test_both_indexes_agree_and_trace_which_index_ranked_each_hit():
    lexical = [_hit(1, "alpha"), _hit(2, "beta")]
    vector = [_hit(2, "beta"), _hit(3, "gamma")]
    reranked = rerank_results(lexical, vector, k=5)
    assert [row["chunk_id"] for row in reranked] == [2, 1, 3]
    assert reranked[0]["rank_signals"] == {"fts5": 2, "vector": 1}
    assert reranked[1]["rank_signals"] == {"fts5": 1}
    assert reranked[0]["rrf_score"] > reranked[1]["rrf_score"]


def test_near_duplicate_from_another_source_does_not_take_an_answer_slot():
    template = (
        "Employees should contact the operations incident commander and the security "
        "on call lead to approve temporary access during a severe outage."
    )
    candidate = template.replace("severe", "major")
    relevant = "The backup account has rotating credentials in the sealed vault."
    rows = [
        _hit(1, template, source="mirror-1.md"),
        _hit(2, candidate, source="mirror-2.md"),
        _hit(3, relevant, source="answer.md"),
    ]
    assert [row["chunk_id"] for row in remove_near_duplicates(rows, k=2)] == [1, 3]


def test_different_passages_with_shared_vocab_survive():
    rows = [
        _hit(1, "First lock the door and then notify the administrator of the event."),
        _hit(2, "First notify the administrator and then lock the door for the event."),
    ]
    assert len(remove_near_duplicates(rows, k=5)) == 2


def test_only_one_index_still_has_a_deduplicated_rank_and_no_rrf_score():
    passage = "The disaster recovery backup snapshots remain encrypted at rest."
    rows = rerank_results([_hit(1, passage), _hit(2, passage)], [], k=5)
    assert len(rows) == 1
    assert rows[0]["rank_signals"] == {"fts5": 1}
    assert "rrf_score" not in rows[0]


def test_provenance_section_is_nearest_prior_heading_across_chunk_boundaries(tmp_path):
    db = tmp_path / "knowledge.db"
    full = "# Opening\n\nA starting note.\n\n## Recovery\n\nTo recover, restore a verified snapshot."
    second_start = full.index("To recover")
    chunks = [
        TextChunk(full[:second_start], 0, 20, 0, second_start),
        TextChunk(full[second_start:], 1, 20, second_start, len(full)),
    ]
    outcome = add_document_with_chunks(
        db, source_uri="operations/backup.md", title="Backups",
        mime="text/markdown", text=full, chunks=chunks,
    )
    hit = {**_hit(17, chunks[1].text, source="operations/backup.md", start=second_start),
           "document_id": outcome.document_id}
    provenance = provenance_for_hits(db, [hit])[17]
    assert provenance["section"] == "Recovery"
    assert provenance["source_file"] == "operations/backup.md"
    assert re.fullmatch(r"kb:[0-9a-f]{20}", provenance["citation_id"])


def test_graph_node_sections_are_scoped_to_their_own_node(tmp_path):
    from backend.knowledge_store import get_or_create_workspace_collection, reindex_graph_content

    db = tmp_path / "knowledge.db"
    reindex_graph_content(
        db, graph_id=13, workspace_id=7, title="Incident graph",
        node_chunks=[
            ("node-alpha", "# Alpha runbook\n\nThe marmalade elevator is disabled."),
            ("node-zulu", "# Zulu runbook\n\nThe quartz compass is available."),
        ],
    )
    collection_id = get_or_create_workspace_collection(db, 7)
    alpha = hybrid_search(db, "marmalade elevator", collection_id=collection_id)
    zulu = hybrid_search(db, "quartz compass", collection_id=collection_id)
    assert len(alpha) == len(zulu) == 1
    assert alpha[0]["source_node_id"] == "node-alpha"
    assert alpha[0]["section"] == "Alpha runbook"
    assert zulu[0]["source_node_id"] == "node-zulu"
    assert zulu[0]["section"] == "Zulu runbook"
    assert alpha[0]["citation_id"] != zulu[0]["citation_id"]


def test_stable_citation_survives_row_id_churn_and_changes_for_new_evidence(tmp_path):
    db = tmp_path / "knowledge.db"
    body = "# Policy\n\nArchive audit logs for at least one year."
    chunk = TextChunk(body, 0, 12, 0, len(body))
    a = add_document_with_chunks(db, source_uri="policy.md", title="Policy", mime="text/markdown", text=body, chunks=[chunk])
    first = {**_hit(1, body, source="policy.md"), "document_id": a.document_id}
    metadata1 = provenance_for_hits(db, [first])[1]
    second = {**first, "chunk_id": 991, "document_id": 999}
    # Citation digest is based on source+offset+text, not volatile row ids.
    from backend.knowledge_store import _stable_chunk_citation
    assert _stable_chunk_citation(first) == _stable_chunk_citation(second) == metadata1["citation_id"]
    assert _stable_chunk_citation({**first, "text": body + " Updated."}) != metadata1["citation_id"]
    assert _stable_chunk_citation({**first, "source_uri": "another.md"}) != metadata1["citation_id"]


def test_hybrid_returns_stable_provenance_and_keeps_legacy_mode(tmp_path):
    db = tmp_path / "knowledge.db"
    content = "# Security\n\nMulti factor login uses a backup token to restore identity."
    add_document_with_chunks(
        db, source_uri="security/guide.md", title="Security guide",
        mime="text/markdown", text=content,
        chunks=[TextChunk(content, 0, 15, 0, len(content))],
    )
    new = hybrid_search(db, "backup token", k=5)
    old = hybrid_search(db, "backup token", k=5, ranking="legacy")
    assert DEFAULT_RANKING == "reranked"
    assert len(new) == len(old) == 1
    assert new[0]["source_file"] == "security/guide.md"
    assert new[0]["section"] == "Security"
    assert new[0]["citation_id"] == old[0]["citation_id"]
    assert new[0]["rank_signals"] == {"fts5": 1}
    assert "rrf_score" not in old[0]
    context = format_untrusted_context(new)
    assert "[k1]" in context and new[0]["citation_id"] in context
    assert "untrusted" in context


def test_hybrid_rejects_invalid_ranking_and_k(tmp_path):
    with pytest.raises(ValueError, match="ranking"):
        hybrid_search(tmp_path / "knowledge.db", "anything", ranking="guess")
    with pytest.raises(ValueError, match="k must"):
        hybrid_search(tmp_path / "knowledge.db", "anything", k=0)
