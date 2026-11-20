"""Incremental SQLite FTS5 index for one workspace agent's append-only JSONL.

The transcript remains authoritative. This database is a disposable sidecar:
each sync scans only bytes after its last committed offset, so it also catches
up after a crash between the JSONL append and the index commit. An existing
transcript is indexed on first search, without a migration or a full scan on
every subsequent query. Keeping the index in the same managed scratch
workspace as the transcript preserves per-agent isolation and existing GC.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

INDEX_FILENAME = "transcript-search.sqlite3"
MAX_QUERY_CHARS = 256
MAX_QUERY_TERMS = 16
MAX_MATCHES = 10
MAX_EXCERPT_CHARS = 500
_MAX_INDEX_LINE_BYTES = 1024 * 1024


def index_path(transcript: Path) -> Path:
    return transcript.with_name(INDEX_FILENAME)


def _open_index(path: Path) -> sqlite3.Connection:
    # The workspace is writable by the agent. Never follow a planted index
    # symlink into another file: this is derived application state, not a
    # caller-selected SQLite database.
    if path.is_symlink():
        raise OSError("Transcript search index must not be a symlink.")
    db = sqlite3.connect(str(path), timeout=5.0)
    try:
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING "
            "fts5(content, role UNINDEXED, line_number UNINDEXED, tokenize='unicode61')"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS transcript_index_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "offset INTEGER NOT NULL, line_number INTEGER NOT NULL)"
        )
    except Exception:
        db.close()
        raise
    return db


def _searchable_text(item: object) -> tuple[str, str] | None:
    if not isinstance(item, dict):
        return None
    kind = item.get("t")
    if kind == "compact":
        content = item.get("content")
        if isinstance(content, str) and content:
            return "summary", content
        return None
    if kind != "msg":
        return None

    # Search results themselves are observations, not new history. Indexing
    # them recursively makes repeated searches drown out the original turns.
    if item.get("role") == "tool" and item.get("name") == "session.search":
        return None
    content = item.get("content")
    parts = [content] if isinstance(content, str) and content else []
    calls = item.get("tool_calls")
    if isinstance(calls, list) and calls:
        # Searching must not manufacture future matches from its own query
        # arguments, just as its tool result must not be re-indexed.
        other_calls = [call for call in calls if not (
            isinstance(call, dict) and call.get("name") == "session.search"
        )]
        if other_calls:
            parts.append(json.dumps(other_calls, ensure_ascii=False))
    text = "\n".join(parts)
    if not text:
        return None
    return str(item.get("role") or "unknown"), text


def sync_index(transcript: Path) -> None:
    """Index complete JSONL records not yet committed to this sidecar.

    Called by the transcript's background writer after each append, and by
    search after a flush. Offsets and FTS rows commit in one transaction;
    an interrupted index update is replayed rather than lost or duplicated.
    Invalid JSONL records are skipped with the same tolerant posture as
    load_messages. An incomplete final line is retried after completion.
    """
    if transcript.is_symlink():
        raise OSError("Transcript must not be a symlink for indexing.")
    if not transcript.is_file():
        return
    with closing(_open_index(index_path(transcript))) as db:
        with transcript.open("rb") as source, db:
            row = db.execute(
                "SELECT offset, line_number FROM transcript_index_state WHERE id = 1"
            ).fetchone()
            offset, line_number = (int(row[0]), int(row[1])) if row else (0, 0)
            source.seek(0, 2)
            if offset > source.tell():
                # The transcript was truncated/replaced. Its search index
                # cannot keep referring to records that no longer exist.
                db.execute("DELETE FROM transcript_fts")
                offset, line_number = 0, 0
            source.seek(offset)

            while True:
                raw = source.readline(_MAX_INDEX_LINE_BYTES + 1)
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    if len(raw) <= _MAX_INDEX_LINE_BYTES:
                        break  # incomplete trailing record; retry next sync
                    # Skip an oversized line in bounded chunks; do not
                    # materialize an untrusted multi-gigabyte JSON value.
                    while raw and not raw.endswith(b"\n"):
                        raw = source.readline(_MAX_INDEX_LINE_BYTES + 1)
                    if not raw:
                        break
                    raw = b""  # consumed but intentionally not indexed
                offset = source.tell()
                line_number += 1
                if not raw:
                    continue
                try:
                    item = json.loads(raw)
                    searchable = _searchable_text(item)
                except (ValueError, UnicodeError, TypeError):
                    continue
                if searchable is not None:
                    role, content = searchable
                    db.execute(
                        "INSERT INTO transcript_fts (content, role, line_number) "
                        "VALUES (?, ?, ?)",
                        (content, role, line_number),
                    )
            db.execute(
                "INSERT INTO transcript_index_state (id, offset, line_number) "
                "VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "offset=excluded.offset, line_number=excluded.line_number",
                (offset, line_number),
            )


def _match_expression(query: str) -> str:
    if not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"Search query must be 1-{MAX_QUERY_CHARS} characters.")
    # Quote literal Unicode tokens, not user-supplied FTS5 syntax. This
    # prevents malformed MATCH expressions and FTS operators in prompts.
    terms = re.findall(r"\w+", query, flags=re.UNICODE)
    if not terms or len(terms) > MAX_QUERY_TERMS:
        raise ValueError(f"Search needs 1-{MAX_QUERY_TERMS} words or numbers.")
    return " AND ".join('"' + term + '"' for term in terms)


def search_index(transcript: Path, query: str, *, limit: int = 5) -> list[dict]:
    """Return bounded, ranked excerpts from this one agent's transcript.

    The caller must first flush the transcript writer. Older transcripts
    and missed post-crash writes are backfilled before the query.
    """
    match = _match_expression(query)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_MATCHES:
        raise ValueError(f"Search limit must be between 1 and {MAX_MATCHES}.")
    if not transcript.is_file():
        return []
    sync_index(transcript)
    with closing(_open_index(index_path(transcript))) as db:
        rows = db.execute(
            "SELECT role, line_number, "
            "snippet(transcript_fts, 0, '[', ']', '…', 24) "
            "FROM transcript_fts WHERE transcript_fts MATCH ? "
            "ORDER BY bm25(transcript_fts), rowid DESC LIMIT ?",
            (match, limit),
        ).fetchall()
    return [
        {
            "role": role,
            "line": int(line_number),
            "excerpt": excerpt[:MAX_EXCERPT_CHARS] + (
                "…" if len(excerpt) > MAX_EXCERPT_CHARS else ""
            ),
        }
        for role, line_number, excerpt in rows
    ]
