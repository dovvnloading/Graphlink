"""Global-search scopes must filter both candidate indexes before top-k."""
from __future__ import annotations

import asyncio
import struct
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from backend import knowledge_embedding_config as config
from backend import knowledge_store as ks
from backend.api import intents_global_search
from backend.events import SessionBus
from backend.knowledge_chunking import chunk_text


@pytest.mark.parametrize("minority_scope", ["graphs", "knowledge"])
@pytest.mark.parametrize("mode", ["keyword", "vector", "fallback"])
def test_global_search_scope_before_candidate_cutoff(tmp_path, monkeypatch, minority_scope, mode):
    path = tmp_path / "knowledge.db"
    collection = ks.get_or_create_workspace_collection(path, 1)
    # 45 stronger hits exceed even the reranker's 40-candidate pool for k=10.
    strong = [f"needle needle needle passage{i}" for i in range(45)]
    weak = "needle " + " ".join(f"background{i}" for i in range(30))

    def ingest(text, index):
        # A graph-looking URI alone must not classify an ordinary document as a graph.
        ks.add_document_with_chunks(
            path, source_uri=f"graph:{1000 + index}", title=f"Document {index}",
            mime="text/plain", text=text, chunks=chunk_text(text, target_tokens=1000),
            collection_id=collection,
        )

    if minority_scope == "graphs":
        for i, text in enumerate(strong):
            ingest(text, i)
        ks.reindex_graph_content(
            path, graph_id=1, workspace_id=1, title="Graph",
            node_chunks=[("weak", weak)],
        )
    else:
        ks.reindex_graph_content(
            path, graph_id=1, workspace_id=1, title="Graph",
            node_chunks=[(f"node-{i}", text) for i, text in enumerate(strong)],
        )
        ingest(weak, 0)

    settings = None
    query = "needle"
    if mode != "keyword":
        rows = ks.search_chunks(path, "needle", k=100)
        ks.upsert_embeddings(path, "test-model", [
            (row["chunk_id"], 2, struct.pack("<2f", *(
                (0.8, 0.6) if row["text"] == weak else (1.0, 0.0)
            )))
            for row in rows
        ])

        class Provider:
            capabilities = SimpleNamespace(embedding=True)

            def embed(self, texts):
                if mode == "fallback":
                    raise RuntimeError("embedding service unavailable")
                return [[1.0, 0.0] for _ in texts]

        @contextmanager
        def provider_for_choice(*_args, **_kwargs):
            yield Provider(), "test-model"

        monkeypatch.setattr(config, "embedding_provider_for_choice", provider_for_choice)
        settings = SimpleNamespace(get_knowledge_embedding_settings=lambda: {"source": "ollama"})
        if mode == "vector":
            query = "semanticquery"  # No lexical matches: exercise the vector-only path.

    monkeypatch.setattr(intents_global_search, "DEFAULT_DB_PATH", path)
    bus = SessionBus("global-search-scope")
    intents_global_search.register_global_search_intents(bus, settings_manager=settings)

    def search(*args):
        return asyncio.run(bus.dispatch_intent("globalSearch", "search", list(args)))["results"]

    def is_graph(row):
        return row["graphId"] is not None and row["sourceNodeId"] is not None

    mixed = search(query, 10)
    assert mixed == search(query, 10, None, "all")
    assert len(mixed) == 10
    assert all(is_graph(row) != (minority_scope == "graphs") for row in mixed)

    [hit] = search(query, 10, None, minority_scope)
    assert hit["text"] == weak
    assert is_graph(hit) == (minority_scope == "graphs")

    majority_scope = "knowledge" if minority_scope == "graphs" else "graphs"
    assert search(query, 10, None, majority_scope) == mixed

    # With a larger mixed limit, both kinds are still eligible.
    all_hits = search(query, 50)
    assert any(is_graph(row) for row in all_hits)
    assert any(not is_graph(row) for row in all_hits)
