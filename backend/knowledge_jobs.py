"""Read-only local file/folder ingestion jobs for a workspace's knowledge collection.

Work (including directory walks, parsers, hashing/chunking and SQLite) runs
off the websocket event loop. All writes go to Graphlink's knowledge.db,
never to the folder a user selected through a native OS picker.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from backend.attachments import AttachmentError, MAX_DOCUMENT_TEXT_CHARS
from backend.events import SessionBus
from backend.knowledge_chunking import chunk_text
from backend.knowledge_ingest import IngestError, extract_text, is_supported_knowledge_file
from backend.knowledge_store import (
    get_or_create_workspace_collection, known_watched_files, reconcile_watched_files,
    set_watched_folder_sync_result, track_watched_file, upsert_file_with_chunks,
)

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_JOB_BYTES = 500 * 1024 * 1024
MAX_JOB_FILES = 10_000
_EXCLUDED_DIRS = {
    ".git", "node_modules", "venv", ".venv", "virtualenv", ".virtualenv",
    "env", ".env", "venvs", ".venvs", "virtualenvs", "site-packages",
    "__pypackages__", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache",
    ".hg", ".svn", ".idea",
}
_FINAL = {"completed", "cancelled", "failed"}


def _excluded_path(relative: Path) -> bool:
    return any(
        part.startswith(".") or part.casefold() in _EXCLUDED_DIRS
        for part in relative.parts
    )


@dataclass
class KnowledgeIngestJob:
    job_id: str
    kind: str
    workspace_id: int | None
    watched_folder_id: int | None = None
    status: str = "queued"
    files_total: int = 0
    files_processed: int = 0
    bytes_total: int = 0
    bytes_processed: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    duplicates: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed_files: int = 0
    last_file: str = ""
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    # Worker-only scan observations include oversized files, which must not
    # be mistaken for deletions. scan_complete is false for unreadable paths.
    observed_paths: set[str] = field(default_factory=set, repr=False)
    scan_complete: bool = True
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def change(self, **updates: object) -> None:
        with self.lock:
            for key, value in updates.items():
                setattr(self, key, value)

    def increment(self, **counts: int) -> None:
        with self.lock:
            for key, value in counts.items():
                setattr(self, key, getattr(self, key) + value)

    def warn(self, message: str) -> None:
        with self.lock:
            if len(self.warnings) < 10:
                self.warnings.append(message)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "jobId": self.job_id, "kind": self.kind, "workspaceId": self.workspace_id,
                "watchedFolderId": self.watched_folder_id, "status": self.status, "filesTotal": self.files_total,
                "filesProcessed": self.files_processed,
                "bytesTotal": self.bytes_total, "bytesProcessed": self.bytes_processed,
                "added": self.added, "updated": self.updated, "removed": self.removed,
                "duplicates": self.duplicates,
                "unchanged": self.unchanged, "skipped": self.skipped,
                "failedFiles": self.failed_files, "lastFile": self.last_file,
                "error": self.error, "warnings": list(self.warnings),
                "cancelRequested": self.cancel_event.is_set(),
            }


def _collect_files(path: Path | list[Path], folder: bool, job: KnowledgeIngestJob) -> list[tuple[Path, int]]:
    """Preflight the entire batch before any store mutation.

    Resolved paths stay within the chosen folder, even for symlink entries.
    Visit each real directory once (in-folder symlink cycles cannot loop).
    Over-limit jobs fail without partially importing their early files.
    Oversized individual files are skipped, and counted in the progress.
    """
    root = None
    if folder:
        if isinstance(path, list):
            raise IngestError("A folder import accepts one selected directory.")
        root = path.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise IngestError("The selected folder is not a directory.")

    found: list[tuple[Path, int]] = []
    seen_files: set[Path] = set()
    checked = 0
    bytes_total = 0

    def add_file(candidate: Path) -> None:
        nonlocal checked, bytes_total
        if not is_supported_knowledge_file(candidate):
            return
        checked += 1
        if checked > MAX_JOB_FILES:
            raise IngestError("Import exceeds the 10,000-file job limit.")
        real = candidate.resolve(strict=True)
        if root is not None:
            if not real.is_relative_to(root) or _excluded_path(real.relative_to(root)):
                job.increment(skipped=1)
                return
        if real in seen_files:
            return
        seen_files.add(real)
        job.observed_paths.add(str(real))
        size = real.stat().st_size
        if size > MAX_FILE_BYTES:
            job.increment(skipped=1)
            job.warn(f"Skipped oversized file: {candidate.name} (25 MiB limit).")
            return
        bytes_total += size
        if bytes_total > MAX_JOB_BYTES:
            raise IngestError("Import exceeds the 500 MiB job limit.")
        found.append((real, size))

    if not folder:
        selected = path if isinstance(path, list) else [path]
        for item in selected:
            resolved = item.expanduser().resolve(strict=True)
            if not resolved.is_file():
                raise IngestError("A selected file is not a regular file.")
            add_file(resolved)
    else:
        stack = [root]
        visited: set[Path] = set()
        while stack:
            if job.cancel_event.is_set():
                return []
            current = stack.pop()
            resolved_dir = current.resolve(strict=True)
            if resolved_dir in visited or not resolved_dir.is_relative_to(root):
                continue
            visited.add(resolved_dir)
            try:
                with os.scandir(current) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name.casefold())
            except OSError as exc:
                job.change(scan_complete=False)
                job.increment(skipped=1)
                job.warn(f"Skipped unreadable directory {current.name}: {exc}")
                continue
            for entry in entries:
                if job.cancel_event.is_set():
                    return []
                candidate = Path(entry.path)
                try:
                    if entry.is_dir(follow_symlinks=True):
                        if entry.name.startswith(".") or entry.name.casefold() in _EXCLUDED_DIRS:
                            continue
                        target = candidate.resolve(strict=True)
                        if target.is_relative_to(root) and not _excluded_path(target.relative_to(root)):
                            stack.append(target)
                        else:
                            job.increment(skipped=1)
                        continue
                    if entry.name.startswith("."):
                        continue  # do not inadvertently import .env, secrets, or metadata
                    if entry.is_file(follow_symlinks=True):
                        add_file(candidate)
                except (OSError, RuntimeError) as exc:
                    job.change(scan_complete=False)
                    job.increment(skipped=1)
                    job.warn(f"Skipped unreadable item {entry.name}: {exc}")

    job.change(files_total=len(found), bytes_total=bytes_total)
    return found


class KnowledgeIngestManager:
    """One active job per session; completed jobs remain queryable on reconnect."""

    def __init__(self, bus: SessionBus, db_path: Path):
        self._bus = bus
        self._db_path = db_path
        self._jobs: dict[str, KnowledgeIngestJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_event = 0.0

    def snapshot(self) -> dict:
        return {"jobs": [job.snapshot() for job in self._jobs.values()]}

    def start(
        self, path: str | list[str], *, folder: bool, workspace_id: int | None,
        watched_folder_id: int | None = None,
    ) -> dict:
        if any(job.snapshot()["status"] not in _FINAL for job in self._jobs.values()):
            raise IngestError("An import is already running in this window.")
        # Do not accept arbitrary paths from untrusted persisted document
        # state; the API calls this only after a native picker has returned.
        for job_id, old in list(self._jobs.items()):
            if len(self._jobs) <= 10:
                break
            if old.snapshot()["status"] in _FINAL:
                del self._jobs[job_id]
        job = KnowledgeIngestJob(
            uuid4().hex, "watched-folder" if watched_folder_id is not None else ("folder" if folder else "file"),
            workspace_id, watched_folder_id=watched_folder_id,
        )
        self._jobs[job.job_id] = job
        self._loop = asyncio.get_running_loop()
        selected = [Path(value) for value in path] if isinstance(path, list) else Path(path)
        task = asyncio.create_task(asyncio.to_thread(self._run, job, selected, folder))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _, job_id=job.job_id: self._tasks.pop(job_id, None))
        self._emit(force=True)
        return {"jobId": job.job_id, "status": "queued"}

    def cancel(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise IngestError("Unknown import job.")
        if job.snapshot()["status"] not in _FINAL:
            job.cancel_event.set()
            self._emit(force=True)
        return job.snapshot()

    def _emit(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_event < 0.15:
            return
        self._last_event = now
        if self._loop is None:
            return

        def publish() -> None:
            asyncio.create_task(self._bus.publish("knowledge-ingest"))

        try:
            self._loop.call_soon_threadsafe(publish)
        except RuntimeError:
            pass  # the session/event loop was disposed during shutdown

    def _run(self, job: KnowledgeIngestJob, path: Path | list[Path], folder: bool) -> None:
        try:
            job.change(status="scanning")
            self._emit(force=True)
            files = _collect_files(path, folder, job)
            if job.cancel_event.is_set():
                job.change(status="cancelled")
                return
            job.change(status="running")
            self._emit(force=True)
            collection_id = (
                get_or_create_workspace_collection(self._db_path, job.workspace_id)
                if job.workspace_id is not None else 0
            )
            watch_id = job.watched_folder_id
            known = known_watched_files(self._db_path, watch_id, collection_id) if watch_id is not None else {}
            present = set(job.observed_paths)
            for file, planned_size in files:
                if job.cancel_event.is_set():
                    break
                try:
                    # Re-resolve paths on use: an entry that was safe during
                    # enumeration may have been swapped for an outside link.
                    actual = file.resolve(strict=True)
                    root = path.expanduser().resolve(strict=True) if folder and isinstance(path, Path) else None
                    if root is not None and not actual.is_relative_to(root):
                        raise IngestError("File now points outside the selected folder.")
                    before = actual.stat()
                    if before.st_size != planned_size:
                        raise IngestError("File changed since the import was scanned; retry the import.")
                    if before.st_size > MAX_FILE_BYTES:
                        raise IngestError("File exceeds the 25 MiB limit.")
                    if watch_id is not None and known.get(str(actual)) == (before.st_size, before.st_mtime_ns):
                        # No parser work for unchanged watched files. The cache is
                        # trusted only while its file_sources mapping still exists.
                        job.increment(unchanged=1)
                    else:
                        text, mime = extract_text(actual)
                        if actual.stat().st_size != before.st_size or actual.stat().st_mtime_ns != before.st_mtime_ns:
                            raise IngestError("File changed while being read; retry the import.")
                        if len(text) > MAX_DOCUMENT_TEXT_CHARS:
                            raise IngestError("Extracted text exceeds the indexing limit.")
                        if not text.strip():
                            raise IngestError("File contains no indexable text.")
                        chunks = chunk_text(text)
                        if not chunks:
                            raise IngestError("File contains no indexable text.")
                        if job.cancel_event.is_set():
                            break
                        result = upsert_file_with_chunks(
                            self._db_path, source_uri=str(actual), title=actual.name,
                            mime=mime, text=text, chunks=chunks, collection_id=collection_id,
                        )
                        if watch_id is not None:
                            track_watched_file(
                                self._db_path, watch_id, str(actual),
                                size_bytes=before.st_size, mtime_ns=before.st_mtime_ns,
                            )
                        key = {
                            "added": "added", "updated": "updated", "duplicate": "duplicates",
                            "unchanged": "unchanged",
                        }[result.action]
                        job.increment(**{key: 1})
                except (OSError, AttachmentError, IngestError, RuntimeError, ValueError) as exc:
                    job.increment(failed_files=1)
                    job.warn(f"{file.name}: {exc}")
                job.increment(files_processed=1, bytes_processed=planned_size)
                job.change(last_file=file.name)
                self._emit()
            if not job.cancel_event.is_set() and watch_id is not None:
                # Only unreadable entries make a scan incomplete. Oversized
                # and deliberately excluded items do not hide other deletions;
                # observed_paths also retains the names of oversized files.
                removed = reconcile_watched_files(
                    self._db_path, watch_id, collection_id, present,
                    prune=job.scan_complete,
                )
                job.increment(removed=removed)
            job.change(status="cancelled" if job.cancel_event.is_set() else "completed")
        except (OSError, IngestError, RuntimeError) as exc:
            job.change(status="cancelled" if job.cancel_event.is_set() else "failed", error=str(exc))
        except Exception:
            # Do not expose untrusted parser stack traces on the websocket.
            job.change(status="failed", error="Import failed unexpectedly. See graphlink.log.")
            import logging
            logging.getLogger(__name__).exception("knowledge import job failed")
        finally:
            if job.watched_folder_id is not None:
                row = job.snapshot()
                if row["status"] != "cancelled":
                    error = row["error"]
                    if row["failedFiles"]:
                        error = (
                            f'{row["failedFiles"]} file(s) failed: ' + "; ".join(row["warnings"][:3])
                        )
                    elif row["skipped"] and not error:
                        error = f'{row["skipped"]} item(s) skipped during watched folder scan.'
                    try:
                        set_watched_folder_sync_result(
                            self._db_path, job.watched_folder_id,
                            completed=row["status"] == "completed", error=error,
                        )
                    except Exception:
                        import logging
                        logging.getLogger(__name__).exception("could not persist watched folder sync result")
            self._emit(force=True)
