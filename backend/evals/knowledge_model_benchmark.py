"""Opt-in retrieval-quality benchmark using the configured knowledge embedding model.

Run explicitly: python -m backend.evals.knowledge_model_benchmark --run
This is a report, not a quality gate. It is never run by the CI eval harness.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.evals.knowledge_retrieval import FIXTURE_PATH, score_at_5
from backend.knowledge_chunking import chunk_text
from backend.knowledge_embedding_config import embedding_provider_for_choice
from backend.knowledge_embeddings import embed_pending_chunks
from backend.knowledge_retrieval import DEFAULT_RANKING, hybrid_search
from backend.knowledge_store import add_document_with_chunks

TOP_K = 5
MODES = ("keyword_only", "hybrid")
METRICS = ("recall_at_5", "mrr")


def _metrics(details: list[dict]) -> dict[str, dict[str, float]]:
    """Macro-average per-question recall@5 and first-relevant-hit MRR@5."""
    if not details:
        raise ValueError("The retrieval benchmark requires at least one question.")
    return {
        mode: {
            metric: sum(row["scores"][mode][metric] for row in details) / len(details)
            for metric in METRICS
        }
        for mode in MODES
    }


def run_benchmark(*, settings_manager=None) -> dict:
    """Compare the same fixed corpus/queries, isolating all writes in a temp DB.

    Uses the normal configured embedding provider with NO keyword fallback:
    model errors abort the report instead of mislabeling lexical results as
    hybrid. The local corpus is destroyed when the run completes.
    """
    if "CI" in os.environ or "GITHUB_ACTIONS" in os.environ:
        raise RuntimeError("Live retrieval benchmarks are disabled in CI.")

    if settings_manager is None:
        from graphlink_settings_store import SettingsManager

        settings_manager = SettingsManager()
    choice = settings_manager.get_knowledge_embedding_settings()
    if choice.get("source") == "off":
        raise ValueError("Configure an embedding model under Settings > Knowledge before running the benchmark.")

    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    documents = fixture["documents"]
    questions = fixture["questions"]
    if not documents or not questions or any(not case["relevant_sources"] for case in questions):
        raise ValueError("The fixed retrieval fixture needs documents and questions with relevant sources.")

    with TemporaryDirectory(prefix="graphlink-knowledge-model-benchmark-") as temporary:
        db_path = Path(temporary) / "knowledge.db"
        for document in documents:
            add_document_with_chunks(
                db_path,
                source_uri=document["source_uri"],
                title=document["title"],
                mime="text/markdown",
                text=document["text"],
                chunks=chunk_text(document["text"], target_tokens=1500),
            )

        # This path never receives an embedding provider, even if vectors
        # exist in the database from the subsequent hybrid pass.
        keyword_hits = [hybrid_search(db_path, case["question"], k=TOP_K) for case in questions]
        with embedding_provider_for_choice(settings_manager) as (provider, model_id):
            if provider is None or model_id is None:
                raise ValueError("The configured knowledge embedding model is disabled.")
            # A brand-new scratch DB must get vectors; do not claim to have
            # benchmarked hybrid retrieval if no passage could be embedded.
            if embed_pending_chunks(db_path, provider, model_id) == 0:
                raise RuntimeError("No fixture passages were embedded; cannot evaluate hybrid retrieval.")
            hybrid_hits = [
                hybrid_search(
                    db_path, case["question"], k=TOP_K,
                    embedding_provider=provider, embedding_model_id=model_id,
                )
                for case in questions
            ]

        details = []
        for index, case in enumerate(questions):
            scores = {}
            sources = {}
            for mode, hits in (("keyword_only", keyword_hits[index]), ("hybrid", hybrid_hits[index])):
                recall, mrr = score_at_5(hits, case["relevant_sources"])
                scores[mode] = {"recall_at_5": recall, "mrr": mrr}
                sources[mode] = [hit["source_uri"] for hit in hits]
            details.append({"question": case["question"], "scores": scores, "sources": sources})

    return {
        "corpus": "backend/evals/fixtures/knowledge_retrieval.json",
        "documents": len(documents),
        "questions": len(questions),
        "k": TOP_K,
        "ranking": DEFAULT_RANKING,
        "embedding": {
            "source": choice["source"],
            "model": choice["model"],
            "cloud_provider": choice.get("cloud_provider") if choice["source"] == "cloud" else None,
        },
        "metrics": _metrics(details),
        "details": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Opt in to embedding the fixture with the configured model")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("This live benchmark is opt-in. Pass --run to use the configured embedding model.")
    if "CI" in os.environ or "GITHUB_ACTIONS" in os.environ:
        parser.error("Live retrieval benchmarks are disabled in CI.")
    try:
        report = run_benchmark()
    except Exception as exc:
        parser.exit(2, f"Retrieval benchmark failed: {exc}\n")
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    # This is deliberately NOT a regression gate: a worse hybrid score is
    # still a successful measurement, and never flips the process exit code.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
