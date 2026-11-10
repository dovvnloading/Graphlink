"""Gitlink review-bound Apply: optimistic conflict checks with real disk files.

No mock of apply_change_set: these cover the same byte-exact writes and
rollback paths used by the desktop plugin. API apply requires baselines;
the legacy direct helper retains its explicit opt-out for older callers.
"""
from __future__ import annotations

import hashlib

import pytest

from backend.agents import _build_gitlink_preview_text, _call_gitlink_apply, _fingerprint_changes
from graphlink_plugins.gitlink.change_review import (
    BASELINE_FIELD,
    MAX_REVIEW_FILE_BYTES,
    MAX_REVIEW_FILES,
    prepare_change_review,
    verify_change_review,
)
from graphlink_plugins.gitlink.repository import apply_change_set


def test_prepare_binds_byte_exact_originals_for_create_update_and_delete(tmp_path):
    (tmp_path / "a.py").write_bytes(b"old\r\n")
    (tmp_path / "remove.txt").write_bytes(b"gone")
    proposal = [
        {"path": "a.py", "operation": "update", "content": "new\n"},
        {"path": "new.py", "operation": "create", "content": "created"},
        {"path": "remove.txt", "operation": "delete"},
    ]

    reviewed, originals = prepare_change_review(proposal, str(tmp_path))
    assert reviewed is not proposal
    assert BASELINE_FIELD not in proposal[0]  # never mutate the model's result
    assert reviewed[0][BASELINE_FIELD] == hashlib.sha256(b"old\r\n").hexdigest()
    assert reviewed[1][BASELINE_FIELD] is None
    assert reviewed[2][BASELINE_FIELD] == hashlib.sha256(b"gone").hexdigest()
    assert originals == {"a.py": b"old\r\n", "new.py": None, "remove.txt": b"gone"}
    assert _fingerprint_changes(reviewed) != _fingerprint_changes(proposal)

    snapshots = verify_change_review(tmp_path, reviewed)
    assert [row.path for row in snapshots] == ["a.py", "new.py", "remove.txt"]
    assert [row.original for row in snapshots] == [b"old\r\n", None, b"gone"]


def test_reviewed_apply_writes_original_approved_content_and_deletes(tmp_path):
    (tmp_path / "a.py").write_bytes(b"old")
    (tmp_path / "b.py").write_bytes(b"remove")
    proposed = [
        {"path": "a.py", "operation": "update", "content": "new\r\n"},
        {"path": "new/c.py", "operation": "create", "content": "made\n"},
        {"path": "b.py", "operation": "delete"},
    ]
    reviewed, _ = prepare_change_review(proposed, str(tmp_path))

    assert _call_gitlink_apply(tmp_path, reviewed) == 3
    assert (tmp_path / "a.py").read_bytes() == b"new\r\n"
    assert (tmp_path / "new/c.py").read_bytes() == b"made\n"
    assert not (tmp_path / "b.py").exists()


@pytest.mark.parametrize("path,new_contents", [
    ("a.py", b"edited after approval"),
    ("b.py", b"edited after approval"),
])
def test_any_stale_file_aborts_entire_set_before_first_write(tmp_path, path, new_contents):
    (tmp_path / "a.py").write_bytes(b"A")
    (tmp_path / "b.py").write_bytes(b"B")
    pending, _ = prepare_change_review([
        {"path": "a.py", "operation": "update", "content": "new A"},
        {"path": "b.py", "operation": "update", "content": "new B"},
    ], str(tmp_path))
    (tmp_path / path).write_bytes(new_contents)

    with pytest.raises(RuntimeError, match="changed since the preview"):
        _call_gitlink_apply(tmp_path, pending)

    assert (tmp_path / path).read_bytes() == new_contents
    assert (tmp_path / ("b.py" if path == "a.py" else "a.py")).read_bytes() == (
        b"B" if path == "a.py" else b"A"
    )


def test_new_file_appearing_after_preview_aborts_all_updates(tmp_path):
    (tmp_path / "a.py").write_text("keep me")
    pending, _ = prepare_change_review([
        {"path": "a.py", "operation": "update", "content": "overwrite me"},
        {"path": "b.py", "operation": "create", "content": "ours"},
    ], str(tmp_path))
    (tmp_path / "b.py").write_text("someone else's new file")

    with pytest.raises(RuntimeError, match="changed since the preview"):
        _call_gitlink_apply(tmp_path, pending)

    assert (tmp_path / "a.py").read_text() == "keep me"
    assert (tmp_path / "b.py").read_text() == "someone else's new file"


