"""Tests for the Gitlink write-gate's security boundary.

_normalize_repo_path / _safe_local_target are the only thing standing between an
LLM-proposed file path and a write to the user's local disk, and they previously had
zero test coverage despite being that entire security boundary.

These now live in graphlink_plugins.gitlink.agent (extracted out of
graphlink_plugin_gitlink.py along with GitlinkAgent), which is what makes them directly
importable here without any Qt widget or QApplication.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from graphlink_plugins.gitlink.agent import (
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

    def test_absolute_posix_path_is_rejected(self):
        # Previously an lstrip("/") ran before the is_absolute() check, making
        # that guard dead code: "/etc/passwd" was silently rewritten to the
        # repo-relative "etc/passwd" and written INSIDE the repo instead of
        # being rejected with the "must stay inside the repository" error the
        # guard was written to raise (audit finding B1). Absolute-looking
        # input now fails loud at the semantic boundary; byte-level
        # containment remains independently enforced by _safe_local_target.
        with pytest.raises(RuntimeError):
            _normalize_repo_path("/etc/passwd")

    def test_unc_style_path_is_rejected(self):
        with pytest.raises(RuntimeError):
            _normalize_repo_path("//server/share/evil.txt")

    def test_windows_drive_letter_path_is_rejected(self):
        # Bug-scan finding: a drive-lettered path isn't "absolute" by POSIX's
        # is_absolute() (no leading '/'), so it slipped past the check above.
        # Not a real escape - _safe_local_target's containment check still
        # holds - but silent ALIASING: Windows pathlib treats a same-drive
        # "C:" path segment as a no-op drive-relative reference rather than a
        # literal folder, so this used to resolve to the exact same target as
        # the plain "config/app.txt", contrary to this guard's own contract
        # of rejecting absolute-looking input.
        with pytest.raises(RuntimeError):
            _normalize_repo_path("C:/config/app.txt")

    def test_windows_drive_relative_path_with_no_slash_is_rejected(self):
        # "C:app.txt" (no slash) is the same drive-relative hazard as above,
        # via a different textual shape - PurePosixPath keeps it as one part
        # ("C:app.txt") rather than splitting off "C:", but the substring
        # check still catches it.
        with pytest.raises(RuntimeError):
            _normalize_repo_path("C:app.txt")

    def test_cross_drive_path_is_rejected(self):
        with pytest.raises(RuntimeError):
            _normalize_repo_path("D:/config/app.txt")

    def test_alternate_data_stream_style_path_is_rejected(self):
        # An NTFS Alternate Data Stream marker - same defensive "reject any
        # colon" guard, though ADS addressing doesn't actually work through
        # this path-join code path.
        with pytest.raises(RuntimeError):
            _normalize_repo_path("file.txt:hidden_stream")

    def test_a_colon_deeper_in_the_path_is_still_rejected(self):
        with pytest.raises(RuntimeError):
            _normalize_repo_path("src/weird:name.py")


class TestSafeLocalTarget:
    def test_normal_path_resolves_under_root(self, tmp_path):
        target = _safe_local_target(tmp_path, "src/foo.py")
        assert target == (tmp_path / "src" / "foo.py").resolve()

    def test_parent_traversal_is_rejected(self, tmp_path):
        with pytest.raises(RuntimeError):
            _safe_local_target(tmp_path, "../../outside.py")

    def test_windows_drive_letter_path_is_rejected_at_normalization(self, tmp_path):
        # _safe_local_target normalizes first, and normalization now rejects
        # a drive-lettered path outright (see TestNormalizeRepoPath) instead
        # of silently aliasing it onto some path under the repo root.
        with pytest.raises(RuntimeError):
            _safe_local_target(tmp_path, "C:/Windows/System32/evil.txt")

    def test_unc_style_path_is_rejected_at_normalization(self, tmp_path):
        # _safe_local_target normalizes first, and normalization now rejects
        # absolute-looking input outright (see TestNormalizeRepoPath) instead
        # of silently rewriting it to a contained relative path.
        with pytest.raises(RuntimeError):
            _safe_local_target(tmp_path, "//server/share/evil.txt")

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
