"""Crash-recoverable Gitlink Apply transactions (issue #532).

A reviewed multi-file Apply needs more than Python exception rollback: a
process may disappear between writes. The sibling journal contains durable
original snapshots and the proposed content hashes before any target changes.
A subsequent Gitlink read/apply rolls an interrupted batch back, but refuses
to clobber third-party edits made after the interruption.

This protects *file contents* within an owned checkout, not a cross-process
filesystem lock or an operating-system-wide transaction. Paths are validated
again when a persisted journal is reopened; no untrusted journal path is used
directly as an absolute filesystem destination.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from graphlink_plugins.gitlink.agent import _safe_local_target

logger = logging.getLogger(__name__)

# The desktop backend serializes operations in its Apply lease; also protect
# synchronous Gitlink context reads from recovering a still-active batch.
APPLY_LOCK = threading.RLock()
_MAX_JOURNAL_FILES = 128
_MAX_JOURNAL_BYTES = 64 * 1024 * 1024


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _journal_dir(local_root: str | Path) -> Path:
    root = Path(local_root).expanduser().resolve()
    return root.with_name(f".{root.name}.graphlink-apply-recovery")


def _sync_dir(path: Path) -> None:
    """Best-effort parent directory fsync after rename/unlink (POSIX)."""
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        logger.warning("could not fsync Gitlink directory %s", path)


def _safe_target(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise RuntimeError("Gitlink recovery journal contains an invalid relative path.")
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RuntimeError(f"Gitlink recovery refuses a symlink at {relative!r}.")
    return _safe_local_target(root, relative)


def atomic_replace_file(target: Path, data: bytes, original_mode: int | None = None) -> None:
    """Never truncate a live code file: fsync temp bytes and rename atomically."""
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise IsADirectoryError(f"Gitlink target is not a regular file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.graphlink-", suffix=".tmp", dir=target.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            # Preserve the pre-existing mode; newly-created source files get
            # the same standard mode as a normal Python text-file creation.
            os.chmod(temp, original_mode if original_mode is not None else 0o644)
        os.replace(temp, target)
        _sync_dir(target.parent)
    finally:
        temp.unlink(missing_ok=True)


def _discard(journal: Path) -> None:
    if not journal.exists():
        return
    # Rename first: if interrupted while recursively deleting backup files,
    # the incomplete cleanup cannot masquerade as a still-live transaction.
    finished = journal.with_name(journal.name + ".finished")
    if finished.exists():
        shutil.rmtree(finished)
    os.replace(journal, finished)
    _sync_dir(journal.parent)
    shutil.rmtree(finished)


def begin_transaction(local_root: str | Path, changes: list[dict[str, Any]]) -> None:
    """Durably capture original bytes + proposed hashes before first write."""
    root = Path(local_root).expanduser().resolve()
    journal = _journal_dir(root)
    if journal.exists():
        raise RuntimeError("An interrupted Gitlink Apply must be recovered before starting another.")
    if not changes or len(changes) > _MAX_JOURNAL_FILES:
        raise RuntimeError("Gitlink Apply has an invalid number of changes.")

    journal.mkdir(mode=0o700)
    manifest: dict[str, Any] = {"version": 1, "root": str(root), "entries": []}
    total = 0
    try:
        for index, change in enumerate(changes):
            relative = change["path"]
            original = change["original"]
            desired = change["desired"]
            mode = change["mode"]
            _safe_target(root, relative)
            total += len(original or b"") + len(desired or b"")
            if total > _MAX_JOURNAL_BYTES:
                raise RuntimeError("Gitlink Apply exceeds the durable recovery size budget.")
            backup_name = None
            if original is not None:
                backup_name = f"before-{index:03d}.bin"
                fd = os.open(journal / backup_name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(original)
                    output.flush()
                    os.fsync(output.fileno())
            manifest["entries"].append({
                "path": relative,
                "before_sha256": _digest(original) if original is not None else None,
                "after_sha256": _digest(desired) if desired is not None else None,
                "backup": backup_name,
                "mode": mode,
            })

        serialized = json.dumps(manifest, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        fd = os.open(journal / "manifest.pending", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(journal / "manifest.pending", journal / "manifest.json")
        _sync_dir(journal)
        _sync_dir(journal.parent)
    except BaseException:
        # No live paths have been changed yet; interrupted preparation cannot
        # accidentally look like an applied transaction on next launch.
        if not (journal / "manifest.json").exists():
            shutil.rmtree(journal, ignore_errors=True)
        raise


def mark_committed(local_root: str | Path) -> None:
    """Durable commit decision, then best-effort cleanup of the journal."""
    journal = _journal_dir(local_root)
    fd = os.open(journal / "committed", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(b"committed\n")
        output.flush()
        os.fsync(output.fileno())
    _sync_dir(journal)
    try:
        _discard(journal)
    except OSError:
        logger.warning("completed Gitlink transaction journal could not be removed: %s", journal)


def recover_interrupted_apply(local_root: str | Path) -> bool:
    """Rollback an interrupted Apply, refusing to overwrite unrelated edits.

    Return True if an unfinished batch was restored. A previously-committed
    journal is safe to discard without undoing the successfully applied files.
    """
    root = Path(local_root).expanduser().resolve()
    journal = _journal_dir(root)
    if not journal.exists():
        return False
    if journal.is_symlink() or not journal.is_dir():
        raise RuntimeError("Gitlink recovery path is not a safe directory.")
    if (journal / "committed").exists():
        _discard(journal)
        return False
    manifest_path = journal / "manifest.json"
    if not manifest_path.exists():
        # Preparation only: no target has been touched without a manifest.
        _discard(journal)
        return False
    if manifest_path.stat().st_size > 128 * 1024:
        raise RuntimeError("Gitlink recovery manifest is oversized; manual review required.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise RuntimeError("Gitlink recovery manifest cannot be read; manual review required.") from exc
    if (not isinstance(manifest, dict) or manifest.get("version") != 1
            or manifest.get("root") != str(root)
            or not isinstance(manifest.get("entries"), list)
            or not 1 <= len(manifest["entries"]) <= _MAX_JOURNAL_FILES):
        raise RuntimeError("Gitlink recovery manifest is invalid; manual review required.")

    to_restore: list[tuple[Path, bytes | None, int | None, str]] = []
    total = 0
    for item in manifest["entries"]:
        if not isinstance(item, dict):
            raise RuntimeError("Gitlink recovery manifest has an invalid file entry.")
        path = str(item.get("path", ""))
        target = _safe_target(root, path)
        backup_name = item.get("backup")
        expected_before = item.get("before_sha256")
        expected_after = item.get("after_sha256")
        original: bytes | None = None
        if backup_name is not None:
            if not isinstance(backup_name, str) or not backup_name.startswith("before-") or "/" in backup_name:
                raise RuntimeError("Gitlink recovery backup path is invalid.")
            backup = journal / backup_name
            if backup.is_symlink() or not backup.is_file():
                raise RuntimeError("Gitlink recovery backup is unavailable.")
            total += backup.stat().st_size
            if total > _MAX_JOURNAL_BYTES:
                raise RuntimeError("Gitlink recovery backups exceed the allowed size.")
            original = backup.read_bytes()
            if _digest(original) != expected_before:
                raise RuntimeError("Gitlink recovery backup checksum mismatch.")
        elif expected_before is not None:
            raise RuntimeError("Gitlink recovery manifest has a missing backup.")
        current = target.read_bytes() if target.is_file() else None
        current_sha = _digest(current) if current is not None else None
        if current_sha not in {expected_before, expected_after}:
            raise RuntimeError(
                f"Gitlink recovery found independent edits in {path!r}; "
                f"journal retained at {journal}. Review the files manually before retrying."
            )
        mode = item.get("mode")
        if mode is not None and (type(mode) is not int or not 0 <= mode <= 0o7777):
            raise RuntimeError("Gitlink recovery manifest contains invalid mode bits.")
        to_restore.append((target, original, mode, path))

    # Preflight every file before restoring any. If recovery itself crashes,
    # the manifest remains durable and can be replayed on next launch.
    for target, original, mode, _path in reversed(to_restore):
        if original is None:
            if target.exists():
                target.unlink()
                _sync_dir(target.parent)
        else:
            atomic_replace_file(target, original, mode)
    _discard(journal)
    return True
