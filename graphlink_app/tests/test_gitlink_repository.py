"""Phase 7 prerequisite increment 6: GitlinkNode's tangled domain logic
(GitHub tree/file loading, local-repo scanning, context-bundle assembly, and
the write-approval loop) moves to a new Qt-free module,
graphlink_plugins/gitlink/repository.py - a peer of gitlink/agent.py and
common/github_client.py, following the exact same "revisit the previously
punted extraction" reasoning gitlink/agent.py's own docstring already
documents. This module has never had any test coverage before this increment
(confirmed via repo-wide grep for build_context_bundle/apply_approved_changes/
etc. before this increment landed) - this is the decisive new coverage for
the one part of the app that writes AI-generated content to the user's local
disk.

No QApplication needed anywhere in this file - repository.py is Qt-free by
construction, matching test_gitlink_agent.py/test_gitlink_path_safety.py's
own precedent for the other two Qt-free gitlink modules.
"""

import base64
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from graphlink_plugins.gitlink.repository import (
    ContextBundleResult,
    GitlinkRepository,
    apply_change_set,
    default_import_root,
    read_local_repo_file,
    resolve_scope_paths,
    scan_local_repo_paths,
    validate_pending_changes,
)


class _FakeGithubClient:
    """Duck-typed stand-in for GitHubRestClient.request(url, params=None, *,
    expect_json=True, timeout=25) - records every call so bugfix-regression
    tests can assert on exactly what was requested and how."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, url, params=None, *, expect_json=True, timeout=25):
        self.calls.append({"url": url, "params": params, "expect_json": expect_json, "timeout": timeout})
        if not self.responses:
            raise AssertionError("_FakeGithubClient ran out of queued responses")
        return self.responses.pop(0)


class TestDefaultImportRoot:
    def test_replaces_slashes_in_repo_and_branch(self):
        result = default_import_root("owner/repo", "feature/x")
        assert result.name == "feature__x"
        assert result.parent.name == "owner__repo"


class TestScanLocalRepoPaths:
    def test_finds_nested_text_files(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print(1)")
        (tmp_path / "README.md").write_text("hi")

        paths = scan_local_repo_paths(tmp_path)

        assert "src/main.py" in paths
        assert "README.md" in paths

    def test_excludes_ignored_directory_names(self, tmp_path):
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("x")
        (tmp_path / "app.py").write_text("x")

        paths = scan_local_repo_paths(tmp_path)

        assert "app.py" in paths
        assert not any("node_modules" in p for p in paths)

    def test_excludes_binary_suffixes(self, tmp_path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        (tmp_path / "app.py").write_text("x")

        paths = scan_local_repo_paths(tmp_path)

        assert "app.py" in paths
        assert "image.png" not in paths

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(RuntimeError):
            scan_local_repo_paths(tmp_path / "does_not_exist")


class TestResolveScopePaths:
    def test_selected_mode_returns_selected_paths(self):
        assert resolve_scope_paths("selected", ["a.py", "b.py"], []) == ["a.py", "b.py"]

    def test_selected_mode_raises_when_nothing_selected(self):
        with pytest.raises(RuntimeError):
            resolve_scope_paths("selected", [], [])

    def test_full_mode_prefers_repo_file_paths(self):
        result = resolve_scope_paths("full", [], ["a.py", "b.py"])
        assert result == ["a.py", "b.py"]

    def test_full_mode_falls_back_to_scanning_local_root(self, tmp_path):
        (tmp_path / "app.py").write_text("x")

        result = resolve_scope_paths("full", [], [], local_root=tmp_path)

        assert result == ["app.py"]

    def test_full_mode_raises_with_nothing_available(self):
        with pytest.raises(RuntimeError):
            resolve_scope_paths("full", [], [])


class TestReadLocalRepoFile:
    def test_happy_path(self, tmp_path):
        (tmp_path / "a.py").write_text("print(1)")
        assert read_local_repo_file(tmp_path, "a.py") == "print(1)"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(RuntimeError):
            read_local_repo_file(tmp_path, "missing.py")

    def test_directory_raises(self, tmp_path):
        (tmp_path / "a_dir").mkdir()
        with pytest.raises(RuntimeError):
            read_local_repo_file(tmp_path, "a_dir")


class TestFetchGithubFileText:
    def test_base64_branch_decodes_correctly(self):
        encoded = base64.b64encode(b"print(1)").decode("ascii")
        client = _FakeGithubClient([{"encoding": "base64", "content": encoded}])
        repo = GitlinkRepository(client)

        result = repo.fetch_github_file_text("owner/repo", "main", "a.py")

        assert result == "print(1)"

    def test_download_url_fallback_routes_through_the_client_not_raw_requests(self):
        # The bugfix regression test: this fallback used to call requests.get()
        # directly, bypassing GitHubRestClient's auth header and error mapping
        # entirely. Now it must go through the same client.request(...) as
        # every other GitHub call, with expect_json=False (raw bytes).
        client = _FakeGithubClient([
            {"download_url": "https://raw.example/a.py"},
            b"print(2)",
        ])
        repo = GitlinkRepository(client)

        result = repo.fetch_github_file_text("owner/repo", "main", "a.py")

        assert result == "print(2)"
        assert client.calls[1]["url"] == "https://raw.example/a.py"
        assert client.calls[1]["expect_json"] is False

    def test_directory_response_raises(self):
        client = _FakeGithubClient([[{"name": "a.py"}, {"name": "b.py"}]])
        repo = GitlinkRepository(client)

        with pytest.raises(RuntimeError):
            repo.fetch_github_file_text("owner/repo", "main", "some_dir")

    def test_neither_content_nor_download_url_raises(self):
        client = _FakeGithubClient([{}])
        repo = GitlinkRepository(client)

        with pytest.raises(RuntimeError):
            repo.fetch_github_file_text("owner/repo", "main", "a.py")


class TestDownloadRepositorySnapshot:
    def test_already_populated_target_short_circuits_with_no_request(self, tmp_path):
        target_root = tmp_path / "existing"
        target_root.mkdir()
        (target_root / "a.py").write_text("x")
        client = _FakeGithubClient([])
        repo = GitlinkRepository(client)

        result = repo.download_repository_snapshot("owner/repo", "main", target_root)

        assert result == target_root
        assert client.calls == []

    def test_fresh_download_extracts_a_zip(self, tmp_path):
        zip_bytes_path = tmp_path / "src.zip"
        with zipfile.ZipFile(zip_bytes_path, "w") as archive:
            archive.writestr("repo-main/app.py", "print(1)")
        archive_bytes = zip_bytes_path.read_bytes()

        client = _FakeGithubClient([archive_bytes])
        repo = GitlinkRepository(client)
        target_root = tmp_path / "extracted" / "owner__repo" / "main"

        result = repo.download_repository_snapshot("owner/repo", "main", target_root)

        assert result == target_root
        assert (target_root / "app.py").read_text() == "print(1)"
        assert client.calls[0]["expect_json"] is False


class TestBuildContextBundle:
    def test_all_local_happy_path(self, tmp_path):
        (tmp_path / "a.py").write_text("print(1)")
        (tmp_path / "b.py").write_text("print(2)")
        repo = GitlinkRepository(_FakeGithubClient([]))

        result = repo.build_context_bundle(
            repo_name="owner/repo",
            branch_name="main",
            scope_mode="selected",
            selected_paths=["a.py", "b.py"],
            repo_file_paths=[],
            local_root=tmp_path,
        )

        assert isinstance(result, ContextBundleResult)
        assert result.context_stats["scanned_files"] == 2
        assert result.context_stats["loaded_files"] == 2
        assert result.context_stats["load_errors"] == 0
        assert "a.py" in result.context_xml
        assert "print(1)" in result.context_xml

    def test_one_file_load_error_is_recorded_not_raised(self, tmp_path):
        (tmp_path / "a.py").write_text("print(1)")
        repo = GitlinkRepository(_FakeGithubClient([]))

        result = repo.build_context_bundle(
            repo_name="owner/repo",
            branch_name="main",
            scope_mode="selected",
            selected_paths=["a.py", "missing.py"],
            repo_file_paths=[],
            local_root=tmp_path,
        )

        assert result.context_stats["load_errors"] == 1
        assert result.context_stats["loaded_files"] == 1

    def test_context_budget_omission(self, tmp_path, monkeypatch):
        (tmp_path / "a.py").write_text("x" * 100)
        (tmp_path / "b.py").write_text("y" * 100)
        monkeypatch.setattr("graphlink_plugins.gitlink.repository.MAX_CONTEXT_CHARS", 50)
        repo = GitlinkRepository(_FakeGithubClient([]))

        result = repo.build_context_bundle(
            repo_name="owner/repo",
            branch_name="main",
            scope_mode="selected",
            selected_paths=["a.py", "b.py"],
            repo_file_paths=[],
            local_root=tmp_path,
        )

        assert result.context_stats["context_omissions"] >= 1
        assert result.context_stats["included_files"] < 2

    def test_scope_resolution_failure_propagates(self, tmp_path):
        repo = GitlinkRepository(_FakeGithubClient([]))

        with pytest.raises(RuntimeError):
            repo.build_context_bundle(
                repo_name="owner/repo",
                branch_name="main",
                scope_mode="selected",
                selected_paths=[],
                repo_file_paths=[],
                local_root=tmp_path,
            )


class TestValidatePendingChanges:
    def test_well_formed_changes_pass(self):
        validate_pending_changes([
            {"path": "a.py", "operation": "update", "content": "x"},
            {"path": "b.py", "operation": "create", "content": ""},
            {"path": "c.py", "operation": "delete"},
        ])  # must not raise

    def test_missing_content_on_update_raises_naming_the_path(self):
        with pytest.raises(RuntimeError, match="missing_content.py"):
            validate_pending_changes([{"path": "missing_content.py", "operation": "update"}])

    def test_missing_content_on_create_raises(self):
        with pytest.raises(RuntimeError):
            validate_pending_changes([{"path": "new.py", "operation": "create"}])

    def test_delete_needs_no_content(self):
        validate_pending_changes([{"path": "gone.py", "operation": "delete"}])  # must not raise

    def test_unrecognized_operation_value_still_requires_content(self):
        # Adversarial-review finding: apply_change_set's real dispatch skips
        # writing ONLY for operation == "delete" and writes for every other
        # value - not just "update"/"create" by name. A guard that enumerated
        # "update"/"create" specifically let an unrecognized value (a typo, a
        # stale schema value, a hand-edited/corrupted saved session) skip
        # validation while still reaching the write branch, silently
        # overwriting a real file with an empty string.
        with pytest.raises(RuntimeError, match="weird.py"):
            validate_pending_changes([{"path": "weird.py", "operation": "modify"}])


class TestApplyChangeSet:
    def test_create_writes_file_and_parent_dirs(self, tmp_path):
        written = apply_change_set(tmp_path, [
            {"path": "nested/new.py", "operation": "create", "content": "print(1)"},
        ])
        assert written == 1
        assert (tmp_path / "nested" / "new.py").read_text() == "print(1)"

    def test_update_overwrites_existing_content(self, tmp_path):
        (tmp_path / "a.py").write_text("old")
        written = apply_change_set(tmp_path, [
            {"path": "a.py", "operation": "update", "content": "new"},
        ])
        assert written == 1
        assert (tmp_path / "a.py").read_text() == "new"

    def test_delete_removes_file_and_counts(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        written = apply_change_set(tmp_path, [{"path": "a.py", "operation": "delete"}])
        assert written == 1
        assert not (tmp_path / "a.py").exists()

    def test_delete_on_nonexistent_file_is_a_silent_no_op(self, tmp_path):
        written = apply_change_set(tmp_path, [{"path": "never_existed.py", "operation": "delete"}])
        assert written == 0


class TestApplyChangeSetRollback:
    """Audit finding A1: a mid-loop failure used to leave files 1..N-1 already
    written/deleted with no way back - the checkout ended half-applied while
    the UI reported the whole apply as failed. apply_change_set now snapshots
    every target's pre-state before touching it and rolls back on any
    exception. The failing item in these tests is a path that resolves to an
    existing DIRECTORY - reading/writing it as a file raises on every
    platform, and it passes validate_pending_changes (content present), so it
    models a genuine mid-write surprise rather than a pre-validated reject."""

    def _blocker(self, tmp_path):
        (tmp_path / "blocker_dir").mkdir()
        return {"path": "blocker_dir", "operation": "update", "content": "x"}

    def test_mid_loop_failure_restores_an_earlier_overwrite(self, tmp_path):
        (tmp_path / "a.py").write_text("ORIGINAL A")

        with pytest.raises(Exception):
            apply_change_set(tmp_path, [
                {"path": "a.py", "operation": "update", "content": "NEW A"},
                self._blocker(tmp_path),
            ])

        assert (tmp_path / "a.py").read_text() == "ORIGINAL A"

    def test_mid_loop_failure_removes_a_file_created_earlier(self, tmp_path):
        with pytest.raises(Exception):
            apply_change_set(tmp_path, [
                {"path": "brand_new.py", "operation": "create", "content": "print(1)"},
                self._blocker(tmp_path),
            ])

        assert not (tmp_path / "brand_new.py").exists()

    def test_mid_loop_failure_restores_an_earlier_delete(self, tmp_path):
        (tmp_path / "a.py").write_text("KEEP ME")

        with pytest.raises(Exception):
            apply_change_set(tmp_path, [
                {"path": "a.py", "operation": "delete"},
                self._blocker(tmp_path),
            ])

        assert (tmp_path / "a.py").read_text() == "KEEP ME"

    def test_successful_apply_is_unchanged_by_the_rollback_machinery(self, tmp_path):
        (tmp_path / "a.py").write_text("old")

        written = apply_change_set(tmp_path, [
            {"path": "a.py", "operation": "update", "content": "new"},
            {"path": "b.py", "operation": "create", "content": "made"},
        ])

        assert written == 2
        assert (tmp_path / "a.py").read_text() == "new"
        assert (tmp_path / "b.py").read_text() == "made"
