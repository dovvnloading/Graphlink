"""Gitlink's Qt-free repository/context-building logic, extracted out of
graphlink_plugin_gitlink.py (Phase 7 prerequisite increment 6).

gitlink/agent.py's own module docstring documents that build_context_bundle/
_resolve_scope_paths/_scan_local_repo_paths were the ORIGINAL target of that
prior extraction and were deliberately left as GitlinkNode methods because
they were judged "entangled with widget state... a redesign, not a refactor."
This module revisits that punt: the state itself (repo/branch/local_root/
scope_mode/task_prompt/selected_paths) stays exactly where it already lived
correctly - as plain GitlinkNode attributes, kept in sync by the node's own
textChanged/currentIndexChanged handlers, matching every other node type in
this migration (WebNode.query, HtmlViewNode.html_content, etc.) - only the
logic that doesn't need Qt at all moves here, taking that already-correct
state as plain parameters instead of reading widgets directly.

GitlinkRepository owns nothing but a GitHubRestClient reference, mirroring
GitlinkNode's own pre-existing self._github_client pattern - it is a stateless
helper, not a second source of truth for repo/branch/local_root.
"""

import base64
import html
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import quote

from graphlink_plugins.gitlink.agent import (
    _clean_text,
    _decode_text_bytes,
    _is_repo_text_path,
    _normalize_repo_path,
    _safe_local_target,
    _truncate_for_context,
    _xml_file_block,
)


IGNORED_LOCAL_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}

MAX_CONTEXT_CHARS = 180000
MAX_MANIFEST_ENTRIES = 1200


def default_import_root(repo_name, branch_name):
    safe_repo = repo_name.replace("/", "__")
    safe_branch = branch_name.replace("/", "__")
    return Path.home() / ".graphlink" / "gitlink_repos" / safe_repo / safe_branch


def scan_local_repo_paths(local_root):
    root_path = Path(local_root).expanduser()
    if not root_path.exists():
        raise RuntimeError("The selected local repo path does not exist.")

    collected = []
    for file_path in root_path.rglob("*"):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(root_path).as_posix()
        if any(part in IGNORED_LOCAL_DIR_NAMES for part in PurePosixPath(relative).parts):
            continue
        if not _is_repo_text_path(relative):
            continue
        collected.append(relative)
    return sorted(collected, key=str.lower)


def resolve_scope_paths(scope_mode, selected_paths, repo_file_paths, local_root=None):
    if scope_mode == "selected":
        if not selected_paths:
            raise RuntimeError("Select one or more files or switch to Full Repo Access.")
        return list(selected_paths)

    if repo_file_paths:
        return list(repo_file_paths)
    if local_root:
        return scan_local_repo_paths(local_root)
    raise RuntimeError("Load the file tree first so Gitlink knows which repository files to stitch together.")


def read_local_repo_file(local_root, repo_path):
    file_path = _safe_local_target(local_root, repo_path)
    if not file_path.exists():
        raise RuntimeError(f"Local checkout is missing `{repo_path}`.")
    if file_path.is_dir():
        raise RuntimeError(f"`{repo_path}` resolves to a directory, not a file.")
    return _decode_text_bytes(file_path.read_bytes())


def validate_pending_changes(pending_changes):
    # Session-restored proposals skip GitlinkAgent._normalize_files (that
    # normalization only runs for freshly-generated proposals, agent.py's
    # get_response), so a restored change item is not guaranteed to have a
    # 'content' key. Without this check, apply_change_set's original
    # `file_item.get('content', '')` would silently write an EMPTY file over
    # real content - failing loud here instead means the same caller's
    # existing except-block revert-to-PREVIEWED path handles it like any
    # other write failure, rather than corrupting a file on disk.
    for file_item in pending_changes:
        operation = file_item.get("operation", "update")
        if operation in ("update", "create"):
            content = file_item.get("content")
            if not isinstance(content, str):
                path_text = file_item.get("path", "<unknown path>")
                raise RuntimeError(f"Proposed change for `{path_text}` is missing its file content.")


def apply_change_set(local_root, pending_changes):
    written_files = 0
    for file_item in pending_changes:
        path_text = file_item.get("path", "")
        operation = file_item.get("operation", "update")
        target_path = _safe_local_target(local_root, path_text)

        if operation == "delete":
            if target_path.exists():
                target_path.unlink()
                written_files += 1
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(file_item.get("content", ""), encoding="utf-8")
        written_files += 1

    return written_files


class ContextBundleResult(NamedTuple):
    context_xml: str
    context_stats: dict
    context_summary: str
    included_paths: list


