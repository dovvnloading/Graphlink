"""Incremental SQLite FTS5 index for one workspace agent's append-only JSONL.

The transcript remains authoritative. This database is a disposable sidecar:
untrusted changes are checked against the already indexed prefix, while
known writer appends continue an in-memory hash checkpoint without rereading
old records. This catches rewrites as well as crash gaps between the JSONL
append and the index commit. An existing transcript is indexed on first search
without a migration. Keeping the index in the same managed scratch
workspace as the transcript preserves per-agent isolation and existing GC.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import threading
from weakref import WeakValueDictionary
from collections import OrderedDict
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INDEX_FILENAME = "transcript-search.sqlite3"
# Only indexes written by this application have trustworthy FTS semantics.
# Checking PRAGMA table_info alone misses content UNINDEXED and contentless
# FTS5 tables, both of which expose the expected three columns.
_FTS_DEFINITION = (
    "CREATE VIRTUAL TABLE transcript_fts USING "
    "fts5(content, role UNINDEXED, line_number UNINDEXED, tokenize='unicode61')"
)
MAX_QUERY_CHARS = 256
MAX_QUERY_TERMS = 16
MAX_MATCHES = 10
MAX_EXCERPT_CHARS = 500
_MAX_INDEX_LINE_BYTES = 1024 * 1024


def index_path(transcript: Path) -> Path:
    return transcript.with_name(INDEX_FILENAME)


class _IncompatibleIndex(sqlite3.DatabaseError):
    """An existing disposable sidecar has an incompatible FTS schema."""


@contextmanager
def _index_directory(path: Path):
    """Pin the sidecar directory for POSIX atomic writes and cleanup.

    A final-component O_NOFOLLOW is insufficient if an agent can also swap a
    parent directory. On Windows, descriptor-relative operations are not
    supported; reads use the opened-handle path verification used by fs.read.
    """
    if os.name == "nt":
        yield None
        return
    if (not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY")
            or os.open not in os.supports_dir_fd):
        raise OSError("Safe transcript search index I/O is unavailable.")
    directory = path.absolute().parent
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        directory_fd = os.open(directory.anchor, flags)
        stack.callback(os.close, directory_fd)
        for component in directory.parts[1:]:
            directory_fd = os.open(component, flags, dir_fd=directory_fd)
            stack.callback(os.close, directory_fd)
        yield directory_fd


def _read_index_bytes(path: Path) -> bytes | None:
    """Read an existing SQLite image through a verified no-follow descriptor.

    SQLite's Unix VFS resolves even /proc/self/fd paths back to names before
    opening them, so passing a validated descriptor path to sqlite3.connect
    would still race. Deserialize only bytes read from the pinned inode.
    """
    if path.is_symlink():
        raise OSError("Transcript search index must not be a symlink.")
    from backend.harness.tools_fs import _open_bounded_file_fd
    from backend.harness.workspace import WorkspaceError

    try:
        fd = _open_bounded_file_fd(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise OSError("Transcript search index must not be a symlink.") from exc
        raise
    except WorkspaceError as exc:
        raise OSError("Transcript search index path changed during open.") from exc
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise OSError("Transcript search index must be a regular file.")
        return source.read()


def _save_index(path: Path, db: sqlite3.Connection) -> None:
    """Atomically publish an in-memory FTS5 image without SQLite side files.

    The sidecar is disposable: a crash during a save leaves the old complete
    image, while a crash inside SQLite cannot create a WAL/SHM/journal at an
    attacker-chosen path. The authoritative JSONL repairs any stale image.
    """
    payload = db.serialize()
    with _index_directory(path) as directory_fd:
        temporary_name = f".{INDEX_FILENAME}.{secrets.token_hex(12)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        if directory_fd is None:
            temporary = path.with_name(temporary_name)
            fd = os.open(temporary, flags, 0o600)
        else:
            fd = os.open(temporary_name, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, "wb") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            if directory_fd is None:
                # Replacing a symlink never writes through it, but a planted
                # link is also an explicit reason not to publish the index.
                if path.is_symlink():
                    raise OSError("Transcript search index must not be a symlink.")
                os.replace(temporary, path)
            else:
                os.replace(
                    temporary_name, path.name,
                    src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                )
                os.fsync(directory_fd)
        finally:
            try:
                if directory_fd is None:
                    temporary.unlink()
                else:
                    os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _open_index(path: Path) -> sqlite3.Connection:
    # Never let SQLite resolve a pathname inside the agent-writable scratch
    # directory. Use an in-memory connection for all FTS and state writes.
    image = _read_index_bytes(path)
    db = sqlite3.connect(":memory:", timeout=5.0)
    try:
        if image is not None:
            # WAL-mode main images need separate companion files, which must
            # never be opened by name here. Rebuild from JSONL instead.
            if (not image.startswith(b"SQLite format 3\x00") or len(image) < 20
                    or image[18:20] != b"\x01\x01"):
                raise _IncompatibleIndex("Unexpected transcript search database image.")
            db.deserialize(image)
        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING "
            "fts5(content, role UNINDEXED, line_number UNINDEXED, tokenize='unicode61')"
        )
        definition = db.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'transcript_fts'"
        ).fetchone()
        # Match the application's actual FTS5 definition, not just exposed
        # column names: UNINDEXED content and content='' silently lose hits
        # or result metadata even when PRAGMA table_info looks identical.
        columns = [column[1] for column in db.execute("PRAGMA table_info(transcript_fts)")]
        if (not definition or definition[0] != _FTS_DEFINITION
                or columns != ["content", "role", "line_number"]):
            raise _IncompatibleIndex("Unexpected transcript search FTS schema.")
        db.execute(
            "CREATE TABLE IF NOT EXISTS transcript_index_state ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "offset INTEGER NOT NULL, line_number INTEGER NOT NULL)"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS transcript_index_source ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "device TEXT NOT NULL, inode TEXT NOT NULL, "
            "prefix_sha256 TEXT NOT NULL)"
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


@dataclass(frozen=True)
class _HashCheckpoint:
    offset: int
    device: str
    inode: str
    prefix_sha256: str
    stamp: tuple[int, int, int, int, int]
    hasher: Any


# hashlib SHA-256 state cannot be serialized to SQLite. Keep a small,
# per-process cache so app-owned append/flush cycles hash only new bytes.
# A cold process or an untrusted external change re-hashes the old prefix.
_HASH_CHECKPOINTS: OrderedDict[str, _HashCheckpoint] = OrderedDict()
_HASH_CHECKPOINT_LOCK = threading.Lock()
_MAX_HASH_CHECKPOINTS = 16

# Search and writer flushes may run in parallel for the same transcript.
# Sidecar replacement must not race with another in-process reader/writer;
# locks are per-sidecar and weakly held so inactive agents do not accumulate.
_INDEX_LOCKS: WeakValueDictionary[str, threading.RLock] = WeakValueDictionary()
_INDEX_LOCKS_GUARD = threading.Lock()


def _index_lock(path: Path) -> threading.RLock:
    with _INDEX_LOCKS_GUARD:
        key = str(path)
        lock = _INDEX_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _INDEX_LOCKS[key] = lock
        return lock


def _is_damaged_index(exc: sqlite3.DatabaseError) -> bool:
    if isinstance(exc, _IncompatibleIndex):
        return True
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in (
        getattr(sqlite3, "SQLITE_CORRUPT", 11),
        getattr(sqlite3, "SQLITE_NOTADB", 26),
    ):
        return True
    message = str(exc).lower()
    return any(fragment in message for fragment in (
        "file is not a database", "database disk image is malformed",
        "malformed database schema",
    ))


def _discard_damaged_index(path: Path) -> None:
    # Delete only derived files. Pin the directory on POSIX so even a raced
    # parent-directory symlink cannot redirect the cleanup elsewhere.
    with _index_directory(path) as directory_fd:
        for candidate in (path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
            if directory_fd is None:
                if candidate.is_symlink():
                    raise OSError("Transcript search index must not be a symlink.")
                if candidate.exists():
                    if not candidate.is_file():
                        raise OSError("Transcript search index must be a regular file.")
                    candidate.unlink()
                continue
            try:
                info = os.stat(candidate.name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode):
                raise OSError("Transcript search index must not be a symlink.")
            if not stat.S_ISREG(info.st_mode):
                raise OSError("Transcript search index must be a regular file.")
            os.unlink(candidate.name, dir_fd=directory_fd)


def _source_stamp(stats: os.stat_result) -> tuple[int, int, int, int, int]:
    # Compare fstat snapshots only: Windows path.stat can report birth time
    # as st_ctime_ns while fstat reports the last metadata-change time.
    return (stats.st_dev, stats.st_ino, stats.st_size, stats.st_mtime_ns, stats.st_ctime_ns)


def _cached_checkpoint(path: Path) -> _HashCheckpoint | None:
    key = str(path)
    with _HASH_CHECKPOINT_LOCK:
        checkpoint = _HASH_CHECKPOINTS.get(key)
        if checkpoint is not None:
            _HASH_CHECKPOINTS.move_to_end(key)
        return checkpoint


def _remember_checkpoint(path: Path, checkpoint: _HashCheckpoint) -> None:
    key = str(path)
    with _HASH_CHECKPOINT_LOCK:
        _HASH_CHECKPOINTS[key] = checkpoint
        _HASH_CHECKPOINTS.move_to_end(key)
        if len(_HASH_CHECKPOINTS) > _MAX_HASH_CHECKPOINTS:
            _HASH_CHECKPOINTS.popitem(last=False)


def _prefix_hasher(source, length: int):
    """Return SHA-256 state for committed bytes, reading in bounded chunks."""
    source.seek(0)
    digest = hashlib.sha256()
    remaining = length
    while remaining:
        chunk = source.read(min(remaining, 64 * 1024))
        if not chunk:
            raise OSError("Transcript changed while updating its search index.")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest


def _sync_index_once(transcript: Path, *, append_origin: os.stat_result | None = None) -> None:
    """Index complete JSONL records not yet committed to this sidecar.

    append_origin is the transcript stat taken immediately before this process's
    writer appended a batch. If it matches our last indexed snapshot, the old
    bytes could not have been changed *before* that append: resume the cached
    SHA-256 state and hash only newly read complete lines. External writes,
    replacements, restarts and mismatched metadata still validate the prefix.
    Offsets, FTS rows and the SHA-256 digest commit in one transaction.
    """
    if transcript.is_symlink():
        raise OSError("Transcript must not be a symlink for indexing.")
    if not transcript.is_file():
        return
    sidecar = index_path(transcript)
    with closing(_open_index(sidecar)) as db:
        with transcript.open("rb") as source, db:
            row = db.execute(
                "SELECT offset, line_number FROM transcript_index_state WHERE id = 1"
            ).fetchone()
            offset, line_number = (int(row[0]), int(row[1])) if row else (0, 0)
            identity = db.execute(
                "SELECT device, inode, prefix_sha256 "
                "FROM transcript_index_source WHERE id = 1"
            ).fetchone()
            stats = os.fstat(source.fileno())
            stamp = _source_stamp(stats)
            source.seek(0, 2)
            size = source.tell()
            device, inode = str(stats.st_dev), str(stats.st_ino)

            rebuild = offset > size or (
                offset > 0 and (
                    identity is None or
                    (identity[0], identity[1]) != (device, inode)
                )
            )
            hasher = hashlib.sha256()
            if offset and not rebuild:
                checkpoint = _cached_checkpoint(sidecar)
                reusable = (
                    checkpoint is not None and
                    checkpoint.offset == offset and
                    (checkpoint.device, checkpoint.inode, checkpoint.prefix_sha256)
                    == (device, inode, identity[2]) and
                    (checkpoint.stamp == stamp or (
                        append_origin is not None and
                        checkpoint.stamp == _source_stamp(append_origin)
                    ))
                )
                hasher = (
                    checkpoint.hasher.copy() if reusable
                    else _prefix_hasher(source, offset)
                )
                if hasher.hexdigest() != identity[2]:
                    rebuild = True

            if rebuild:
                # The transcript, not the disposable sidecar, is authoritative.
                db.execute("DELETE FROM transcript_fts")
                offset, line_number = 0, 0
                hasher = hashlib.sha256()
            source.seek(offset)

            while True:
                raw = source.readline(_MAX_INDEX_LINE_BYTES + 1)
                if not raw:
                    break
                # A digest copy ensures an incomplete/oversized trailing line
                # never enters the stored prefix hash or committed offset.
                line_hasher = hasher.copy()
                line_hasher.update(raw)
                if not raw.endswith(b"\n"):
                    if len(raw) <= _MAX_INDEX_LINE_BYTES:
                        break  # incomplete trailing record; retry next sync
                    while raw and not raw.endswith(b"\n"):
                        raw = source.readline(_MAX_INDEX_LINE_BYTES + 1)
                        if raw:
                            line_hasher.update(raw)
                    if not raw:
                        break
                    raw = b""  # consumed but intentionally not indexed
                hasher = line_hasher
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

            digest = hasher.hexdigest()
            db.execute(
                "INSERT INTO transcript_index_state (id, offset, line_number) "
                "VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "offset=excluded.offset, line_number=excluded.line_number",
                (offset, line_number),
            )
            db.execute(
                "INSERT INTO transcript_index_source (id, device, inode, prefix_sha256) "
                "VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "device=excluded.device, inode=excluded.inode, "
                "prefix_sha256=excluded.prefix_sha256",
                (device, inode, digest),
            )
            # Do not trust this checkpoint if the source was concurrently
            # modified during indexing; the next sync will revalidate it.
            stable = _source_stamp(os.fstat(source.fileno())) == stamp
        # Commit in memory before publishing a complete SQLite image. SQLite
        # itself never opens the scratch pathname or creates WAL/SHM siblings.
        _save_index(sidecar, db)
        if stable:
            _remember_checkpoint(sidecar, _HashCheckpoint(
                offset, device, inode, digest, stamp, hasher,
            ))


def sync_index(transcript: Path, *, append_origin: os.stat_result | None = None) -> None:
    """Rebuild an unreadable disposable index once from authoritative JSONL."""
    sidecar = index_path(transcript)
    with _index_lock(sidecar):
        try:
            _sync_index_once(transcript, append_origin=append_origin)
        except sqlite3.DatabaseError as exc:
            if not _is_damaged_index(exc):
                raise
            _discard_damaged_index(sidecar)
            _sync_index_once(transcript)


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
    sidecar = index_path(transcript)
    with _index_lock(sidecar):
        # Also recover corruption detected only while querying FTS pages.
        for attempt in range(2):
            if attempt:
                _discard_damaged_index(sidecar)
            sync_index(transcript)
            try:
                with closing(_open_index(sidecar)) as db:
                    rows = db.execute(
                        "SELECT role, line_number, "
                        "snippet(transcript_fts, 0, '[', ']', '…', 24) "
                        "FROM transcript_fts WHERE transcript_fts MATCH ? "
                        "ORDER BY bm25(transcript_fts), rowid DESC LIMIT ?",
                        (match, limit),
                    ).fetchall()
                break
            except sqlite3.DatabaseError as exc:
                if attempt or not _is_damaged_index(exc):
                    raise
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
