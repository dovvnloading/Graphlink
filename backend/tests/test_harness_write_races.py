"""Regressions for approval-bound workspace file publishing (#751, #752)."""

from __future__ import annotations

import asyncio
import errno
import os

import pytest

from backend.harness import tools_fs as fs
from backend.harness.workspace import WorkspaceError
from backend.providers.base import ToolCall
from backend.tools import FS_WRITE, RunContext, ToolRegistry


def _approved_file_call(root, name, **arguments):
    """Approve the actual preview at the registry gate, not a pre-seeded state."""
    call = ToolCall(id="approved", name=name, arguments=arguments)
    registry = ToolRegistry()
    fs.register_harness_fs_tools(registry)
    ctx = RunContext(granted_scopes=frozenset({FS_WRITE}), request_approval=None)

    async def approve(disclosed_call):
        preview = fs.preview_harness_file_change(disclosed_call, root)
        assert preview.available and preview.state is not None
        ctx.harness_file_approval_states[call.id] = preview.state
        return True

    ctx.request_approval = approve
    ctx.harness_file_approval_states = {}
    ctx.harness_workspace_dir = root
    return asyncio.run(registry.invoke(call, ctx))


def test_approved_new_file_is_created_when_hard_links_are_unsupported(tmp_path, monkeypatch):
    def unsupported_link(_source, _target):
        raise OSError(errno.EOPNOTSUPP, "hard links not supported")

    monkeypatch.setattr(fs.os, "link", unsupported_link)
    result = _approved_file_call(tmp_path, "fs.write", path="new.txt", content="approved\ncontent\n")

    assert not result.is_error, result.content
    assert (tmp_path / "new.txt").read_bytes() == b"approved\ncontent\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.txt"]


def test_linkless_creation_still_refuses_a_raced_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "new.txt"

    def unsupported_link(_source, _target):
        raise OSError(errno.EPERM, "hard links disabled")

    create_exclusively = fs._create_exclusively_without_links

    def external_create_before_exclusive_open(staged, destination):
        destination.write_text("external winner", encoding="utf-8")
        return create_exclusively(staged, destination)

    monkeypatch.setattr(fs.os, "link", unsupported_link)
    monkeypatch.setattr(fs, "_create_exclusively_without_links", external_create_before_exclusive_open)

    with pytest.raises(WorkspaceError, match="File changed since approval"):
        fs._write_atomic_text(target, "agent lost", approval_state=fs.FileApprovalState(target, None, None))

    assert target.read_text(encoding="utf-8") == "external winner"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.txt"]


@pytest.mark.parametrize("operation", ["fs.write", "fs.edit"])
def test_approved_overwrite_preserves_external_in_place_edit_at_commit(tmp_path, monkeypatch, operation):
    target = tmp_path / "reviewed.txt"
    target.write_text("reviewed original", encoding="utf-8")
    exchange = fs._exchange_approved_file
    raced = False

    def edit_just_before_commit(destination, staged):
        nonlocal raced
        if not raced:
            raced = True
            destination.write_text("external replacement", encoding="utf-8")
        return exchange(destination, staged)

    monkeypatch.setattr(fs, "_exchange_approved_file", edit_just_before_commit)
    arguments = (
        {"path": "reviewed.txt", "content": "agent version"}
        if operation == "fs.write"
        else {"path": "reviewed.txt", "old_string": "reviewed", "new_string": "agent"}
    )
    result = _approved_file_call(tmp_path, operation, **arguments)

    assert raced
    assert result.is_error and "File changed since approval" in result.content
    assert target.read_text(encoding="utf-8") == "external replacement"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reviewed.txt"]


