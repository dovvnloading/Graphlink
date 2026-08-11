"""ADR-017 stage 17.2: knowledge.search registered on a real ToolRegistry.

Exercised via direct registry.invoke() calls, not a live model conversation
- see backend/tools_knowledge.py's own module docstring for why (ADR-008's
tool-use loop does not exist yet), mirroring backend/tests/
test_tool_registry.py's own established pattern for testing the registry
layer itself.
"""

from __future__ import annotations

import asyncio
import json


from backend.knowledge_chunking import chunk_text
from backend.knowledge_store import add_document_with_chunks
from backend.providers.base import ToolCall
from backend.tools import KNOWLEDGE_READ, GRAPH_READ, RunContext, ToolRegistry
from backend.tools_knowledge import KNOWLEDGE_SEARCH_SPEC, register_knowledge_tools


def _run(coro):
    return asyncio.run(coro)


def _ingest(db_path, *, text="The quick brown fox jumps over the lazy dog.", **kwargs):
    return add_document_with_chunks(
        db_path,
        source_uri=kwargs.pop("source_uri", "fox.txt"),
        title=kwargs.pop("title", "Fox Story"),
        mime="text/plain",
        text=text,
        chunks=chunk_text(text, target_tokens=1000),
        **kwargs,
    )


def _ctx(granted_scopes=(KNOWLEDGE_READ,)) -> RunContext:
    async def request_approval(call: ToolCall) -> bool:
        return True

    return RunContext(granted_scopes=frozenset(granted_scopes), request_approval=request_approval)


def _registry(db_path) -> ToolRegistry:
    registry = ToolRegistry()
    register_knowledge_tools(registry, db_path=db_path)
    return registry


class TestRegistration:
    def test_registers_under_the_knowledge_read_scope_with_auto_approval(self, tmp_path):
        registry = _registry(tmp_path / "knowledge.db")
        assert registry.specs() == (KNOWLEDGE_SEARCH_SPEC,)

    def test_a_run_without_the_knowledge_read_scope_is_denied_before_the_handler_runs(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path)
        registry = _registry(db_path)
        result = _run(
            registry.invoke(
                ToolCall(id="1", name="knowledge.search", arguments={"query": "fox"}),
                _ctx(granted_scopes=(GRAPH_READ,)),
            )
        )
        assert result.is_error is True
        assert "knowledge.read" in result.content


class TestInvocation:
    def test_a_real_search_round_trips_through_invoke_with_correct_citation_fields(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path)
        registry = _registry(db_path)

        result = _run(
            registry.invoke(
                ToolCall(id="1", name="knowledge.search", arguments={"query": "brown fox"}),
                _ctx(),
            )
        )
        assert result.is_error is False
        payload = json.loads(result.content)
        assert len(payload) == 1
        assert payload[0]["document_title"] == "Fox Story"
        assert payload[0]["source_uri"] == "fox.txt"
        assert "fox" in payload[0]["text"].lower()
        assert isinstance(payload[0]["offset_start"], int)
        assert isinstance(payload[0]["offset_end"], int)

    def test_no_matches_is_a_successful_empty_result_not_an_error(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path)
        registry = _registry(db_path)
        result = _run(
            registry.invoke(
                ToolCall(id="1", name="knowledge.search", arguments={"query": "submarine reactor core"}),
                _ctx(),
            )
        )
        assert result.is_error is False
        assert "No matching" in result.content

    def test_collection_id_and_k_arguments_are_honored(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path, text="Alpha content about pandas.", collection_id=1, source_uri="a.txt")
        _ingest(db_path, text="Beta content about pandas.", collection_id=2, source_uri="b.txt")
        registry = _registry(db_path)

        result = _run(
            registry.invoke(
                ToolCall(
                    id="1", name="knowledge.search",
                    arguments={"query": "pandas", "collection_id": 1, "k": 1},
                ),
                _ctx(),
            )
        )
        payload = json.loads(result.content)
        assert len(payload) == 1
        assert payload[0]["source_uri"] == "a.txt"

    def test_a_missing_query_is_a_clean_error_result_not_a_raise(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path)
        registry = _registry(db_path)
        result = _run(
            registry.invoke(ToolCall(id="1", name="knowledge.search", arguments={}), _ctx())
        )
        assert result.is_error is True
        assert "query" in result.content

    def test_a_non_integer_k_is_a_clean_error_result_not_a_raise(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        _ingest(db_path)
        registry = _registry(db_path)
        result = _run(
            registry.invoke(
                ToolCall(id="1", name="knowledge.search", arguments={"query": "fox", "k": "lots"}),
                _ctx(),
            )
        )
        assert result.is_error is True
        assert "k" in result.content

    def test_k_is_capped_at_the_module_maximum_even_if_the_caller_asks_for_more(self, tmp_path):
        db_path = tmp_path / "knowledge.db"
        for i in range(30):
            _ingest(db_path, text=f"Entry {i} about koalas.", source_uri=f"koala{i}.txt")
        registry = _registry(db_path)

        result = _run(
            registry.invoke(
                ToolCall(id="1", name="knowledge.search", arguments={"query": "koalas", "k": 9999}),
                _ctx(),
            )
        )
        payload = json.loads(result.content)
        from backend.tools_knowledge import _MAX_K
        assert len(payload) == _MAX_K