class GitlinkRepository:
    """Stateless helper for the GitHub/filesystem operations GitlinkNode needs -
    owns only a github_client reference, exactly like the node's own
    self._github_client, not a second copy of repo/branch/local_root state."""

    def __init__(self, github_client):
        self.github_client = github_client

    def fetch_github_file_text(self, repo_name, branch_name, repo_path):
        content_payload = self.github_client.request(
            f"https://api.github.com/repos/{repo_name}/contents/{quote(repo_path, safe='/')}",
            params={"ref": branch_name},
        )
        if isinstance(content_payload, list):
            raise RuntimeError(f"`{repo_path}` resolves to a directory, not a file.")

        if content_payload.get("encoding") == "base64" and content_payload.get("content"):
            return _decode_text_bytes(base64.b64decode(content_payload["content"]))

        download_url = content_payload.get("download_url")
        if download_url:
            # Route through github_client rather than a raw requests.get: the
            # bypassed call sent no Authorization header and got none of the
            # client's 401/403/404 friendly-error mapping - a real gap for
            # private-repo file fetches on this (rarer) fallback branch.
            raw_bytes = self.github_client.request(download_url, expect_json=False, timeout=25)
            return _decode_text_bytes(raw_bytes)

        raise RuntimeError(f"GitHub did not return file contents for `{repo_path}`.")

    def download_repository_snapshot(self, repo_name, branch_name, target_root):
        if target_root.exists() and any(target_root.iterdir()):
            return target_root

        target_root.parent.mkdir(parents=True, exist_ok=True)
        archive_bytes = self.github_client.request(
            f"https://api.github.com/repos/{repo_name}/zipball/{quote(branch_name, safe='')}",
            expect_json=False,
            timeout=60,
        )

        with tempfile.TemporaryDirectory(prefix="gitlink_import_") as temp_dir:
            temp_path = Path(temp_dir)
            archive_path = temp_path / "repo.zip"
            extract_root = temp_path / "extract"
            extract_root.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(archive_bytes)

            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(extract_root)

            extracted_dirs = [item for item in extract_root.iterdir() if item.is_dir()]
            extracted_root = extracted_dirs[0] if extracted_dirs else extract_root

            if target_root.exists():
                return target_root

            shutil.move(str(extracted_root), str(target_root))

        return target_root

    def build_context_bundle(self, *, repo_name, branch_name, scope_mode, selected_paths, repo_file_paths, local_root):
        scope_paths = resolve_scope_paths(scope_mode, selected_paths, repo_file_paths, local_root=local_root)
        records = []
        included_file_count = 0
        omitted_for_budget = 0
        load_errors = 0

        for repo_path in scope_paths:
            normalized_path = _normalize_repo_path(repo_path)
            source_origin = "github"
            try:
                if local_root is not None:
                    source_text = read_local_repo_file(local_root, normalized_path)
                    source_origin = "local"
                else:
                    source_text = self.fetch_github_file_text(repo_name, branch_name, normalized_path)
                visible_text, source_truncated = _truncate_for_context(source_text)
                records.append({
                    "path": normalized_path,
                    "source": source_origin,
                    "content": visible_text,
                    "original_chars": len(source_text),
                    "source_truncated": source_truncated,
                    "included": False,
                })
            except Exception as exc:
                load_errors += 1
                records.append({
                    "path": normalized_path,
                    "source": source_origin,
                    "error": _clean_text(exc, limit=180) or "Unknown file load error.",
                })

        current_chars = 0
        file_blocks = []
        for record in records:
            if record.get("error"):
                continue
            candidate_block = _xml_file_block(
                record["path"],
                record["content"],
                truncated=record.get("source_truncated", False),
                original_chars=record.get("original_chars", 0),
            )
            if file_blocks and (current_chars + len(candidate_block) > MAX_CONTEXT_CHARS):
                omitted_for_budget += 1
                continue
            record["included"] = True
            file_blocks.append(candidate_block)
            current_chars += len(candidate_block)
            included_file_count += 1

        manifest_lines = []
        for index, record in enumerate(records):
            if index >= MAX_MANIFEST_ENTRIES:
                break
            attrs = [
                f'path="{html.escape(record["path"], quote=True)}"',
                f'source="{html.escape(record.get("source", "unknown"), quote=True)}"',
            ]
            if record.get("error"):
                attrs.append(f'error="{html.escape(record["error"], quote=True)}"')
            else:
                attrs.append(f'included="{str(bool(record.get("included"))).lower()}"')
                attrs.append(f'chars="{max(0, int(record.get("original_chars", 0)))}"')
                attrs.append(f'truncated="{str(bool(record.get("source_truncated"))).lower()}"')
                if not record.get("included"):
                    attrs.append('omitted="true"')
                    attrs.append('reason="context_budget"')
            manifest_lines.append(f"    <file {' '.join(attrs)} />")

        manifest_omitted = max(0, len(records) - MAX_MANIFEST_ENTRIES)
        if manifest_omitted:
            manifest_lines.append(f'    <more count="{manifest_omitted}" reason="manifest_budget" />')

        scope_label = "full_repo" if scope_mode == "full" else "selected_files"
        xml_parts = [
            f'<gitlink_context repository="{html.escape(repo_name, quote=True)}" branch="{html.escape(branch_name, quote=True)}" scope="{scope_label}">',
            f"  <summary scanned_files=\"{len(records)}\" loaded_files=\"{len(records) - load_errors}\" included_files=\"{included_file_count}\" load_errors=\"{load_errors}\" context_omissions=\"{omitted_for_budget}\" />",
            "  <manifest>",
            *manifest_lines,
            "  </manifest>",
            "  <files>",
            *file_blocks,
            "  </files>",
            "</gitlink_context>",
        ]
        context_xml = "\n".join(xml_parts)

        source_root = str(local_root) if local_root is not None else "github"
        summary_parts = [
            f"Scanned {len(records)} files",
            f"loaded {len(records) - load_errors}",
            f"included {included_file_count}",
        ]
        if omitted_for_budget:
            summary_parts.append(f"omitted {omitted_for_budget} for context budget")
        if load_errors:
            summary_parts.append(f"hit {load_errors} load errors")
        summary_parts.append(f"source={source_root}")
        context_summary = ", ".join(summary_parts) + "."

        context_stats = {
            "scanned_files": len(records),
            "loaded_files": len(records) - load_errors,
            "included_files": included_file_count,
            "load_errors": load_errors,
            "context_omissions": omitted_for_budget,
            "source_root": source_root,
            "summary": context_summary,
        }
        included_paths = [record["path"] for record in records if record.get("included")]

        return ContextBundleResult(
            context_xml=context_xml,
            context_stats=context_stats,
            context_summary=context_summary,
            included_paths=included_paths,
        )
