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


# -- ADR-020 stage 20.3: workspace-scoped knowledge corpus -------------------
#
# The exit criterion's own "different corpora" half, proven through the REAL
# search()/setChatIndexIntoKnowledge intent call paths (backend/api/
# intents_knowledge.py) - never by calling backend.knowledge_store's
# get_or_create_workspace_collection or hybrid_search directly - so this
# actually pins the WIRING (document.current_workspace_id -> collection
# resolution -> collection_id-scoped store call), not just the underlying
# pure functions those intents happen to call.


class TestWorkspaceScopedKnowledgeCorpus:
    def test_two_separate_sessions_in_two_workspaces_never_see_each_others_content(self, _isolated_knowledge_db):
        # Two INDEPENDENT sessions/documents (not one document whose
        # workspace id is merely mutated mid-test) - the closest analog to
        # the real world, where two different browser tabs/windows each
        # have their own chats open in different workspaces at once.
        bus1, document1, _ = make_bus()
        document1.current_workspace_id = 10
        node1 = _run(bus1.dispatch_intent("scene", "addChatNode", [0, 0, "alpha team roadmap notes", True]))
        _run(bus1.dispatch_intent("scene", "setChatIndexIntoKnowledge", [node1, True]))

        bus2, document2, _ = make_bus()
        document2.current_workspace_id = 20
        node2 = _run(bus2.dispatch_intent("scene", "addChatNode", [0, 0, "beta team roadmap notes", True]))
        _run(bus2.dispatch_intent("scene", "setChatIndexIntoKnowledge", [node2, True]))

        # Each session's own search() call (real intent dispatch, not a
        # direct hybrid_search() call) sees ONLY its own workspace's content.
        results1 = _run(bus1.dispatch_intent("knowledge", "search", ["roadmap notes", 10]))
        assert len(results1["results"]) == 1
        assert "alpha" in results1["results"][0]["text"]

        results2 = _run(bus2.dispatch_intent("knowledge", "search", ["roadmap notes", 10]))
        assert len(results2["results"]) == 1
        assert "beta" in results2["results"][0]["text"]

        # Behind the scenes, two genuinely distinct collections.
        collection1 = knowledge_store.get_or_create_workspace_collection(_isolated_knowledge_db, 10)
        collection2 = knowledge_store.get_or_create_workspace_collection(_isolated_knowledge_db, 20)
        assert collection1 != collection2
        docs1 = knowledge_store.list_documents(_isolated_knowledge_db, collection_id=collection1)
        docs2 = knowledge_store.list_documents(_isolated_knowledge_db, collection_id=collection2)
        assert len(docs1) == 1 and len(docs2) == 1
        assert docs1[0]["id"] != docs2[0]["id"]

    def test_switching_the_same_sessions_workspace_switches_what_search_can_see(self, _isolated_knowledge_db):
        bus, document, _ = make_bus()

        document.current_workspace_id = 1
        node_ws1 = _run(bus.dispatch_intent("scene", "addChatNode", [0, 0, "workspace one exclusive content", True]))
        _run(bus.dispatch_intent("scene", "setChatIndexIntoKnowledge", [node_ws1, True]))

        # Still in workspace 1: finds it.
        found_in_ws1 = _run(bus.dispatch_intent("knowledge", "search", ["exclusive content", 5]))
        assert len(found_in_ws1["results"]) == 1

        # Mirrors what loadChat's own current_workspace_id fix (backend/
        # chat_library.py) does when a session switches to a different
        # graph/workspace - search() must immediately stop seeing workspace
        # 1's content.
        document.current_workspace_id = 2
        found_in_ws2 = _run(bus.dispatch_intent("knowledge", "search", ["exclusive content", 5]))
        assert found_in_ws2["results"] == []

    def test_a_session_with_no_workspace_context_falls_back_to_the_pre_20_3_global_pool(self, _isolated_knowledge_db):
        # document.current_workspace_id is None (the default - a session
        # that never loaded/created a chat with a real workspace) - must
        # resolve to collection_id=0, the pre-20.3 unscoped pool, not raise
        # or silently pick some arbitrary workspace.
        bus, document, _ = make_bus()
        assert document.current_workspace_id is None

        node_id = _run(bus.dispatch_intent("scene", "addChatNode", [0, 0, "unscoped content", True]))
        _run(bus.dispatch_intent("scene", "setChatIndexIntoKnowledge", [node_id, True]))

        docs = knowledge_store.list_documents(_isolated_knowledge_db, collection_id=0)
        assert len(docs) == 1

        results = _run(bus.dispatch_intent("knowledge", "search", ["unscoped content", 5]))
        assert len(results["results"]) == 1
