"""C3 read-only Context Inspector preview of the next chat send."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.context_engine.inspector import inspect_next_chat_send
from backend.domain.graph import SceneDocument
from backend.domain.model import SceneError


class _Dispatcher:
    def __init__(self, *, provider="ollama", window=4096, override=None):
        self._provider_runtime = SimpleNamespace(context_window=lambda _task: window)
        self.route_label = provider
        self.override = override
        self.model_resolutions = 0

    def _resolve_branch_system_prompt(self, document, node_id):
        return self.override

    def persona(self):
        return "Be helpful"

    def _resolve_model_ref_for_dispatch(self, document, node_id, bus=None):
        self.model_resolutions += 1
        return None

    def active_provider_model(self, ref=None):
        return self.route_label, "test-model"


def _document():
    document = SceneDocument()
    root = document.add_chat_node(0, 0, "root question", True)
    previous = document.add_chat_node(0, 200, "previous answer", False, parent_id=root.id)
    return document, root.id, previous.id


def test_inspection_is_read_only_and_reports_exact_branch_origins(monkeypatch):
    import api_provider

    def should_not_call_llm(*args, **kwargs):
        raise AssertionError("inspector called the provider")
    monkeypatch.setattr(api_provider, "chat", should_not_call_llm)
    monkeypatch.setattr(api_provider, "chat_stream", should_not_call_llm)

    document, root_id, last_id = _document()
    before = deepcopy(document.nodes), deepcopy(document.edges), document.last_chat_node_id
    dispatcher = _Dispatcher()
    result = inspect_next_chat_send(document, last_id, dispatcher, draft_text="NEXT MESSAGE")

    assert before == (document.nodes, document.edges, document.last_chat_node_id)
    assert result["nodeId"] == last_id
    assert result["provider"] == "ollama"
    assert result["leavesDevice"] is False
    assert result["draftIncluded"] is True
    assert result["contextWindow"] == 4096
    assert result["promptTokens"] <= result["promptBudget"]
    assert [s["origin"] for s in result["sources"]] == [
        "system:settings",
        f"node:{root_id}", f"node:{last_id}", f"draft:after:{last_id}",
    ]
    assert [s["kind"] for s in result["sources"]] == [
        "instructions", "branch_ancestor", "branch_ancestor", "current_message",
    ]
    assert all(s["included"] for s in result["sources"])
    assert all(not s["leavesDevice"] for s in result["sources"])


def test_inspector_reports_excluded_sources_with_reason_and_cloud_egress():
    document, root_id, last_id = _document()
    document.nodes[root_id].content = "old " + "x" * 9000
    dispatcher = _Dispatcher(provider="OpenAI", window=600)
    result = inspect_next_chat_send(document, last_id, dispatcher, draft_text="short draft")
    sources = {source["origin"]: source for source in result["sources"]}
    assert result["leavesDevice"]
    assert sources[f"node:{root_id}"]["included"] is False
    assert sources[f"node:{root_id}"]["reason"] != "within budget"
    assert sources[f"node:{root_id}"]["leavesDevice"] is False
    assert sources[f"draft:after:{last_id}"]["included"] is True
    assert sources[f"draft:after:{last_id}"]["leavesDevice"] is True


def test_raw_branch_override_replaces_default_persona():
    document, _, last_id = _document()
    dispatcher = _Dispatcher(override="Raw replacement only")
    result = inspect_next_chat_send(document, last_id, dispatcher, draft_text="new message")
    assert result["sources"][0]["origin"].startswith("system-note:")
    assert result["sources"][0]["included"]
    assert "error" not in result  # preview succeeds without dispatch


def test_invalid_node_rejected_without_mutation():
    document, _, _ = _document()
    with pytest.raises(SceneError, match="not a chat"):
        inspect_next_chat_send(document, "n-nonexistent", _Dispatcher())
    with pytest.raises(ValueError, match="draft_text"):
        inspect_next_chat_send(document, next(iter(document.nodes)), _Dispatcher(), draft_text=123)


def test_empty_draft_is_distinguishable_from_sending_and_is_not_dispatched():
    document, _, node_id = _document()
    result = inspect_next_chat_send(document, node_id, _Dispatcher(), draft_text="")
    assert result["draftIncluded"] is False
    assert result["sources"][-1]["origin"] == f"draft:after:{node_id}"
    assert result["sources"][-1]["kind"] == "current_message"


def test_impossible_mandatory_budget_lists_all_unsent_sources():
    document, _, last_id = _document()
    dispatcher = _Dispatcher(provider="OpenAI", window=300, override="Z" * 100_000)
    response = inspect_next_chat_send(document, last_id, dispatcher, draft_text="hello")
    assert "error" in response
    assert len(response["sources"]) == 4
    assert response["promptTokens"] == 0
    assert all(not row["included"] for row in response["sources"])
    assert all(not row["leavesDevice"] for row in response["sources"])
    assert all("mandatory" in row["reason"] for row in response["sources"])


def test_inspector_includes_ranked_draft_passages_and_audits_last_reply(monkeypatch):
    from backend.context_engine import inspector
    from backend.context_engine.citations import CitedChatReply, apply_reply_citations

    hit = {
        "citation_id": "kb:0123456789abcdef0123",
        "document_title": "Recovery guide",
        "source_uri": "ops/recovery.md",
        "source_file": "ops/recovery.md",
        "section": "Backups",
        "text": "Use a verified snapshot to restore service.",
        "offset_start": 10, "offset_end": 53,
    }
    monkeypatch.setattr(inspector, "search_chat_knowledge", lambda *_args: [hit])
    document, _root_id, last_id = _document()
    document.set_use_knowledge_override(last_id, True)
    apply_reply_citations(document.nodes[last_id], CitedChatReply(
        "Restore using the backup [kb:0123456789abcdef0123]", [hit],
    ))
    before = deepcopy(document.nodes), deepcopy(document.edges)
    report = inspect_next_chat_send(document, last_id, _Dispatcher(), draft_text="How to restore?")
    assert before == (document.nodes, document.edges)
    knowledge = [s for s in report["sources"] if s["kind"] == "knowledge_hit"]
    assert len(knowledge) == 1
    assert knowledge[0]["included"]
    assert knowledge[0]["citationId"] == hit["citation_id"]
    assert knowledge[0]["section"] == "Backups"
    audit = report["lastReplyCitations"]
    assert audit["replyNodeId"] == last_id
    assert audit["sources"][0]["citationId"] == hit["citation_id"]
    assert audit["sources"][0]["cited"] is True
    assert audit["sources"][0]["section"] == "Backups"


def test_disabled_knowledge_never_queries_or_previews_hits(monkeypatch):
    from backend.context_engine import inspector

    calls = []
    def record_search(*args):
        calls.append(args)
        return []

    monkeypatch.setattr(inspector, "search_chat_knowledge", record_search)
    document, root_id, last_id = _document()
    document.set_use_knowledge_override(root_id, False)
    report = inspect_next_chat_send(document, last_id, _Dispatcher(), draft_text="How to restore?")
    assert report["useKnowledge"] is False
    assert calls == []  # disabled means no search call, not just no matches
    assert all(s["kind"] != "knowledge_hit" for s in report["sources"])
    with pytest.raises(ValueError, match="use_knowledge"):
        inspect_next_chat_send(document, last_id, _Dispatcher(), use_knowledge="yes")
