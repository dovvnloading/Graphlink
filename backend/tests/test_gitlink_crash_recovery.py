"""Crash-consistency regressions for reviewed Gitlink Apply (issue #532)."""

from __future__ import annotations

import pytest

from graphlink_plugins.gitlink.apply_transaction import (
    _journal_dir,
    atomic_replace_file,
    begin_transaction,
    recover_interrupted_apply,
)
from graphlink_plugins.gitlink.repository import apply_change_set


def _change(path: str, original: bytes | None, desired: bytes | None) -> dict:
    return {
        "path": path, "original": original, "desired": desired, "mode": 0o600,
    }


def test_crash_after_first_of_two_updates_recovers_all_original_bytes(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_bytes(b"original first\r\n")
    second.write_bytes(b"original second\n")

    begin_transaction(tmp_path, [
        _change("first.py", first.read_bytes(), b"new first\n"),
        _change("second.py", second.read_bytes(), b"new second\n"),
    ])
    atomic_replace_file(first, b"new first\n")
    # Simulates abrupt process loss: no mark_committed(), no exception handler.
    assert _journal_dir(tmp_path).exists()

    assert recover_interrupted_apply(tmp_path) is True
    assert first.read_bytes() == b"original first\r\n"
    assert second.read_bytes() == b"original second\n"
    assert not _journal_dir(tmp_path).exists()


def test_crash_after_file_create_and_delete_restores_both(tmp_path):
    gone = tmp_path / "old.py"
    created = tmp_path / "new.py"
    gone.write_bytes(b"restore me")

    begin_transaction(tmp_path, [
        _change("old.py", gone.read_bytes(), None),
        _change("new.py", None, b"new content"),
    ])
    gone.unlink()
    atomic_replace_file(created, b"new content")

    assert recover_interrupted_apply(tmp_path) is True
    assert gone.read_bytes() == b"restore me"
    assert not created.exists()


def test_recovery_refuses_to_overwrite_independent_external_edits(tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_bytes(b"original A")
    second.write_bytes(b"original B")
    begin_transaction(tmp_path, [
        _change("first.py", b"original A", b"new A"),
        _change("second.py", b"original B", b"new B"),
    ])
    atomic_replace_file(first, b"new A")
    second.write_bytes(b"unrelated editor's new B")

    with pytest.raises(RuntimeError, match="independent edits"):
        recover_interrupted_apply(tmp_path)
    assert first.read_bytes() == b"new A"
    assert second.read_bytes() == b"unrelated editor's new B"
    assert _journal_dir(tmp_path).exists()

    second.write_bytes(b"original B")
    assert recover_interrupted_apply(tmp_path) is True
    assert first.read_bytes() == b"original A"


def test_atomic_replace_failure_preserves_previous_complete_file(tmp_path, monkeypatch):
    from graphlink_plugins.gitlink import apply_transaction

    target = tmp_path / "source.py"
    target.write_bytes(b"approved original")
    actual_replace = apply_transaction.os.replace

    def interrupt(staged, destination):
        if destination == target:
            raise PermissionError("simulated rename failure")
        return actual_replace(staged, destination)

    monkeypatch.setattr(apply_transaction.os, "replace", interrupt)
    with pytest.raises(PermissionError, match="rename failure"):
        atomic_replace_file(target, b"replacement bytes")
    assert target.read_bytes() == b"approved original"
    assert not list(tmp_path.glob(".*.graphlink-*.tmp"))


def test_normal_apply_cleans_committed_journal(tmp_path):
    target = tmp_path / "file.py"
    target.write_bytes(b"before")
    assert apply_change_set(tmp_path, [{
        "path": "file.py", "operation": "update", "content": "after",
    }]) == 1
    assert target.read_bytes() == b"after"
    assert not _journal_dir(tmp_path).exists()
