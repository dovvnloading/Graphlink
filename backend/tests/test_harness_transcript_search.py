"""SQLite FTS5 transcript search: incremental writes, recovery and scope."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from backend.harness import transcript as transcript_module
from backend.harness import workspace as workspace_module
from backend.harness.tools_transcript import register_workspace_tools
from backend.harness.transcript import (
    append_compaction, append_message, flush, load_messages, transcript_path,
)
from backend.harness.transcript_index import (
    MAX_EXCERPT_CHARS, index_path, search_index,
)
from backend.providers.base import ToolCall
from backend.tools import FS_READ, RunContext, ToolRegistry


def test_writer_indexes_messages_calls_and_compaction_without_rewriting_history(tmp_path):
    append_message(tmp_path, {"role": "user", "content": "Find the chrysanthemum decision."})
    append_message(tmp_path, {
        "role": "assistant", "content": "I checked the earlier design.",
        "tool_calls": [{"id": "c1", "name": "fs.read", "arguments": {"path": "stellar-notes.md"}}],
    })
    append_compaction(tmp_path, "Historical summary: chrysanthemum was selected.")
    assert flush(tmp_path)

    transcript = transcript_path(tmp_path)
    assert index_path(transcript).is_file()  # indexed on write, not just on search
    assert [hit["role"] for hit in search_index(transcript, "chrysanthemum")] == [
        "user", "summary",
    ] or {hit["role"] for hit in search_index(transcript, "chrysanthemum")} == {
        "user", "summary",
    }
    calls = search_index(transcript, "stellar notes")
    assert len(calls) == 1 and calls[0]["role"] == "assistant"
    assert calls[0]["line"] >= 2
    assert "[stellar]" in calls[0]["excerpt"].lower()
    assert "chrysanthemum" in json.dumps(load_messages(tmp_path)).lower()


def test_legacy_backfill_and_crash_gap_sync_are_idempotent(tmp_path):
    transcript = transcript_path(tmp_path)
    transcript.write_text(
        '{"t":"meta","v":1}\n'
        '{"t":"msg","role":"user","content":"legacy lavender answer"}\n'
        'not json\n',
        encoding="utf-8",
    )
    assert not index_path(transcript).exists()
    first = search_index(transcript, "lavender")
    assert len(first) == 1 and first[0]["line"] == 2

    # Simulate a transcript append committed before its sidecar write.
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write('{"t":"msg","role":"assistant","content":"new marigold answer"}\n')
    assert len(search_index(transcript, "marigold")) == 1
    assert len(search_index(transcript, "marigold")) == 1
    with sqlite3.connect(index_path(transcript)) as db:
        assert db.execute("SELECT count(*) FROM transcript_fts").fetchone()[0] == 2

    # The FTS database is derived data: deleting it must not lose history.
    index_path(transcript).unlink()
    assert len(search_index(transcript, "lavender")) == 1
    assert len(search_index(transcript, "marigold")) == 1


def test_incomplete_and_oversized_lines_do_not_poison_index(tmp_path):
    transcript = transcript_path(tmp_path)
    transcript.write_bytes(
        b'{"t":"msg","role":"user","content":"complete pinecone"}\n'
        b'{"t":"msg","role":"user","content":"unfinished elderberry'
    )
    assert len(search_index(transcript, "pinecone")) == 1
    assert not search_index(transcript, "elderberry")

    with transcript.open("ab") as fh:
        fh.write(b'"}\n')
        # A hostile oversized record must not be loaded into memory.
        fh.write(b"x" * (1024 * 1024 + 100) + b"\n")
        fh.write(b'{"t":"msg","role":"user","content":"final hazelnut"}\n')
    assert len(search_index(transcript, "elderberry")) == 1
    assert len(search_index(transcript, "hazelnut")) == 1


def test_index_failure_cannot_lose_the_source_transcript(tmp_path, monkeypatch):
    real_sync = transcript_module.sync_index

    def broken_index(path):
        raise sqlite3.OperationalError("simulated FTS write failure")

    monkeypatch.setattr(transcript_module, "sync_index", broken_index)
    append_message(tmp_path, {"role": "user", "content": "durable persimmon"})
    assert flush(tmp_path)
    assert load_messages(tmp_path)[0]["content"] == "durable persimmon"

    monkeypatch.setattr(transcript_module, "sync_index", real_sync)
    assert len(search_index(transcript_path(tmp_path), "persimmon")) == 1


def test_search_excludes_search_tool_output_and_bounds_excerpts(tmp_path):
    append_message(tmp_path, {
        "role": "tool", "name": "session.search",
        "content": "result duplicated old kiwi history",
    })
    append_message(tmp_path, {
        "role": "tool", "name": "fs.read",
        "content": "a real kiwi observation " + "a " * 1000,
    })
    assert flush(tmp_path)
    hits = search_index(transcript_path(tmp_path), "kiwi")
    assert len(hits) == 1
    assert hits[0]["role"] == "tool"
    assert len(hits[0]["excerpt"]) <= MAX_EXCERPT_CHARS + 1

    with pytest.raises(ValueError):
        search_index(transcript_path(tmp_path), '"')
    with pytest.raises(ValueError):
        search_index(transcript_path(tmp_path), "kiwi", limit=100)
    # User input is literal tokens, not executable FTS5/SQL syntax.
    assert not search_index(transcript_path(tmp_path), 'kiwi OR "imaginary"')


def test_auto_approved_search_only_reads_its_own_agent_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_module, "HARNESS_WORKSPACE_ROOT", tmp_path / "agent-roots")
    own = workspace_module.ensure_workspace("mine")
    other = workspace_module.ensure_workspace("someone-else")
    append_message(own, {"role": "user", "content": "private juniper recall"})
    append_message(other, {"role": "user", "content": "secret elderflower recall"})
    assert flush(own) and flush(other)

    registry = ToolRegistry()
    register_workspace_tools(registry, None, None)
    assert registry.scopes_for("session.search") == frozenset({FS_READ})
    assert registry.approval_for("session.search") == "auto"
    assert registry.parallel_safe_for("session.search")

    async def must_not_ask(_call):
        raise AssertionError("read-only search must not request approval")

    ctx = RunContext(granted_scopes=frozenset({FS_READ}), request_approval=must_not_ask)
    ctx.harness_workspace_id = "mine"
    ctx.harness_workspace_dir = other  # a project-folder binding must not redirect history

    def invoke(arguments):
        return asyncio.run(registry.invoke(
            ToolCall(id="find", name="session.search", arguments=arguments), ctx,
        ))

    found = invoke({"query": "juniper"})
    assert not found.is_error
    assert "juniper" in found.content and "transcript.jsonl:" in found.content
    assert "elderflower" not in found.content
    assert "No matching" in invoke({"query": "elderflower"}).content
    assert invoke({"query": ""}).is_error
    assert invoke({"query": "juniper", "limit": 0}).is_error
    assert invoke({"query": "juniper", "limit": True}).is_error

    ctx.harness_workspace_id = None
    assert "No agent transcript" in invoke({"query": "juniper"}).content
