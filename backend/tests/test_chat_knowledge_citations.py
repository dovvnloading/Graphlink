"""End-to-end citation provenance from retrieved passages to saved chat metadata."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import api_provider
from backend.context_engine import ContextAssembler
from backend.context_engine.citations import (
    CITATION_RECORD_NAME, CitedChatReply, apply_reply_citations,
    assemble_chat_context, citation_record_for_reply, inline_citation_ids,
    inspected_reply_citations,
)
from backend.domain.graph import SceneDocument
from backend.knowledge_chunking import chunk_text
from backend.knowledge_retrieval import search_chat_knowledge
from backend.knowledge_store import add_document_with_chunks, get_or_create_workspace_collection
from graphlink_chat_agent import ChatWorker


REAL_ID = "kb:0123456789abcdef0123"
OTHER_ID = "kb:deadbeefdeadbeefdead"
INVENTED_ID = "kb:aaaaaaaaaaaaaaaaaaaa"


def _hit(citation_id=REAL_ID, text="Restore the latest verified snapshot."):
    return {
        "citation_id": citation_id,
        "document_id": 14,
        "chunk_id": 29,
        "document_title": "Restore handbook",
        "source_uri": "https://example.com/restore.md",
        "source_file": "https://example.com/restore.md",
        "section": "Recovery",
        "text": text,
        "offset_start": 45,
        "offset_end": 45 + len(text),
        "token_count": 30,
    }


def _plan(hits, window=12000):
    return assemble_chat_context(
        ContextAssembler(), "Be helpful.",
        [{"role": "user", "content": "How do I restore the database?"}],
        knowledge_hits=hits, context_window=window,
    )


def test_context_budget_proves_exact_included_passages_and_untrusted_wrapper():
    hits = [_hit(), _hit(OTHER_ID, "Another verified runbook excerpt")]
    plan, included = _plan(hits)
    assert [hit["citation_id"] for hit in included] == [REAL_ID, OTHER_ID]
    knowledge = [s for s in plan.inspect()["sources"] if s["kind"] == "knowledge_hit"]
    assert [s["citationId"] for s in knowledge] == [REAL_ID, OTHER_ID]
    assert all(s["included"] and s["trust"] == "retrieved" for s in knowledge)
    assert all(s["section"] == "Recovery" for s in knowledge)
    assert plan.messages[-1] == {"role": "user", "content": "How do I restore the database?"}
    assert all(s["role"] == "user" for s in plan.messages[1:])
    # The lower-trust text is an escaped JSON datum, not a system instruction.
    assert any('"permissions": "none"' in msg["content"] for msg in plan.messages)
    assert "Citation ID: [" + REAL_ID + "]" in json.dumps(plan.messages)
    assert "cite their" in plan.messages[0]["content"]


def test_non_selected_and_duplicate_hits_cannot_become_verified_citations():
    big = _hit(OTHER_ID, "large " * 10000)
    plan, included = _plan([_hit(), big, _hit()])
    assert [hit["citation_id"] for hit in included] == [REAL_ID]
    oversized = [s for s in plan.sources if s["sourceId"] == "knowledge:" + OTHER_ID]
    assert len(oversized) == 1 and oversized[0]["included"] is False
    assert oversized[0]["reason"] == "exceeds prompt budget"


def test_when_sources_absent_or_none_fit_prompt_is_exactly_legacy_shape():
    original = [
        {"role": "user", "content": "ask"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "next"},
    ]
    assembler = ContextAssembler()
    baseline = assembler.assemble(assembler.chat_sources("persona", original), context_window=12000)
    without, no_sources = assemble_chat_context(
        assembler, "persona", original, context_window=12000,
    )
    oversized, excluded = assemble_chat_context(
        assembler, "persona", original, context_window=12000,
        knowledge_hits=[_hit(text="enormous " * 25000)],
    )
    assert no_sources == excluded == ()
    assert without.messages == oversized.messages == baseline.messages


@pytest.mark.parametrize("stream", [False, True])
def test_chat_worker_only_sends_included_sources_and_preserves_plain_answer(monkeypatch, stream):
    sent = {}
    def provider_response(**kwargs):
        sent["messages"] = kwargs["messages"]
        if "on_chunk" in kwargs:
            kwargs["on_chunk"]("Answer ", False)
        return {"message": {"content": "Answer [" + REAL_ID + "]"}}
    monkeypatch.setattr(api_provider, "chat" if not stream else "chat_stream", provider_response)
    history = [{"role": "user", "content": "restore?", "_knowledge_hits": [_hit()]}]
    result = ChatWorker("").run(
        history, None, resolved_system_prompt="persona",
        runtime=SimpleNamespace(context_window=lambda _: 12000),
        **({"on_chunk": lambda *_args: None} if stream else {}),
    )
    assert isinstance(result, CitedChatReply)
    assert isinstance(result, str)
    assert str(result) == "Answer [" + REAL_ID + "]"
    assert result.knowledge_sources[0]["citation_id"] == REAL_ID
    assert all("_knowledge_hits" not in str(row) for row in sent["messages"])
    assert sent["messages"][-1] == {"role": "user", "content": "restore?"}
    assert any(REAL_ID in str(row) for row in sent["messages"])
    assert history[-1]["_knowledge_hits"] == [_hit()]  # no mutation


def test_unverified_ids_do_not_inherit_other_source_metadata():
    answer = CitedChatReply(
        "Answer [" + REAL_ID + "], forged [" + INVENTED_ID + "] and again [" + REAL_ID + "]",
        [_hit(), _hit(OTHER_ID)],
    )
    record = citation_record_for_reply(answer)
    assert record and record["name"] == CITATION_RECORD_NAME
    payload = json.loads(record["result"])
    assert [(s["citationId"], s["cited"]) for s in payload["sources"]] == [
        (REAL_ID, True), (OTHER_ID, False),
    ]
    assert payload["unverifiedIds"] == [INVENTED_ID]
    assert payload["sources"][0]["section"] == "Recovery"
    assert payload["sources"][0]["documentId"] == 14
    assert payload["sources"][0]["chunkId"] == 29
    assert not any(s["citationId"] == INVENTED_ID for s in payload["sources"])
    assert inline_citation_ids(str(answer)) == [REAL_ID, INVENTED_ID]


def test_record_replaced_on_regenerate_and_inspector_can_audit_cited_passages():
    doc = SceneDocument()
    user = doc.add_chat_node(0, 0, "Restore?", True)
    assistant = doc.add_chat_node(0, 160, "placeholder", False, parent_id=user.id)
    apply_reply_citations(assistant, CitedChatReply("Yes [" + REAL_ID + "]", [_hit()]))
    audited = inspected_reply_citations(doc, [f"node:{user.id}", f"node:{assistant.id}"])
    assert audited["replyNodeId"] == assistant.id
    assert audited["sources"][0]["citationId"] == REAL_ID
    assert audited["sources"][0]["cited"] is True
    assert audited["sources"][0]["section"] == "Recovery"

    apply_reply_citations(assistant, CitedChatReply("Not referenced", [_hit(OTHER_ID)]))
    records = [row for row in assistant.state.tool_invocations
               if row["name"] == CITATION_RECORD_NAME]
    assert len(records) == 1
    assert json.loads(records[0]["result"])["sources"][0]["cited"] is False
    apply_reply_citations(assistant, "Plain regenerate without knowledge")
    assert assistant.state.tool_invocations == []


def test_partial_regeneration_clears_stale_citations_and_is_undoable():
    from backend.api.intents_chat import _land_partial_regenerate

    document = SceneDocument()
    user = document.add_chat_node(0, 0, "Restore?", True)
    previous = "Verified previous answer [" + REAL_ID + "]"
    assistant = document.add_chat_node(0, 160, previous, False, parent_id=user.id)
    actual_tool = {
        "id": "call_1", "name": "search_docs", "arguments": {"query": "restore"},
        "result": "ok", "is_error": False,
    }
    assistant.state.tool_invocations.append(actual_tool)
    apply_reply_citations(assistant, CitedChatReply(previous, [_hit()]))
    before = list(assistant.state.tool_invocations)
    assert len(before) == 2

    # The partial text can mention the same ID, but its new run has no
    # trusted included-source sidecar. It must not reuse the old ledger.
    partial = "Interrupted new answer [" + REAL_ID + "]"
    _land_partial_regenerate(document, assistant, partial)
    interrupted = document.nodes[assistant.id]
    assert interrupted.content == partial
    assert interrupted.state.response_incomplete is True
    assert interrupted.state.tool_invocations == [actual_tool]
    assert inspected_reply_citations(
        document, [f"node:{user.id}", f"node:{assistant.id}"],
    ) == {
        "replyNodeId": assistant.id,
        "sources": [],
        "unverifiedIds": [REAL_ID],
    }

    # Content and provenance change in one command: undo restores both,
    # and redo does not resurrect the previous verified citation.
    document.undo()
    restored = document.nodes[assistant.id]
    assert restored.content == previous
    assert restored.state.response_incomplete is False
    assert restored.state.tool_invocations == before

    document.redo()
    redone = document.nodes[assistant.id]
    assert redone.content == partial
    assert redone.state.response_incomplete is True
    assert redone.state.tool_invocations == [actual_tool]


def test_missing_database_and_cross_workspace_retrieval_isolation(tmp_path):
    db = tmp_path / "knowledge.db"
    assert search_chat_knowledge("recovery", 41, db_path=db) == []
    assert not db.exists()
    collection = get_or_create_workspace_collection(db, 41)
    text = "# Recovery\n\nUse the cobalt cascade switch for failover."
    add_document_with_chunks(
        db, source_uri="ops/recovery.md", title="Operations recovery",
        mime="text/markdown", text=text,
        chunks=chunk_text(text), collection_id=collection,
    )
    hits = search_chat_knowledge("cobalt cascade", 41, db_path=db)
    assert len(hits) == 1
    assert hits[0]["source_file"] == "ops/recovery.md"
    assert hits[0]["section"] == "Recovery"
    assert hits[0]["citation_id"].startswith("kb:")
    assert len(search_chat_knowledge(
        "How do I use the cobalt cascade switch for failover?",
        41, db_path=db,
    )) == 1
    assert search_chat_knowledge("cobalt cascade", 42, db_path=db) == []
    assert search_chat_knowledge("cobalt cascade", None, db_path=db) == []


def test_async_intent_snapshot_is_ephemeral_and_failed_search_is_nonfatal(monkeypatch):
    from backend.api import intents_chat

    history = [{"role": "user", "content": "Restore?"}]
    monkeypatch.setattr(intents_chat, "search_chat_knowledge", lambda *_args: [_hit()])
    enriched = asyncio.run(intents_chat._history_with_chat_knowledge(history, "Restore?", 2))
    assert history == [{"role": "user", "content": "Restore?"}]
    assert enriched[-1]["_knowledge_hits"] == [_hit()]
    assert intents_chat._latest_user_query([
        {"role": "user", "content": "hello\n\n--- Attached: README ---\ntext"}
    ]) == "hello"

    def failed(*_args):
        raise RuntimeError("offline")
    monkeypatch.setattr(intents_chat, "search_chat_knowledge", failed)
    assert asyncio.run(
        intents_chat._history_with_chat_knowledge(history, "Restore?", 2)
    ) is history
