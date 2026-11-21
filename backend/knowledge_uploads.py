"""Browser drag-and-drop uploads, staged safely before PR 889's file ingestion job.

A browser File does not expose a trustworthy absolute source path. Each
upload is sent in small base64 chunks, validated against the same file/job
limits as native picker imports, and kept under Graphlink's private
knowledge directory so the user can re-index it later. No client supplied
path is ever interpreted as a filesystem location.
"""

from __future__ import annotations

import base64
import binascii
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from backend.knowledge_ingest import IngestError, is_supported_knowledge_file
from backend.knowledge_jobs import MAX_FILE_BYTES, MAX_JOB_BYTES, MAX_JOB_FILES

UPLOAD_CHUNK_BYTES = 192 * 1024


def _ensure_private_upload_dir(path: Path) -> None:
    """Do not expose staged filenames or redirect uploads via an existing link."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or path.is_symlink():
        raise IngestError("Knowledge upload directory is redirected by a symlink.")
    if os.name != "nt":
        path.parent.chmod(0o700)
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink():
        raise IngestError("Knowledge upload directory is redirected by a symlink.")
    if os.name != "nt":
        path.chmod(0o700)


@dataclass
class _File:
    name: str
    size: int
    path: Path
    received: int = 0


@dataclass
class _Session:
    upload_id: str
    directory: Path
    workspace_id: int | None
    files: list[_File]


class KnowledgeDropUploads:
    """Single in-progress upload per websocket session, no arbitrary file paths."""

    def __init__(self, db_path: Path):
        self._staging = db_path.parent / "upload-staging"
        self._sources = db_path.parent / "uploads"
        self._session: _Session | None = None
        self._lock = threading.RLock()

    def begin(self, files: list[dict], *, workspace_id: int | None) -> dict:
        if not isinstance(files, list) or not 0 < len(files) <= MAX_JOB_FILES:
            raise IngestError("Select between 1 and 10,000 supported files.")
        names: list[tuple[str, int]] = []
        total = 0
        for spec in files:
            if not isinstance(spec, dict):
                raise IngestError("Invalid dropped file.")
            name, size = spec.get("name"), spec.get("size")
            if (
                not isinstance(name, str) or not name or len(name) > 255
                or name.startswith(".") or name in {".", ".."}
                or "/" in name or "\\" in name or ":" in name
                or not is_supported_knowledge_file(Path(name))
                or type(size) is not int or not 0 <= size <= MAX_FILE_BYTES
            ):
                raise IngestError("Unsupported filename or file size (25 MiB max per file).")
            names.append((name, size))
            total += size
            if total > MAX_JOB_BYTES:
                raise IngestError("Import exceeds the 500 MiB job limit.")

        with self._lock:
            if self._session is not None:
                raise IngestError("A file drop is already uploading.")
            upload_id = uuid4().hex
            _ensure_private_upload_dir(self._staging)
            directory = self._staging / upload_id
            directory.mkdir(mode=0o700)
            try:
                entries: list[_File] = []
                for index, (name, size) in enumerate(names):
                    item_dir = directory / str(index)
                    item_dir.mkdir(mode=0o700)
                    path = item_dir / name
                    path.touch(mode=0o600, exist_ok=False)
                    entries.append(_File(name, size, path))
                self._session = _Session(upload_id, directory, workspace_id, entries)
            except BaseException:
                shutil.rmtree(directory, ignore_errors=True)
                raise
            return {"uploadId": upload_id, "filesTotal": len(entries), "bytesTotal": total}

    def append(self, upload_id: str, index: int, data: str) -> dict:
        if not isinstance(data, str) or len(data) > (UPLOAD_CHUNK_BYTES * 4 // 3 + 8):
            raise IngestError("Upload chunk exceeds the transfer limit.")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise IngestError("Invalid file upload data.") from exc
        if not decoded or len(decoded) > UPLOAD_CHUNK_BYTES:
            raise IngestError("Empty or oversized upload chunk.")

        with self._lock:
            session = self._session
            if session is None or session.upload_id != upload_id:
                raise IngestError("Upload session is no longer active.")
            if type(index) is not int or not 0 <= index < len(session.files):
                raise IngestError("Invalid upload file index.")
            entry = session.files[index]
            if entry.received + len(decoded) > entry.size:
                raise IngestError("Uploaded bytes exceed the declared file size.")
            with entry.path.open("ab") as file:
                file.write(decoded)
            entry.received += len(decoded)
            return {"bytesReceived": sum(item.received for item in session.files)}

    def finish(self, upload_id: str) -> tuple[list[str], int | None]:
        with self._lock:
            session = self._session
            if session is None or session.upload_id != upload_id:
                raise IngestError("Upload session is no longer active.")
            if any(entry.received != entry.size for entry in session.files):
                raise IngestError("Upload is incomplete.")
            _ensure_private_upload_dir(self._sources)
            destination = self._sources / session.upload_id
            session.directory.rename(destination)
            self._session = None
            return [
                str(destination / str(index) / entry.name)
                for index, entry in enumerate(session.files)
            ], session.workspace_id

    def cancel(self, upload_id: str) -> dict:
        with self._lock:
            session = self._session
            if session is None or session.upload_id != upload_id:
                return {"cancelled": False}
            self._session = None
            shutil.rmtree(session.directory, ignore_errors=True)
            return {"cancelled": True}

    def cleanup_source(self, source_uri: str) -> None:
        """Remove only owned upload copies, never native-picker source files."""
        source = Path(source_uri)
        if self._sources.is_symlink() or self._sources.parent.is_symlink():
            return
        root = self._sources.resolve()
        try:
            resolved = source.resolve(strict=True)
            if not resolved.is_relative_to(root) or len(resolved.relative_to(root).parts) != 3:
                return
            resolved.unlink()
            resolved.parent.rmdir()
            # Other files in the same drop must remain available.
            try:
                resolved.parent.parent.rmdir()
            except OSError:
                pass
        except OSError:
            pass
