"""Reproducible, fully-offline comparison of legacy versus reranked retrieval.

Run: python -m backend.evals.knowledge_retrieval [--assert-improved]
The embedding provider below is deterministic and in-process: no download,
model server, API key, cloud request, or learned evaluation labels are used.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from backend.knowledge_chunking import chunk_text
from backend.knowledge_embeddings import embed_pending_chunks
from backend.knowledge_retrieval import hybrid_search
from backend.knowledge_store import add_document_with_chunks

if TYPE_CHECKING:
    from backend.providers.base import Provider

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "knowledge_retrieval.json"

# A small, transparent dictionary for reproducible synonym-aware vectors.
# It is shared by ALL documents and questions, without any document- or
# query-ID lookup. This does not measure a production embedding model; it
# measures ranking behavior when lexical and semantic lists disagree.
_CONCEPT_GROUPS = (
    ("identity", "authenticator", "factor", "mfa", "codes", "credentials", "tokens", "login", "recovery"),
    ("billing", "database", "storage", "snapshot", "ledger", "restore", "corruption", "recover", "reconciliation"),
    ("emergency", "production", "access", "incident", "privileges", "approvals", "commander", "security", "elevation"),
    ("personal", "data", "deletion", "purge", "erasure", "retention", "customer", "identifiers", "archives"),
    ("rollback", "release", "deployment", "reversal", "build", "canary", "artifact", "migration", "faulty"),
    ("contractors", "time", "paid", "off", "absence", "leave", "vendor", "agency", "coordinator"),
)
_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)


class OfflineConceptEmbeddings:
    """A predictable local semantic approximation, not a benchmark for ML."""

    capabilities = SimpleNamespace(embedding=True)

    def embed(self, texts: list[str]) -> list[list[float]]:
        embeddings = []
        for text in texts:
            counts = Counter(_WORD_RE.findall(text.casefold()))
            vector = [sum(counts[token] for token in concept) for concept in _CONCEPT_GROUPS]
            norm = math.sqrt(sum(float(x * x) for x in vector))
            embeddings.append([value / norm for value in vector] if norm else [0.0] * len(vector))
        return embeddings


def score_at_5(results: list[dict], relevant_sources: list[str]) -> tuple[float, float]:
    """Recall@5 and reciprocal rank of the first relevant source in top 5."""
    relevant = set(relevant_sources)
    top = results[:5]
    retrieved = {row["source_uri"] for row in top}
    recall = len(relevant & retrieved) / len(relevant)
    first_rank = next((i for i, row in enumerate(top, 1) if row["source_uri"] in relevant), None)
    return recall, 1.0 / first_rank if first_rank else 0.0


def run_evaluation() -> dict:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    provider = OfflineConceptEmbeddings()
    with TemporaryDirectory(prefix="graphlink-retrieval-eval-") as tmp:
        db_path = Path(tmp) / "knowledge.db"
        for source in fixture["documents"]:
            add_document_with_chunks(
                db_path, source_uri=source["source_uri"], title=source["title"],
                mime="text/markdown", text=source["text"],
                chunks=chunk_text(source["text"], target_tokens=1500),
            )
        # This deterministic fixture implements only the embed half of Provider.
        embed_pending_chunks(db_path, cast("Provider", provider), "offline-concepts-v1")
        per_question = []
        for case in fixture["questions"]:
            scores = {}
            top_sources = {}
            for ranking in ("legacy", "reranked"):
                rows = hybrid_search(
                    db_path, case["question"], k=5, ranking=ranking,
                    embedding_provider=provider, embedding_model_id="offline-concepts-v1",
                )
                recall, mrr = score_at_5(rows, case["relevant_sources"])
                scores[ranking] = {"recall_at_5": recall, "mrr": mrr}
                top_sources[ranking] = [row["source_uri"] for row in rows]
            per_question.append({"question": case["question"], "scores": scores, "sources": top_sources})
        totals = {
            ranking: {
                metric: sum(case["scores"][ranking][metric] for case in per_question) / len(per_question)
                for metric in ("recall_at_5", "mrr")
            }
            for ranking in ("legacy", "reranked")
        }
        return {"questions": len(per_question), "documents": len(fixture["documents"]),
                "metrics": totals, "details": per_question}


def ranking_improved(report: dict) -> bool:
    """Gate: no regression in either metric and a strict gain in at least one."""
    old = report["metrics"]["legacy"]
    new = report["metrics"]["reranked"]
    return (all(new[metric] >= old[metric] for metric in ("recall_at_5", "mrr"))
            and any(new[metric] > old[metric] for metric in ("recall_at_5", "mrr")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assert-improved", action="store_true")
    args = parser.parse_args()
    report = run_evaluation()
    sys.stdout.write(json.dumps({"documents": report["documents"], "questions": report["questions"],
                                 "metrics": report["metrics"], "default_gate_passed": ranking_improved(report)},
                                indent=2, sort_keys=True) + "\n")
    if args.assert_improved and not ranking_improved(report):
        parser.exit(1, "Reranked retrieval failed the offline quality gate.\n")


if __name__ == "__main__":
    main()