def test_deleted_or_renamed_original_aborts_and_does_not_recreate_it(tmp_path):
    (tmp_path / "a.py").write_text("keep")
    pending, _ = prepare_change_review(
        [{"path": "a.py", "operation": "update", "content": "new"}], str(tmp_path)
    )
    (tmp_path / "a.py").unlink()
    with pytest.raises(RuntimeError, match="changed since the preview"):
        _call_gitlink_apply(tmp_path, pending)
    assert not (tmp_path / "a.py").exists()


def test_old_session_proposal_without_baselines_is_rejected_by_desktop_apply(tmp_path):
    (tmp_path / "a.py").write_text("don't lose me")
    with pytest.raises(RuntimeError, match="no local-file baselines"):
        _call_gitlink_apply(tmp_path, [{"path": "a.py", "operation": "update", "content": "lost"}])
    assert (tmp_path / "a.py").read_text() == "don't lose me"
    # Explicit non-UI direct callers preserve their existing behavior.
    assert apply_change_set(tmp_path, [{"path": "a.py", "operation": "update", "content": "direct"}]) == 1


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_preparation_refuses_operation_that_disagrees_with_existing_file(tmp_path, operation):
    path = tmp_path / "a.py"
    if operation == "create":
        path.write_text("existing")
    proposed = [{"path": "a.py", "operation": operation}]
    if operation != "delete":
        proposed[0]["content"] = "new"
    with pytest.raises(RuntimeError, match="already exists|local file is missing"):
        prepare_change_review(proposed, str(tmp_path))


def test_duplicate_alias_and_unknown_operation_are_rejected(tmp_path):
    (tmp_path / "a.py").write_text("original")
    with pytest.raises(RuntimeError, match="duplicate"):
        prepare_change_review([
            {"path": "a.py", "operation": "update", "content": "1"},
            {"path": "A.py", "operation": "update", "content": "2"},
        ], str(tmp_path))
    with pytest.raises(RuntimeError, match="unknown operation"):
        prepare_change_review([{"path": "a.py", "operation": "replace", "content": "x"}], str(tmp_path))


def test_safe_review_rejects_symlink_alias_into_a_local_file(tmp_path):
    (tmp_path / "original.txt").write_text("safe")
    try:
        (tmp_path / "shortcut.txt").symlink_to(tmp_path / "original.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires OS permission")
    with pytest.raises(RuntimeError, match="symlink"):
        prepare_change_review([
            {"path": "shortcut.txt", "operation": "update", "content": "unsafe"}
        ], str(tmp_path))
    assert (tmp_path / "original.txt").read_text() == "safe"


def test_safe_review_rejects_unbounded_originals_and_proposed_content(tmp_path):
    (tmp_path / "huge.txt").write_bytes(b"x" * (MAX_REVIEW_FILE_BYTES + 1))
    with pytest.raises(RuntimeError, match="size limit"):
        prepare_change_review([
            {"path": "huge.txt", "operation": "update", "content": "one"}
        ], str(tmp_path))
    with pytest.raises(RuntimeError, match="size limit"):
        prepare_change_review([
            {"path": "small.txt", "operation": "create", "content": "x" * (MAX_REVIEW_FILE_BYTES + 1)}
        ], str(tmp_path))


def test_safe_review_rejects_too_many_files_before_any_disk_read(tmp_path):
    files = [{"path": f"{i}.py", "operation": "create", "content": "x"}
             for i in range(MAX_REVIEW_FILES + 1)]
    with pytest.raises(RuntimeError, match="proposed file changes"):
        prepare_change_review(files, str(tmp_path))


def test_preview_uses_exact_original_that_was_hashed_not_a_second_file_read(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("approved source")
    files, originals = prepare_change_review([
        {"path": "a.py", "operation": "update", "content": "replacement"}
    ], str(tmp_path))
    (tmp_path / "a.py").write_text("different source")
    # Preview construction must consume the already captured original rather
    # than re-reading it and showing something different from the approval.
    import backend.agents as agents
    monkeypatch.setattr(
        agents, "read_local_repo_file",
        lambda *_: (_ for _ in ()).throw(AssertionError("unexpected second read"))
    )
    preview = _build_gitlink_preview_text(files, str(tmp_path), "owner/repo", "main",
                                          original_bytes=originals)
    assert '-"approved source"' in preview
    assert '-"different source"' not in preview


def test_no_local_checkout_can_still_generate_preview_only_but_not_apply(tmp_path):
    proposed = [{"path": "x.py", "operation": "create", "content": "x"}]
    reviewed, originals = prepare_change_review(proposed, "")
    assert originals is None and reviewed == proposed
    with pytest.raises(RuntimeError, match="no local-file baselines"):
        _call_gitlink_apply(tmp_path, reviewed)
