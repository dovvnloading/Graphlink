"""SQLite FTS5 transcript search: incremental writes, recovery and scope."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend.harness import transcript as transcript_module
from backend.harness import transcript_index as index_module
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
    # Windows CI can be CPU- and filesystem-busy under parallel coverage;
    # allow the writer time to finish FTS without weakening its default
    # production flush timeout.
    assert flush(tmp_path, timeout=20.0)

    transcript = transcript_path(tmp_path)
    assert index_path(transcript).is_file()  # indexed on write, not just on search
    # The indexer never passes the scratch pathname to SQLite, so no WAL,
    # shared-memory or rollback-journal companion is created there.
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(str(index_path(transcript)) + suffix).exists()
    assert {hit["role"] for hit in search_index(transcript, "chrysanthemum")} == {
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
    # sqlite3.Connection.__exit__ commits but does NOT close the handle.
    # Windows refuses to unlink the index while that handle is still open.
    with closing(sqlite3.connect(index_path(transcript))) as db:
        assert db.execute("SELECT count(*) FROM transcript_fts").fetchone()[0] == 2

    # The FTS database is derived data: deleting it must not lose history.
    index_path(transcript).unlink()
    assert len(search_index(transcript, "lavender")) == 1
    assert len(search_index(transcript, "marigold")) == 1



def test_corrupted_sidecar_is_rebuilt_from_unchanged_transcript(tmp_path):
    transcript = transcript_path(tmp_path)
    transcript.write_text('{"t":"msg","role":"user","content":"durable saffron"}\n', encoding="utf-8")
    assert len(search_index(transcript, "saffron")) == 1
    original = transcript.read_bytes()
    sidecar = index_path(transcript)
    sidecar.write_bytes(b"not a sqlite database")

    hits = search_index(transcript, "saffron")
    assert len(hits) == 1 and hits[0]["role"] == "user"
    assert len(search_index(transcript, "saffron")) == 1
    assert sidecar.read_bytes().startswith(b"SQLite format 3\x00")
    assert transcript.read_bytes() == original


def test_incompatible_fts_schema_is_recreated_from_jsonl(tmp_path):
    transcript = transcript_path(tmp_path)
    transcript.write_text('{"t":"msg","role":"assistant","content":"violet ledger"}\n', encoding="utf-8")
    with closing(sqlite3.connect(index_path(transcript))) as db:
        db.execute("CREATE TABLE transcript_fts (content TEXT)")
        db.commit()

    assert len(search_index(transcript, "violet")) == 1
    assert len(search_index(transcript, "ledger")) == 1


@pytest.mark.parametrize("fts_columns", [
    "content",
    "content, role UNINDEXED",
    "content, line_number UNINDEXED",
    "content, role UNINDEXED, other UNINDEXED",
    "role UNINDEXED, content, line_number UNINDEXED",
])
def test_incompatible_fts5_columns_rebuild_from_unchanged_jsonl(tmp_path, fts_columns):
    transcript = transcript_path(tmp_path)
    transcript.write_text(
        '{"t":"msg","role":"user","content":"violet ledger"}\n', encoding="utf-8",
    )
    original = transcript.read_bytes()
    with closing(sqlite3.connect(index_path(transcript))) as db:
        db.execute(f"CREATE VIRTUAL TABLE transcript_fts USING fts5({fts_columns})")
        db.commit()

    hits = search_index(transcript, "violet")
    assert len(hits) == 1 and hits[0]["role"] == "user"
    assert hits[0]["line"] == 1
    assert len(search_index(transcript, "ledger")) == 1
    with closing(sqlite3.connect(index_path(transcript))) as db:
        assert [column[1] for column in db.execute("PRAGMA table_info(transcript_fts)")] == [
            "content", "role", "line_number",
        ]
    assert transcript.read_bytes() == original


@pytest.mark.parametrize("fts_definition", [
    "content UNINDEXED, role UNINDEXED, line_number UNINDEXED",
    "content, role UNINDEXED, line_number UNINDEXED, content=''",
    "content, role UNINDEXED, line_number UNINDEXED, tokenize='porter'",
])
def test_incompatible_fts5_options_rebuild_even_with_matching_columns(tmp_path, fts_definition):
    transcript = transcript_path(tmp_path)
    transcript.write_text(
        '{"t":"msg","role":"user","content":"violet ledger"}\n', encoding="utf-8",
    )
    original = transcript.read_bytes()
    with closing(sqlite3.connect(index_path(transcript))) as db:
        db.execute(f"CREATE VIRTUAL TABLE transcript_fts USING fts5({fts_definition})")
        assert [row[1] for row in db.execute("PRAGMA table_info(transcript_fts)")] == [
            "content", "role", "line_number",
        ]
        db.commit()

    hits = search_index(transcript, "violet")
    assert len(hits) == 1
    assert hits[0]["role"] == "user" and hits[0]["line"] == 1
    assert len(search_index(transcript, "ledger")) == 1
    with closing(sqlite3.connect(index_path(transcript))) as db:
        definition = db.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'transcript_fts'"
        ).fetchone()[0]
    assert definition == index_module._FTS_DEFINITION
    assert transcript.read_bytes() == original


def test_corrupt_index_recovery_never_follows_symlink(tmp_path):
    transcript = transcript_path(tmp_path)
    transcript.write_text('{"t":"msg","role":"user","content":"poppy"}\n', encoding="utf-8")
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"private file")
    try:
        index_path(transcript).symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    with pytest.raises(OSError, match="symlink"):
        search_index(transcript, "poppy")
    assert outside.read_bytes() == b"private file"


def test_sidecar_open_race_never_follows_replaced_symlink(tmp_path, monkeypatch):
    """The link is planted after the fast path check, before the actual read.

    SQLite must never open the sidecar pathname, even if an attacker swaps it
    to a writable database outside the managed search index location.
    """
    from backend.harness import tools_fs

    scratch = tmp_path / "managed-workspace"
    scratch.mkdir()
    transcript = transcript_path(scratch)
    transcript.write_text(
        '{"t":"msg","role":"user","content":"violet ledger"}\n', encoding="utf-8",
    )
    sidecar = index_path(transcript)
    outside = tmp_path / "outside.sqlite3"
    with closing(sqlite3.connect(outside)) as db:
        db.execute("CREATE TABLE sentinel (value TEXT)")
        db.execute("INSERT INTO sentinel VALUES ('unchanged')")
        db.commit()
    probe = tmp_path / "symlink-probe"
    try:
        probe.symlink_to(outside)
        probe.unlink()
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    actual_connect = sqlite3.connect

    def reject_pathname_connect(database, *args, **kwargs):
        assert str(database) != str(sidecar), "SQLite must never open the untrusted sidecar path"
        return actual_connect(database, *args, **kwargs)

    monkeypatch.setattr(index_module.sqlite3, "connect", reject_pathname_connect)
    original_open = tools_fs._open_bounded_file_fd

    def race_after_link_check(path: Path):
        if path == sidecar:
            sidecar.symlink_to(outside)
        return original_open(path)

    monkeypatch.setattr(tools_fs, "_open_bounded_file_fd", race_after_link_check)
    with pytest.raises(OSError, match="symlink|path changed"):
        index_module.sync_index(transcript)

    with closing(actual_connect(outside)) as db:
        assert db.execute("SELECT value FROM sentinel").fetchone() == ("unchanged",)
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'transcript_%'"
        ).fetchall() == []
    assert sidecar.is_symlink()


def test_replacing_transcript_without_shrinking_invalidates_old_fts_rows(tmp_path):
    transcript = transcript_path(tmp_path)
    old = b'{"t":"msg","role":"user","content":"alpha"}\n'
    replacement = b'{"t":"msg","role":"user","content":"bravo"}\n'
    assert len(old) == len(replacement)

    transcript.write_bytes(old)
    assert len(search_index(transcript, "alpha")) == 1
    transcript.write_bytes(replacement)  # Same inode/size on many filesystems.
    assert search_index(transcript, "alpha") == []
    assert len(search_index(transcript, "bravo")) == 1

    # An in-place rewrite to a larger file must also invalidate prior rows,
    # rather than seek directly to the old byte offset.
    transcript.write_bytes(b'{"t":"msg","role":"user","content":"chrysanthemum"}\n')
    assert search_index(transcript, "bravo") == []
    assert len(search_index(transcript, "chrysanthemum")) == 1

    # A normal append after a reset still indexes once without duplication.
    with transcript.open("ab") as stream:
        stream.write(b'{"t":"msg","role":"assistant","content":"marigold"}\n')
    assert len(search_index(transcript, "chrysanthemum")) == 1
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

    def broken_index(path, **kwargs):
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
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "lookup", "name": "session.search",
                        "arguments": {"query": "notrealhistory"}}],
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
    assert not search_index(transcript_path(tmp_path), "notrealhistory")

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


def test_writer_batches_search_indexing_until_flush(tmp_path, monkeypatch):
    indexed = []
    original_sync = transcript_module.sync_index

    def count_sync(path, **kwargs):
        indexed.append(path)
        original_sync(path, **kwargs)

    monkeypatch.setattr(transcript_module, "sync_index", count_sync)
    append_message(tmp_path, {"role": "user", "content": "first observation"})
    append_message(tmp_path, {"role": "assistant", "content": "second observation"})
    assert flush(tmp_path)
    assert indexed.count(transcript_path(tmp_path)) == 1
    assert len(search_index(transcript_path(tmp_path), "observation")) == 2


def test_writer_appends_reuse_hash_checkpoint_instead_of_rereading_prefix(tmp_path, monkeypatch):
    append_message(tmp_path, {"role": "user", "content": "archive " * 60000})
    assert flush(tmp_path)
    transcript = transcript_path(tmp_path)

    # Windows can expose different ctime values via path.stat and fstat for
    # the same file. Reproduce that disagreement on all platforms.
    original_stat = type(transcript).stat

    class DifferentPathCtime:
        def __init__(self, stats):
            self.stats = stats

        def __getattr__(self, name):
            if name == "st_ctime_ns":
                return self.stats.st_ctime_ns + 1_000_000
            return getattr(self.stats, name)

    def different_path_stat(path, *args, **kwargs):
        stats = original_stat(path, *args, **kwargs)
        return DifferentPathCtime(stats) if path == transcript else stats

    monkeypatch.setattr(type(transcript), "stat", different_path_stat)

    rescans = []
    original = index_module._prefix_hasher

    def counted(source, length):
        rescans.append(length)
        return original(source, length)

    monkeypatch.setattr(index_module, "_prefix_hasher", counted)
    for i in range(4):
        append_message(tmp_path, {"role": "user", "content": f"incremental marigold {i}"})
        assert flush(tmp_path)
    assert rescans == []
    assert len(search_index(transcript, "marigold")) == 4
    assert rescans == []  # Search with an unchanged source is also cheap.


def test_external_rewrite_before_writer_append_invalidates_cached_index(tmp_path):
    append_message(tmp_path, {"role": "user", "content": "alpha"})
    assert flush(tmp_path)
    transcript = transcript_path(tmp_path)
    # Same-size rewrite on the same inode, then an ordinary writer append.
    original = transcript.read_bytes()
    assert b"alpha" in original
    transcript.write_bytes(original.replace(b"alpha", b"bravo"))
    append_message(tmp_path, {"role": "assistant", "content": "charlie"})
    assert flush(tmp_path)
    assert search_index(transcript, "alpha") == []
    assert len(search_index(transcript, "bravo")) == 1
    assert len(search_index(transcript, "charlie")) == 1


def test_loader_refuses_to_invent_interrupted_results_when_writer_is_behind(tmp_path, monkeypatch):
    append_message(tmp_path, {
        "role": "assistant", "content": "working",
        "tool_calls": [{"id": "pending", "name": "fs.read", "arguments": {}}],
    })
    assert flush(tmp_path)
    monkeypatch.setattr(transcript_module, "flush", lambda *_args, **_kw: False)
    with pytest.raises(OSError, match="did not finish flushing"):
        load_messages(tmp_path)
