"""Hybrid knowledge retrieval stays consistent across UI, chat and agent tools.

All embedding calls use an in-memory deterministic provider: no model server
or external API is contacted.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from types import SimpleNamespace

from backend import knowledge_embedding_config as config
from backend.api import intents_knowledge
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.harness import tools_shared
from backend.knowledge_chunking import chunk_text
from backend.knowledge_retrieval import format_untrusted_context, search_chat_knowledge
from backend.knowledge_store import add_document_with_chunks, get_or_create_workspace_collection
from backend.providers.base import ProviderCapabilities, ToolCall
from backend.tools import KNOWLEDGE_READ, RunContext, ToolRegistry
from backend.tools_knowledge import register_knowledge_tools


class _EmbeddingProvider:
    capabilities = ProviderCapabilities(embedding=True)

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [
            [1.0, 0.0] if "heron" in text.lower() or "wetland" in text.lower()
            else [0.0, 1.0]
            for text in texts
        ]


def _ingest(db_path, collection_id, text, source):
    return add_document_with_chunks(
        db_path, source_uri=source, title=source, mime="text/plain",
        text=text, chunks=chunk_text(text), collection_id=collection_id,
    )


def test_ui_chat_and_tool_use_scoped_hybrid_search_and_expose_match_signals(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    own = get_or_create_workspace_collection(db, 71)
    other = get_or_create_workspace_collection(db, 72)
    _ingest(db, own, "The blue heron keeps watch over the marshland.", "own.md")
    _ingest(db, own, "A pressure valve opens in the boiler room.", "noise.md")
    _ingest(db, other, "Wetland bird records belong only to the other workspace.", "other.md")

    provider = _EmbeddingProvider()
    manager = object()

    @contextmanager
    def selected_model(_manager):
        assert _manager is manager
        yield provider, "ollama:fixture-model"

    monkeypatch.setattr(config, "embedding_provider_for_choice", selected_model)
    monkeypatch.setattr(intents_knowledge, "DEFAULT_DB_PATH", db)

    document = SceneDocument()
    document.current_workspace_id = 71
    bus = SessionBus("knowledge-hybrid-test")
    intents_knowledge.register_knowledge_intents(bus, document, settings_manager=manager)

    # No lexical overlap with own.md: this hit is semantic-only.
    ui = asyncio.run(bus.dispatch_intent("knowledge", "search", ["wetland bird", 1]))["results"]
    assert len(ui) == 1 and ui[0]["sourceUri"] == "own.md"
    assert ui[0]["rankSignals"] == {"vector": 1}

    chat = search_chat_knowledge("wetland bird", 71, db_path=db, k=1, settings_manager=manager)
    assert len(chat) == 1 and chat[0]["source_uri"] == "own.md"
    assert chat[0]["rank_signals"] == {"vector": 1}

    registry = ToolRegistry()
    register_knowledge_tools(registry, document=document, db_path=db, settings_manager=manager)

    async def allow(_call):
        return True

    context = RunContext(
        granted_scopes=frozenset({KNOWLEDGE_READ}), request_approval=allow,
    )
    response = asyncio.run(registry.invoke(
        ToolCall("search-1", "knowledge.search", {"query": "wetland bird", "k": 1}),
        context,
    ))
    assert not response.is_error
    result = json.loads(response.content)["results"]
    assert len(result) == 1 and result[0]["source_uri"] == "own.md"
    assert result[0]["rank_signals"] == {"vector": 1}

    # Increasing k to include every chunk must not pad results with a
    # vector-only neighbor whose cosine similarity is zero.
    all_ui = asyncio.run(bus.dispatch_intent("knowledge", "search", ["wetland bird", 10]))["results"]
    assert [hit["sourceUri"] for hit in all_ui] == ["own.md"]
    all_chat = search_chat_knowledge("wetland bird", 71, db_path=db, k=10, settings_manager=manager)
    assert [hit["source_uri"] for hit in all_chat] == ["own.md"]
    all_tool = asyncio.run(registry.invoke(
        ToolCall("search-all", "knowledge.search", {"query": "wetland bird", "k": 10}),
        context,
    ))
    assert not all_tool.is_error
    assert [hit["source_uri"] for hit in json.loads(all_tool.content)["results"]] == ["own.md"]

    # An active embedding choice cannot cross the current collection.
    document.current_workspace_id = 72
    other_ui = asyncio.run(bus.dispatch_intent("knowledge", "search", ["wetland bird", 1]))["results"]
    assert len(other_ui) == 1 and other_ui[0]["sourceUri"] == "other.md"
    assert other_ui[0]["rankSignals"] == {"fts5": 1, "vector": 1}

    # Off/no provider is keyword-only: no semantic-only hit is fabricated.
    assert search_chat_knowledge("wetland bird", 71, db_path=db, k=1) == []
    keyword = search_chat_knowledge("heron", 71, db_path=db, k=1)
    assert len(keyword) == 1 and keyword[0]["rank_signals"] == {"fts5": 1}
    # The cache avoids re-embedding the same chunks on later requests.
    assert provider.calls.count([
        "The blue heron keeps watch over the marshland.",
        "A pressure valve opens in the boiler room.",
    ]) == 1

    # A workspace with only irrelevant vectors returns no citations anywhere.
    only_noise = get_or_create_workspace_collection(db, 73)
    _ingest(db, only_noise, "Steam engine calibration instructions.", "noise-only.md")
    document.current_workspace_id = 73
    assert asyncio.run(bus.dispatch_intent("knowledge", "search", ["wetland heron", 10])) == {"results": []}
    no_chat_hits = search_chat_knowledge("wetland heron", 73, db_path=db, k=10, settings_manager=manager)
    assert no_chat_hits == []
    assert format_untrusted_context(no_chat_hits) == ""
    no_tool_hits = asyncio.run(registry.invoke(
        ToolCall("no-matches", "knowledge.search", {"query": "wetland heron", "k": 10}),
        context,
    ))
    assert not no_tool_hits.is_error
    assert json.loads(no_tool_hits.content)["results"] == []


def test_workspace_agent_fallback_passes_embedding_settings_to_read_only_search(monkeypatch):
    from backend import tools_fal, tools_graph, tools_knowledge

    manager = object()
    dispatcher = SimpleNamespace(_settings_manager=manager)
    document = SceneDocument()
    registry = ToolRegistry()
    observed = []

    monkeypatch.setattr(tools_graph, "register_graph_tools", lambda *_args: None)
    monkeypatch.setattr(tools_graph, "register_run_node_tool", lambda *_args: None)
    monkeypatch.setattr(tools_fal, "register_fal_tools", lambda *_args: None)
    monkeypatch.setattr(
        tools_knowledge, "register_knowledge_tools",
        lambda _registry, **kwargs: observed.append(kwargs),
    )

    tools_shared.register_workspace_tools(registry, dispatcher, document)
    assert observed == [{"document": document, "settings_manager": manager}]

def test_knowledge_panel_drops_in_flight_results_after_workspace_switch(tmp_path, monkeypatch):
    from threading import Event

    db = tmp_path / "knowledge.db"
    document = SceneDocument()
    document.current_workspace_id = 1
    bus = SessionBus("knowledge-switch-test")
    monkeypatch.setattr(intents_knowledge, "DEFAULT_DB_PATH", db)
    intents_knowledge.register_knowledge_intents(bus, document)

    entered, release = Event(), Event()

    def held_search(*_args, **_kwargs):
        entered.set()
        if not release.wait(3):
            raise RuntimeError("Knowledge search barrier timed out")
        return [{"chunk_id": 1}]

    monkeypatch.setattr(intents_knowledge, "configured_hybrid_search", held_search)

    async def switch_during_search():
        pending = asyncio.create_task(bus.dispatch_intent("knowledge", "search", ["query", 1]))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            document.current_workspace_id = 2
        finally:
            release.set()
        return await pending

    assert asyncio.run(switch_during_search()) == {"results": []}
