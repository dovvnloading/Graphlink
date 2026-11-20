"""Approved fs.write/fs.edit must not destroy concurrent filesystem edits."""

from __future__ import annotations

import errno
import os
import shutil
import stat

import pytest

from backend.harness import tools_fs as fs
from backend.harness.workspace import WorkspaceError
from backend.providers.base import ToolCall


def _approved(root, content="agent"):
    call = ToolCall(
        id="approved", name="fs.write",
        arguments={"path": "reviewed.txt", "content": content},
    )
    preview = fs.preview_harness_file_change(call, root)
    assert preview.available and preview.state is not None
    return preview.state


@pytest.mark.skipif(os.name == "nt", reason="POSIX rename exchange")
def test_failed_rollback_keeps_displaced_external_file(tmp_path, monkeypatch):
    target = tmp_path / "reviewed.txt"
    target.write_text("original")
    approval = _approved(tmp_path)
    exchange = fs._exchange_approved_file
    calls = 0

    def fail_second_exchange(destination, staged):
        nonlocal calls
        calls += 1
        if calls == 1:
            destination.write_text("external edit")
            return exchange(destination, staged)
        raise OSError(errno.EIO, "rollback failed")

    monkeypatch.setattr(fs, "_exchange_approved_file", fail_second_exchange)
    with pytest.raises(WorkspaceError, match="Automatic restoration failed") as exc:
        fs._write_atomic_text(target, "agent", approval_state=approval)

    assert calls == 2
    assert target.read_text() == "agent"
    saved = list(tmp_path.glob(".reviewed.txt.*.tmp"))
    assert len(saved) == 1
    assert saved[0].read_text() == "external edit"
    assert str(saved[0]) in str(exc.value)
    assert "unapproved agent version" in str(exc.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX chmod and rename exchange")
def test_raced_chmod_rejects_approval_without_reverting_permissions(tmp_path, monkeypatch):
    target = tmp_path / "reviewed.txt"
    target.write_text("original")
    target.chmod(0o644)
    approval = _approved(tmp_path)
    exchange = fs._exchange_approved_file
    called = False

    def chmod_at_commit(destination, staged):
        nonlocal called
        if not called:
            called = True
            destination.chmod(0o600)
        return exchange(destination, staged)

    monkeypatch.setattr(fs, "_exchange_approved_file", chmod_at_commit)
    with pytest.raises(WorkspaceError, match="File changed since approval"):
        fs._write_atomic_text(target, "agent", approval_state=approval)

    assert called
    assert target.read_text() == "original"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reviewed.txt"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX rename exchange")
def test_recovery_never_overwrites_a_post_exchange_rename(tmp_path, monkeypatch):
    target = tmp_path / "reviewed.txt"
    target.write_text("original")
    approval = _approved(tmp_path)
    exchange = fs._exchange_approved_file
    matches = fs._displaced_file_matches_approval
    calls = 0

    def raced_exchange(destination, staged):
        nonlocal calls
        calls += 1
        if calls == 1:
            destination.write_text("external-B")
        return exchange(destination, staged)

    def newest_after_exchange(displaced, approved):
        new_path = tmp_path / "newest.tmp"
        new_path.write_text("newest-C")
        os.replace(new_path, target)
        return matches(displaced, approved)

    monkeypatch.setattr(fs, "_exchange_approved_file", raced_exchange)
    monkeypatch.setattr(fs, "_displaced_file_matches_approval", newest_after_exchange)
    with pytest.raises(WorkspaceError, match="newer destination was left untouched") as exc:
        fs._write_atomic_text(target, "agent", approval_state=approval)

    assert calls == 1, "a stale recovery must not attempt a second blind exchange"
    assert target.read_text() == "newest-C"
    saved = list(tmp_path.glob(".reviewed.txt.*.tmp"))
    assert len(saved) == 1
    assert saved[0].read_text() == "external-B"
    assert str(saved[0]) in str(exc.value)


def test_unsupported_hard_links_use_complete_atomic_publication(tmp_path, monkeypatch):
    target = tmp_path / "new.txt"
    payload = "complete payload\n" * 1000
    state = fs.FileApprovalState(target, None, None)

    def no_links(_source, _destination):
        raise OSError(errno.EOPNOTSUPP, "links unsupported")

    def no_partial_copy(*_args, **_kwargs):
        raise AssertionError("copying into the destination would expose partial bytes")

    original_publish = fs._create_exclusively_without_links

    def check_before_publish(staged, destination):
        assert not destination.exists()
        assert staged.read_text() == payload
        result = original_publish(staged, destination)
        assert destination.read_text() == payload
        return result

    monkeypatch.setattr(fs.os, "link", no_links)
    monkeypatch.setattr(shutil, "copyfileobj", no_partial_copy)
    monkeypatch.setattr(fs, "_create_exclusively_without_links", check_before_publish)
    fs._write_atomic_text(target, payload, approval_state=state)
    assert target.read_text() == payload
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.txt"]


def test_no_atomic_no_replace_fails_closed_without_publishing(tmp_path, monkeypatch):
    target = tmp_path / "new.txt"

    def no_links(_source, _destination):
        raise OSError(errno.EOPNOTSUPP, "links unsupported")

    def no_atomic_rename(_staged, _target):
        raise WorkspaceError("atomic no-replace unavailable")

    monkeypatch.setattr(fs.os, "link", no_links)
    monkeypatch.setattr(fs, "_create_exclusively_without_links", no_atomic_rename)
    with pytest.raises(WorkspaceError, match="atomic no-replace unavailable"):
        fs._write_atomic_text(
            target, "unpublished", approval_state=fs.FileApprovalState(target, None, None),
        )
    assert not list(tmp_path.iterdir())
