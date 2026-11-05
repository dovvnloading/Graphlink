"""Regression tests for security boundaries retained from overlapping PRs.

These tests focus on changes that were not already merged via #444, #447, or #451.
"""

from __future__ import annotations

import pytest

from backend.harness.context import INSTRUCTIONS_BUDGET_CHARS, read_instructions
from backend.harness.tools_fs import _read_bounded_text
from backend.harness.workspace import WorkspaceError
from graphlink_plugins.common.github_client import GitHubRestClient
from graphlink_plugins.gitlink.agent import (
    _is_repo_text_path,
    _normalize_repo_path,
    _safe_local_target,
)
from graphlink_plugins.gitlink.repository import (
    GitlinkRepository,
    resolve_scope_paths,
    scan_local_repo_paths,
)
from provider_runtime import media


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "nested/.aws/credentials",
        "src/.ssh/id_ed25519",
        "certs/app.pem",
        "terraform.tfstate",
        "nested/.dockerconfigjson",
        "secrets.yaml",
    ],
)
def test_automatic_repo_inventory_excludes_obvious_credentials(path):
    assert not _is_repo_text_path(path)


@pytest.mark.parametrize("path", [".env.example", ".env.sample", "README.md", "src/settings.py"])
def test_automatic_repo_inventory_keeps_safe_templates(path):
    assert _is_repo_text_path(path)


@pytest.mark.parametrize("part", [".git", ".hg", ".svn", ".bzr", "cvs"])
def test_gitlink_edits_deny_vcs_metadata(part):
    with pytest.raises(RuntimeError, match="metadata|\.git"):
        _normalize_repo_path(f"subdir/{part}/hooks/post-commit")


def test_cached_inventory_is_filtered_again_when_used_for_full_repo():
    found = resolve_scope_paths(
        "full", [], ["README.md", ".env", "sub/.aws/credentials", ".env.example"],
    )
    assert found == ["README.md", ".env.example"]


def test_symlink_alias_does_not_enter_automatic_repo_inventory(tmp_path):
    (tmp_path / "README.md").write_text("public", encoding="utf-8")
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=private", encoding="utf-8")
    alias = tmp_path / "public.txt"
    try:
        alias.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host")
    assert scan_local_repo_paths(tmp_path) == ["README.md"]


def test_gitlink_cannot_edit_through_symlink_to_vcs_hooks(tmp_path):
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    alias = tmp_path / "shortcut"
    try:
        alias.symlink_to(hooks, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host")
    with pytest.raises(RuntimeError, match="metadata"):
        _safe_local_target(tmp_path, "shortcut/post-commit")


def test_full_repo_context_does_not_load_symlinked_credentials(tmp_path):
    (tmp_path / "README.md").write_text("public", encoding="utf-8")
    secret = tmp_path / ".env"
    secret.write_text("SECRET=DO_NOT_SEND", encoding="utf-8")
    alias = tmp_path / "public.txt"
    try:
        alias.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host")
    bundle = GitlinkRepository(None).build_context_bundle(
        repo_name="o/r", branch_name="main", scope_mode="full",
        selected_paths=[], repo_file_paths=["README.md", "public.txt"],
        local_root=tmp_path,
    )
    assert "DO_NOT_SEND" not in str(bundle)


def test_download_url_from_github_response_must_be_trusted():
    calls = []

    class FakeClient:
        def request(self, url, **kwargs):
            calls.append(url)
            return {"download_url": "http://127.0.0.1/metadata", "encoding": "none"}

    with pytest.raises(RuntimeError, match="unsafe download URL"):
        GitlinkRepository(FakeClient()).fetch_github_file_text("o/r", "main", "README.md")
    assert len(calls) == 1


def test_github_token_is_not_attached_to_http_or_unusual_ports():
    class Settings:
        def get_github_token(self):
            return "ghp_secret"

    client = GitHubRestClient(Settings())
    assert client.build_headers("https://api.github.com/")["Authorization"] == "Bearer ghp_secret"
    for url in (
        "http://api.github.com/repos/o/r",
        "https://api.github.com:444/repos/o/r",
        "https://api.github.com@evil.example/steal",
    ):
        assert "Authorization" not in client.build_headers(url)


def test_workspace_instruction_symlink_never_reads_external_secret(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "private.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    try:
        (workspace / "AGENTS.md").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable on this host")
    assert read_instructions(workspace) == ""


def test_workspace_instructions_are_read_with_a_byte_budget(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "A" * (INSTRUCTIONS_BUDGET_CHARS + 20_000) + "DO_NOT_SEND", encoding="utf-8",
    )
    result = read_instructions(tmp_path)
    assert len(result) < INSTRUCTIONS_BUDGET_CHARS + 100
    assert "[Truncated at" in result
    assert "DO_NOT_SEND" not in result


def test_grep_helper_enforces_actual_bytes_read_not_prior_stat(tmp_path):
    target = tmp_path / "growing.txt"
    target.write_bytes(b"1234567")
    with pytest.raises(WorkspaceError, match="read limit"):
        _read_bounded_text(target, errors="replace", max_bytes=6)


def test_inline_image_base64_size_is_rejected_before_decoding(monkeypatch):
    monkeypatch.setattr(media, "_MAX_PROVIDER_IMAGE_BYTES", 4)

    def forbidden_decode(_text):
        raise AssertionError("Oversized input must be refused before base64 decoding")

    monkeypatch.setattr(media.base64, "b64decode", forbidden_decode)
    with pytest.raises(RuntimeError, match="download limit"):
        media._decode_base64_image("a" * 20)


def test_inline_image_decoded_size_is_checked(monkeypatch):
    monkeypatch.setattr(media, "_MAX_PROVIDER_IMAGE_BYTES", 4)
    monkeypatch.setattr(media.base64, "b64decode", lambda _text: b"12345")
    with pytest.raises(RuntimeError, match="download limit"):
        media._decode_base64_image("aGVsbG8=")
