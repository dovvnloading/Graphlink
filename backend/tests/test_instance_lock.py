"""Cross-process backend ownership for both desktop and headless Graphlink.

These tests exercise the real OS file locks, not an in-process dictionary.
The subprocess probe is deliberately nonblocking so failures cannot hang CI.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.instance_lock import (
    DataDirectoryInUse,
    exclusive_data_directory,
    lock_path_for,
)
from graphlink_process_env import safe_subprocess_env

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE_SCRIPT = """
import sys
from backend.instance_lock import DataDirectoryInUse, exclusive_data_directory

try:
    with exclusive_data_directory(sys.argv[1]):
        sys.stdout.write("ACQUIRED\\n")
except DataDirectoryInUse:
    sys.stdout.write("BUSY\\n")
    sys.exit(3)
"""


def _probe(db_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT, str(db_path)],
        cwd=REPO_ROOT,
        env=safe_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_second_process_cannot_claim_data_directory_until_owner_exits(tmp_path):
    db = tmp_path / "chats.db"
    with exclusive_data_directory(db) as path:
        assert path == tmp_path / ".graphlink.instance.lock"
        assert path.exists()
        assert not db.exists(), "acquiring an instance lock must not create the database"
        other = _probe(db)
        assert other.returncode == 3, other.stderr
        assert other.stdout.strip() == "BUSY"

        with pytest.raises(DataDirectoryInUse, match="already using"):
            with exclusive_data_directory(db):
                pass

    later = _probe(db)
    assert later.returncode == 0, later.stderr
    assert later.stdout.strip() == "ACQUIRED"
    assert path.exists(), "retain the inode; unlinking creates a race with waiters"


def test_different_database_names_in_same_directory_share_the_lock(tmp_path):
    first = tmp_path / "chats.db"
    second = tmp_path / "another.db"
    assert lock_path_for(first) == lock_path_for(second)
    with exclusive_data_directory(first):
        blocked = _probe(second)
        assert blocked.returncode == 3, blocked.stderr
    assert _probe(second).returncode == 0


def test_different_data_directories_can_launch_independently(tmp_path):
    first = tmp_path / "one" / "chats.db"
    second = tmp_path / "two" / "chats.db"
    with exclusive_data_directory(first):
        running = _probe(second)
        assert running.returncode == 0, running.stderr


def test_crashed_process_does_not_leave_a_stale_lock(tmp_path):
    db = tmp_path / "chats.db"
    # Deliberately bypass Python finally/context-manager teardown. The OS
    # must drop the lock when the child terminates, even though the file
    # persists on disk.
    script = """
import os
import sys
from backend.instance_lock import exclusive_data_directory
with exclusive_data_directory(sys.argv[1]):
    os._exit(0)
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(db)],
        cwd=REPO_ROOT, env=safe_subprocess_env(),
        text=True, capture_output=True, timeout=15, check=False,
    )
    assert crashed.returncode == 0, crashed.stderr
    assert lock_path_for(db).exists()
    following = _probe(db)
    assert following.returncode == 0, following.stderr


def test_symlink_lock_file_is_refused_without_touching_its_target(tmp_path):
    db = tmp_path / "chats.db"
    target = tmp_path / "victim.txt"
    target.write_text("unchanged", encoding="utf-8")
    link = lock_path_for(db)
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("creating file symlinks is not supported here")
    with pytest.raises(OSError, match="symlink"):
        with exclusive_data_directory(db):
            pass
    assert target.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.posix_permissions
def test_posix_lock_file_mode_is_private_and_repaired_when_reused(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX file mode is not the Windows ACL system")
    db = tmp_path / "chats.db"
    path = lock_path_for(db)
    path.write_bytes(b"")
    os.chmod(path, 0o666)
    with exclusive_data_directory(db):
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.is_file()
    assert path.stat().st_mode & 0o777 == 0o600
