"""One OS-enforced owner for each Graphlink data directory.

The desktop and headless launchers must not build independent in-memory
SceneDocuments against the same chats.db: each process would have its own
autosave state and optimistic-concurrency snapshot. A distinct TCP port does
not make the database safe to share. Locking by the canonical DB *directory*
also covers separate chats.db paths whose adjacent asset store is shared.

The guard locks a stable companion file (not chats.db itself: SQLite owns
its own locking and WAL protocol) for the ENTIRE application lifetime.
The file is deliberately NEVER unlinked. Removing it on exit could create
two different inodes and let a second process acquire a different lock while
the first still holds the original. It is not the crash-detection sentinel
backend/crash_recovery.py calls running.lock.

Locks are kernel-owned: a kill, crash, or power loss releases the lock when
the process closes, without a stale-PID check or manually breaking a file.
Works with fcntl.flock on POSIX and msvcrt.locking on Windows; no dependency.
"""

from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class DataDirectoryInUse(RuntimeError):
    """A running Graphlink process already owns this database."""


def lock_path_for(db_path: Path | str) -> Path:
    """Canonicalize the DB parent shared by SQLite, assets, and backups."""
    path = Path(db_path).expanduser().resolve()
    return path.parent / ".graphlink.instance.lock"


def _try_lock(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        # Lock one byte non-blockingly, never spin waiting for the GUI to
        # exit. The placeholder byte is installed before the lock attempt.
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _is_contention(exc: OSError) -> bool:
    # Windows' msvcrt.locking may report EACCES or the Win32 lock-violation
    # codes; POSIX flock typically reports EAGAIN/EWOULDBLOCK.
    return exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK) or (
        getattr(exc, "winerror", None) in (33, 36)
    )


@contextmanager
def exclusive_data_directory(db_path: Path | str) -> Iterator[Path]:
    """Claim the data directory for one running process or refuse immediately.

    The caller must keep this context OPEN for the entire backend lifetime.
    Permission/filesystem errors fail closed; only real lock contention is
    converted to DataDirectoryInUse. No database file is created or opened
    by this guard.
    """
    lock_path = lock_path_for(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if lock_path.is_symlink():
        raise OSError(f"refusing symlink used as a Graphlink instance lock: {lock_path}")

    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    # O_NOFOLLOW also closes the check/open symlink race on POSIX.
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    locked = False
    try:
        if os.name == "nt" and os.fstat(fd).st_size == 0:
            os.write(fd, b"\x00")
        try:
            _try_lock(fd)
        except OSError as exc:
            if _is_contention(exc):
                raise DataDirectoryInUse(
                    f"another Graphlink process is already using {lock_path.parent}; "
                    "close it before starting this backend"
                ) from exc
            raise
        locked = True
        if os.name != "nt":
            # A pre-existing stale file may have weak mode bits. Tighten
            # permissions only AFTER obtaining ownership of the inode.
            os.fchmod(fd, 0o600)
        yield lock_path
    finally:
        if locked:
            try:
                _unlock(fd)
            finally:
                os.close(fd)
        else:
            os.close(fd)