def test_approved_overwrite_preserves_atomic_external_rename_at_commit(tmp_path, monkeypatch):
    target = tmp_path / "reviewed.txt"
    target.write_text("reviewed original", encoding="utf-8")
    exchange = fs._exchange_approved_file
    raced = False

    def rename_just_before_commit(destination, staged):
        nonlocal raced
        if not raced:
            raced = True
            external = tmp_path / "external.txt"
            external.write_text("external replacement", encoding="utf-8")
            os.replace(external, destination)
        return exchange(destination, staged)

    monkeypatch.setattr(fs, "_exchange_approved_file", rename_just_before_commit)
    result = _approved_file_call(tmp_path, "fs.write", path="reviewed.txt", content="agent version")

    assert raced
    assert result.is_error and "File changed since approval" in result.content
    assert target.read_text(encoding="utf-8") == "external replacement"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reviewed.txt"]


@pytest.mark.parametrize("operation", ["fs.write", "fs.edit"])
def test_unchanged_approved_overwrite_still_succeeds(tmp_path, operation):
    target = tmp_path / "reviewed.txt"
    target.write_text("reviewed original", encoding="utf-8")
    arguments = (
        {"path": "reviewed.txt", "content": "agent version"}
        if operation == "fs.write"
        else {"path": "reviewed.txt", "old_string": "reviewed", "new_string": "agent"}
    )

    result = _approved_file_call(tmp_path, operation, **arguments)

    assert not result.is_error, result.content
    assert target.read_text(encoding="utf-8") == (
        "agent version" if operation == "fs.write" else "agent original"
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == ["reviewed.txt"]


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX no-follow directory handles")
@pytest.mark.parametrize("tool", ("fs.write", "fs.edit"))
def test_approved_publication_rejects_parent_symlink_swap_after_final_check(tmp_path, monkeypatch, tool):
    workspace = tmp_path / "workspace"
    parent = workspace / "sub"
    outside = tmp_path / "outside"
    parent.mkdir(parents=True)
    outside.mkdir()
    if tool == "fs.edit":
        (parent / "new.txt").write_text("before", encoding="utf-8")

    original_check = fs._file_approval_state_matches
    checks = 0

    def swap_after_last_approval_check(target, approved):
        nonlocal checks
        result = original_check(target, approved)
        checks += 1
        if checks == 2:
            # The real tool first checked its pinned target, then staged the
            # complete payload and did its final approval-version check.
            # Swap the parent AFTER that check, just before publication.
            parent.rename(workspace / "sub-old")
            parent.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(fs, "_file_approval_state_matches", swap_after_last_approval_check)
    arguments = (
        {"path": "sub/new.txt", "content": "agent data"}
        if tool == "fs.write"
        else {"path": "sub/new.txt", "old_string": "before", "new_string": "after"}
    )
    result = _approved_file_call(workspace, tool, **arguments)

    assert checks == 2
    assert result.is_error and "File changed since approval" in result.content
    assert not (outside / "new.txt").exists(), "approved writes must never follow the swapped parent"
    old_target = workspace / "sub-old" / "new.txt"
    if tool == "fs.edit":
        assert old_target.read_text(encoding="utf-8") == "before"
    else:
        assert not old_target.exists()
    assert not list((workspace / "sub-old").glob(".*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX no-follow directory handles")
def test_parent_symlink_swap_before_staging_does_not_touch_outside(tmp_path, monkeypatch):
    from contextlib import contextmanager

    workspace = tmp_path / "workspace"
    parent = workspace / "sub"
    outside = tmp_path / "outside"
    parent.mkdir(parents=True)
    outside.mkdir()
    original_pin = fs._pinned_write_target

    @contextmanager
    def swap_before_pin(root, target):
        parent.rename(workspace / "sub-old")
        parent.symlink_to(outside, target_is_directory=True)
        with original_pin(root, target) as pinned:
            yield pinned

    monkeypatch.setattr(fs, "_pinned_write_target", swap_before_pin)
    result = _approved_file_call(workspace, "fs.write", path="sub/new.txt", content="agent data")

    assert result.is_error
    assert not (outside / "new.txt").exists()
    assert not (workspace / "sub-old" / "new.txt").exists()
