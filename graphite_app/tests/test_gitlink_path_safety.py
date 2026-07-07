"""Tests for the Gitlink write-gate's security boundary.

graphite_plugin_gitlink.py has no prior test coverage even though
_normalize_repo_path / _safe_local_target are the only thing standing between an
LLM-proposed file path and a write to the user's local disk (see
doc/PLUGIN_SYSTEM_REFACTOR_PLAN.md section 2.2 / 4.4).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphite_plugins.graphite_plugin_gitlink import (
    _fingerprint_changes,
    _normalize_repo_path,
    _safe_local_target,
)


class TestNormalizeRepoPath:
    def test_plain_relative_path_is_unchanged(self):
        assert _normalize_repo_path("src/foo.py") == "src/foo.py"

    def test_backslashes_are_normalized_to_forward_slashes(self):
        assert _normalize_repo_path("src\\foo.py") == "src/foo.py"

    def test_empty_path_is_rejected(self):
        with pytest.raises(RuntimeError):
            _normalize_repo_path("")

    def test_whitespace_only_path_is_rejected(self):
        with pytest.raises(RuntimeError):
            _normalize_repo_path("   ")

    def test_parent_traversal_is_rejected(self):
        with pytest.raises(RuntimeError):
            _normalize_repo_path("../outside.py")

    def test_parent_traversal_nested_is_rejected(self):
        with pytest.raises(RuntimeError):
            _normalize_repo_path("src/../../outside.py")

    def test_leading_slash_is_stripped_to_a_relative_path(self):
        # `lstrip("/")` runs before the PurePosixPath absolute-path check, so a
        # leading-slash input never actually reaches that check as "absolute" -
        # it's silently rewritten to a relative path instead of being rejected.
        # That's still safe (containment is enforced downstream by
        # _safe_local_target), but it means the `is_absolute()` guard in
        # _normalize_repo_path is effectively unreachable for POSIX-style input.
        # This test documents the real behavior so a future refactor doesn't
        # accidentally rely on the dead branch.
        assert _normalize_repo_path("/etc/passwd") == "etc/passwd"


class TestSafeLocalTarget:
    def test_normal_path_resolves_under_root(self, tmp_path):
        target = _safe_local_target(tmp_path, "src/foo.py")
        assert target == (tmp_path / "src" / "foo.py").resolve()

    def test_parent_traversal_is_rejected(self, tmp_path):
        with pytest.raises(RuntimeError):
            _safe_local_target(tmp_path, "../../outside.py")

    def test_windows_drive_letter_injection_stays_contained(self, tmp_path):
        # A path that looks like a Windows absolute path (drive letter) must not
        # be able to escape the repo root once re-joined onto it.
        target = _safe_local_target(tmp_path, "C:/Windows/System32/evil.txt")
        resolved_root = tmp_path.resolve()
        assert target == resolved_root or resolved_root in target.parents

    def test_unc_style_path_stays_contained(self, tmp_path):
        target = _safe_local_target(tmp_path, "//server/share/evil.txt")
        resolved_root = tmp_path.resolve()
        assert target == resolved_root or resolved_root in target.parents

    def test_root_itself_is_a_valid_target(self, tmp_path):
        # repo_path resolving to the root exactly (e.g. a single-segment name)
        # must not be flagged as an escape.
        target = _safe_local_target(tmp_path, "file_at_root.txt")
        assert target.parent == tmp_path.resolve()


class TestFingerprintChanges:
    def test_same_changes_produce_same_fingerprint(self):
        changes = [{"path": "a.py", "operation": "update", "content": "x = 1"}]
        assert _fingerprint_changes(changes) == _fingerprint_changes(list(changes))

    def test_key_order_does_not_affect_fingerprint(self):
        a = [{"path": "a.py", "operation": "update", "content": "x = 1"}]
        b = [{"content": "x = 1", "path": "a.py", "operation": "update"}]
        assert _fingerprint_changes(a) == _fingerprint_changes(b)

    def test_content_change_produces_a_different_fingerprint(self):
        original = [{"path": "a.py", "operation": "update", "content": "x = 1"}]
        mutated = [{"path": "a.py", "operation": "update", "content": "x = 2"}]
        assert _fingerprint_changes(original) != _fingerprint_changes(mutated)

    def test_empty_changes_are_stable(self):
        assert _fingerprint_changes([]) == _fingerprint_changes([])
