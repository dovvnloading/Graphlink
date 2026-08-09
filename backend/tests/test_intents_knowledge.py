"""ADR-017 stage 17.5: the "knowledge" topic's two WS intents
(backend/api/intents_knowledge.py) - knowledge.search (read-only) and
scene/setChatIndexIntoKnowledge (branch-indexing opt-in).

Every test monkeypatches DEFAULT_DB_PATH on all three modules that bind
their OWN copy of the name at import time (backend.knowledge_store,
backend.knowledge_ingest, backend.api.intents_knowledge) to a tmp_path db -
these intents default to the real `~/.graphlink/knowledge/knowledge.db`
when no path is given, and this suite must never read or write real user
data (this codebase's own established test-suite invariant)."""

from __future__ import annotations

import asyncio

import pytest

from backend import knowledge_ingest, knowledge_store
from backend.api import intents_knowledge
from backend.tests.test_canvas import make_bus


@pytest.fixture(autouse=True)
def _isolated_knowledge_db(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge.db"
    monkeypatch.setattr(knowledge_store, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(knowledge_ingest, "DEFAULT_DB_PATH", db_path)
    monkeypatch.setattr(intents_knowledge, "DEFAULT_DB_PATH", db_path)
    return db_path


def _run(coro):
    return asyncio.run(coro)


class TestSearchIntent:
    def test_search_with_nothing_ingested_returns_no_results(self, _isolated_knowledge_db):
        bus, document, recorder = make_bus()
        result = _run(bus.dispatch_intent("knowledge", "search", ["anything", 5]))
        assert result == {"results": []}

    def test_search_finds_a_real_ingested_chunk_with_full_citation_fields(self, _isolated_knowledge_db):
        knowledge_ingest.ingest_text(
            "The quick brown fox jumps over the lazy dog.",
            source_uri="doc.txt", title="Fox Doc", db_path=_isolated_knowledge_db,
        )
        bus, document, recorder = make_bus()
        result = _run(bus.dispatch_intent("knowledge", "search", ["brown fox", 5]))
        assert len(result["results"]) == 1
        row = result["results"][0]
        assert row["documentTitle"] == "Fox Doc"
        assert row["sourceUri"] == "doc.txt"
        assert "fox" in row["text"].lower()
        assert isinstance(row["offsetStart"], int)
        assert isinstance(row["offsetEnd"], int)

    def test_search_is_read_only_and_never_publishes_scene(self, _isolated_knowledge_db):
        bus, document, recorder = make_bus()
        recorder.messages.clear()
        _run(bus.dispatch_intent("knowledge", "search", ["query", 5]))
        assert recorder.topics_seen() == []

    def test_k_is_capped_at_the_modules_maximum(self, _isolated_knowledge_db):
        for i in range(30):
            knowledge_ingest.ingest_text(
                f"Entry {i} about koalas.", source_uri=f"k{i}.txt", title=f"Koala {i}",
                db_path=_isolated_knowledge_db,
            )
        bus, document, recorder = make_bus()
        result = _run(bus.dispatch_intent("knowledge", "search", ["koalas", 9999]))
        assert len(result["results"]) == intents_knowledge._MAX_K


class TestSetChatIndexIntoKnowledgeIntent:
    def test_enabling_indexes_the_branch_and_sets_the_flag(self, _isolated_knowledge_db):
        bus, document, recorder = make_bus()
        node_id = _run(bus.dispatch_intent("scene", "addChatNode", [0, 0, "hello there", True]))

        _run(bus.dispatch_intent("scene", "setChatIndexIntoKnowledge", [node_id, True]))

        assert document.nodes[node_id].state.index_into_knowledge is True
        docs = knowledge_store.list_documents(_isolated_knowledge_db)
        assert len(docs) == 1
        assert docs[0]["source_uri"] == f"branch:{node_id}"

    def test_enabling_indexes_the_full_root_to_here_branch_text(self, _isolated_knowledge_db):
        bus, document, recorder = make_bus()
        first = _run(bus.dispatch_intent("scene", "addChatNode", [0, 0, "first message", True]))
        # addChatNode's own domain method (backend/domain/graph.py) never
        # auto-chains onto the last chat node - parent_id must be passed
        # explicitly for the parent-edge chat_branch_history() walks.
        second = _run(bus.dispatch_intent(
            "scene", "addChatNode", [0, 40, "second message", False, first],
        ))

        _run(bus.dispatch_intent("scene", "setChatIndexIntoKnowledge", [second, True]))

        docs = knowledge_store.list_documents(_isolated_knowledge_db)
        [doc] = docs
        chunks = knowledge_store.list_chunks_for_document(_isolated_knowledge_db, doc["id"])
        full_text = " ".join(c["text"] for c in chunks)
        assert "first message" in full_text
        assert "second message" in full_text

    def test_disabling_clears_the_flag_without_touching_already_indexed_content(self, _isolated_knowledge_db):
        bus, document, recorder = make_bus()
        node_id = _run(bus.dispatch_intent("scene", "addChatNode", [0, 0, "hello there", True]))
        _run(bus.dispatch_intent("scene", "setChatIndexIntoKnowledge", [node_id, True]))

        _run(bus.dispatch_intent("scene", "setChatIndexIntoKnowledge", [node_id, False]))

        assert document.nodes[node_id].state.index_into_knowledge is False
        assert len(knowledge_store.list_documents(_isolated_knowledge_db)) == 1  # untouched

    def test_setting_it_on_a_non_chat_node_raises(self, _isolated_knowledge_db):
        from backend.domain.graph import SceneError

        bus, document, recorder = make_bus()
        node_id = _run(bus.dispatch_intent("scene", "addNode", [0, 0, "plain node"]))
        with pytest.raises(SceneError):
            _run(bus.dispatch_intent("scene", "setChatIndexIntoKnowledge", [node_id, True]))

    def test_toggling_publishes_the_scene_topic(self, _isolated_knowledge_db):
        bus, document, recorder = make_bus()
        node_id = _run(bus.dispatch_intent("scene", "addChatNode", [0, 0, "hi", True]))
        recorder.messages.clear()
        _run(bus.dispatch_intent("scene", "setChatIndexIntoKnowledge", [node_id, True]))
        assert "scene" in recorder.topics_seen()
