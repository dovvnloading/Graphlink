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
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, urlsplit

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
# Cap both the number of context candidates and the amount ever read from
# one user-selected file. The eventual XML truncation is NOT an I/O bound.
MAX_CONTEXT_FILES = 1200
MAX_LOCAL_CONTEXT_FILE_BYTES = 2 * 1024 * 1024
MAX_LOCAL_SCAN_ENTRIES = 20_000

# An untrusted repository can contain a ZIP bomb: bound both the compressed
# HTTP body and the number/size of entries extracted onto the user's disk.
MAX_REPO_ARCHIVE_DOWNLOAD_BYTES = 128 * 1024 * 1024
MAX_REPO_FILE_DOWNLOAD_BYTES = 2 * 1024 * 1024
MAX_REPO_ARCHIVE_MEMBER_BYTES = 128 * 1024 * 1024
MAX_REPO_ARCHIVE_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_REPO_ARCHIVE_MEMBERS = 20_000


def _validate_repository_archive(archive: zipfile.ZipFile) -> str:
    """Preflight the entire remote archive, before extracting any entry."""
    infos = archive.infolist()
    if not infos or len(infos) > MAX_REPO_ARCHIVE_MEMBERS:
        raise RuntimeError("Repository archive has too many entries or is empty.")
    roots: set[str] = set()
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = info.filename
        parts = name.rstrip("/").split("/")
        if (not name or name.startswith("/") or "\\" in name
                or any(part in ("", ".", "..") or ":" in part for part in parts)
                or (not info.is_dir() and len(parts) < 2)):
            raise RuntimeError("Repository archive contains an unsafe file path.")
        key = "/".join(parts).casefold()
        if key in seen:
            raise RuntimeError("Repository archive contains duplicate file paths.")
        seen.add(key)
        if info.flag_bits & 1 or stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK:
            raise RuntimeError("Repository archive contains encrypted or symbolic-link entries.")
        if info.file_size > MAX_REPO_ARCHIVE_MEMBER_BYTES:
            raise RuntimeError("Repository archive member exceeds the extraction size limit.")
        total += info.file_size
        if total > MAX_REPO_ARCHIVE_UNPACKED_BYTES:
            raise RuntimeError("Repository archive exceeds the total extraction size limit.")
        roots.add(parts[0])
    if len(roots) != 1:
        raise RuntimeError("Repository archive must contain one top-level directory.")
    return next(iter(roots))


def default_import_root(repo_name, branch_name):
    # Sanitizing only "/" left backslash-embedded ".." segments untouched -
    # on Windows, pathlib re-parses "\\" as a real separator once the
    # sanitized string is joined into a Path, so a repo/branch value such as
    # "a/xyz\\..\\..\\..\\AppData\\Local\\Temp\\evil\\marker" turned back
    # into literal ".." path components after the join and escaped the
    # ~/.graphlink/gitlink_repos/ sandbox. Sanitizing "\\" the same way "/"
    # already is collapses any embedded separator so a single repo/branch
    # value can never reintroduce path components.
    safe_repo = repo_name.replace("/", "__").replace("\\", "__")
    safe_branch = branch_name.replace("/", "__").replace("\\", "__")

    sandbox_root = Path.home() / ".graphlink" / "gitlink_repos"
    candidate = sandbox_root / safe_repo / safe_branch

    # Defense in depth, mirroring agent.py's _safe_local_target pattern
    # (resolve, then verify the result is actually inside the intended
    # root) rather than trusting sanitization alone to cover every case -
    # e.g. a repo/branch value that is itself the literal segment "..".
    # The containment check runs against resolved copies so any lexical
    # ".." is normalized away for the comparison; the *returned* path is
    # left unresolved so it is not silently rewritten to match the on-disk
    # casing of any directory that happens to already exist there.
    resolved_sandbox = sandbox_root.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate != resolved_sandbox and resolved_sandbox not in resolved_candidate.parents:
        raise RuntimeError("Resolved Gitlink import path escaped the local sandbox root.")
    return candidate


def scan_local_repo_paths(local_root):
    from graphlink_plugins.gitlink.apply_transaction import APPLY_LOCK, recover_interrupted_apply

    with APPLY_LOCK:
        if recover_interrupted_apply(local_root):
            raise RuntimeError(
                "Recovered a previously interrupted Gitlink Apply. Reload the file tree "
                "before generating new changes."
            )
    root_path = Path(local_root).expanduser()
    if not root_path.is_dir():
        raise RuntimeError("The selected local repo path does not exist.")

    collected = []
    inspected = 0
    # rglob() traversed ignored directories before filtering the results.
    # Prune them before descent, and do not follow symlinked directories.
    for directory, dirs, filenames in os.walk(root_path, followlinks=False):
        dirs[:] = [
            name for name in dirs
            if name not in IGNORED_LOCAL_DIR_NAMES and not (Path(directory) / name).is_symlink()
        ]
        inspected += len(dirs) + len(filenames)
        if inspected > MAX_LOCAL_SCAN_ENTRIES:
            raise RuntimeError("Repository scan exceeds the file-count limit; select a smaller folder.")
        for name in filenames:
            file_path = Path(directory) / name
            if file_path.is_symlink() or not file_path.is_file():
                continue
            relative = file_path.relative_to(root_path).as_posix()
            if _is_repo_text_path(relative):
                collected.append(relative)
                if len(collected) > MAX_CONTEXT_FILES:
                    raise RuntimeError("Repository contains too many text files for context; select individual files.")
    return sorted(collected, key=str.lower)


def resolve_scope_paths(scope_mode, selected_paths, repo_file_paths, local_root=None):
    if scope_mode == "selected":
        if not selected_paths:
            raise RuntimeError("Select one or more files or switch to Full Repo Access.")
        return list(selected_paths)

    if repo_file_paths:
        # Reapply privacy policy to stale or imported inventories.
        return [path for path in repo_file_paths if _is_repo_text_path(path)]
    if local_root:
        return scan_local_repo_paths(local_root)
    raise RuntimeError("Load the file tree first so Gitlink knows which repository files to stitch together.")


def read_local_repo_file(local_root, repo_path):
    from graphlink_plugins.gitlink.apply_transaction import APPLY_LOCK, recover_interrupted_apply

    with APPLY_LOCK:
        if recover_interrupted_apply(local_root):
            raise RuntimeError(
                "Recovered a previously interrupted Gitlink Apply. Reload the file tree "
                "before generating new changes."
            )
    file_path = _safe_local_target(local_root, repo_path)
    if not file_path.exists():
        raise RuntimeError(f"Local checkout is missing `{repo_path}`.")
    if file_path.is_dir():
        raise RuntimeError(f"`{repo_path}` resolves to a directory, not a file.")
    with file_path.open("rb") as source:
        raw = source.read(MAX_LOCAL_CONTEXT_FILE_BYTES + 1)
    if len(raw) > MAX_LOCAL_CONTEXT_FILE_BYTES:
        raise RuntimeError(f"File `{repo_path}` exceeds the bounded context-read limit.")
    return _decode_text_bytes(raw)


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
        # Mirror apply_change_set's own dispatch exactly: it skips writing
        # only when operation == "delete" and writes for every OTHER value,
        # not just "update"/"create" by name. Enumerating "update"/"create"
        # here instead of "not delete" let an unrecognized operation value
        # (a typo, a stale schema value, or a hand-edited/corrupted saved
        # session) skip this check entirely while still reaching
        # apply_change_set's write branch below - reproduced end to end:
        # such an item silently overwrote a real file with an empty string.
        if operation != "delete":
            content = file_item.get("content")
            if not isinstance(content, str):
                path_text = file_item.get("path", "<unknown path>")
                raise RuntimeError(f"Proposed change for `{path_text}` is missing its file content.")


def apply_change_set(local_root, pending_changes, *, require_review=False):
    """Apply review-bound changes with durable crash recovery and atomic writes.

    A previous interrupted batch is recovered *before* we verify a new
    proposal. An exception rolls back immediately, while a hard crash leaves
    a disk journal that is replayed on the next Gitlink use of this checkout.
    """
    from graphlink_plugins.gitlink.apply_transaction import (
        APPLY_LOCK,
        atomic_replace_file,
        begin_transaction,
        mark_committed,
        recover_interrupted_apply,
    )

    with APPLY_LOCK:
        if recover_interrupted_apply(local_root):
            raise RuntimeError(
                "An interrupted Gitlink Apply was restored. Regenerate and review the proposal "
                "against the recovered checkout before applying again."
            )

        reviewed = None
        if require_review:
            from graphlink_plugins.gitlink.change_review import verify_change_review
            reviewed = verify_change_review(local_root, pending_changes)

        # Snapshot the *complete* input before any mutation. A durable journal
        # records the originals and proposed hashes before the first write.
        entries = []
        seen_paths = set()
        for index, file_item in enumerate(pending_changes):
            path_text = file_item.get("path", "")
            canonical = _normalize_repo_path(path_text)
            if canonical.casefold() in seen_paths:
                raise RuntimeError(f"Duplicate Gitlink Apply path: {canonical}")
            seen_paths.add(canonical.casefold())
            target = _safe_local_target(local_root, canonical)
            if target.exists() and not target.is_file():
                raise IsADirectoryError(f"Gitlink target is not a regular file: {target}")
            if reviewed is not None:
                from graphlink_plugins.gitlink.change_review import assert_target_unchanged
                assert_target_unchanged(local_root, file_item, reviewed[index])

            operation = file_item.get("operation", "update")
            if operation not in {"create", "update", "delete"}:
                raise RuntimeError(f"Unknown Gitlink Apply operation: {operation}")
            content = file_item.get("content")
            if operation != "delete" and not isinstance(content, str):
                raise RuntimeError(f"Proposed change for {canonical} is missing text content.")
            original = target.read_bytes() if target.is_file() else None
            desired = None if operation == "delete" else content.encode("utf-8")
            mode = stat.S_IMODE(target.stat().st_mode) if original is not None else None
            entries.append({
                "path": canonical, "target": target, "original": original,
                "desired": desired, "mode": mode,
            })

        if not entries:
            return 0
        begin_transaction(local_root, entries)
        written_files = 0
        try:
            for index, (file_item, entry) in enumerate(zip(pending_changes, entries)):
                if reviewed is not None:
                    from graphlink_plugins.gitlink.change_review import assert_target_unchanged
                    assert_target_unchanged(local_root, file_item, reviewed[index])

                target = entry["target"]
                if entry["desired"] is None:
                    if target.exists():
                        target.unlink()
                        written_files += 1
                else:
                    atomic_replace_file(target, entry["desired"], entry["mode"])
                    written_files += 1

            mark_committed(local_root)
            return written_files
        except Exception as exc:
            try:
                recover_interrupted_apply(local_root)
            except Exception as recovery_error:
                raise RuntimeError(
                    f"{exc} (Gitlink could not fully roll back the failed Apply: "
                    f"{recovery_error})"
                ) from exc
            raise


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
        if not isinstance(content_payload, dict):
            raise RuntimeError("GitHub returned an invalid file response.")

        if content_payload.get("encoding") == "base64" and content_payload.get("content"):
            return _decode_text_bytes(base64.b64decode(content_payload["content"]))

        download_url = content_payload.get("download_url")
        if download_url:
            # URLs in an API response are not trusted network destinations.
            try:
                parsed_download = urlsplit(str(download_url))
                trusted_download = (
                    parsed_download.scheme == "https"
                    and parsed_download.hostname in {"api.github.com", "raw.githubusercontent.com"}
                    and parsed_download.port in (None, 443)
                    and parsed_download.username is None
                    and parsed_download.password is None
                )
            except ValueError:
                trusted_download = False
            if not trusted_download:
                raise RuntimeError("GitHub file response contains an unsafe download URL (untrusted destination).")
            # Route through github_client rather than a raw requests.get: the
            # bypassed call sent no Authorization header and got none of the
            # client's 401/403/404 friendly-error mapping - a real gap for
            # private-repo file fetches on this (rarer) fallback branch.
            # An API-supplied URL is not itself a trusted network
            # destination. Bound the body before buffering it and enforce
            # the HTTP client's HTTPS/GitHub-only redirect policy.
            raw_bytes = self.github_client.request(
                download_url, expect_json=False, timeout=25,
                max_bytes=MAX_REPO_FILE_DOWNLOAD_BYTES,
            )
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
            max_bytes=MAX_REPO_ARCHIVE_DOWNLOAD_BYTES,
        )

        with tempfile.TemporaryDirectory(prefix="gitlink_import_") as temp_dir:
            temp_path = Path(temp_dir)
            archive_path = temp_path / "repo.zip"
            extract_root = temp_path / "extract"
            extract_root.mkdir(parents=True, exist_ok=True)
            archive_path.write_bytes(archive_bytes)

            with zipfile.ZipFile(archive_path) as archive:
                root_name = _validate_repository_archive(archive)
                archive.extractall(extract_root)

            extracted_root = extract_root / root_name

            if target_root.exists():
                # An existing populated checkout is a concurrent winner.
                # An EMPTY checkout, however, is incomplete and must not
                # turn a successful download into a no-op.
                if any(target_root.iterdir()):
                    return target_root
                target_root.rmdir()
            try:
                shutil.move(str(extracted_root), str(target_root))
            except OSError:
                # A second importer may have promoted its complete checkout
                # after our empty-directory check; prefer its result.
                if target_root.is_dir() and any(target_root.iterdir()):
                    return target_root
                raise

        return target_root

    def build_context_bundle(self, *, repo_name, branch_name, scope_mode, selected_paths, repo_file_paths, local_root):
        scope_paths = resolve_scope_paths(scope_mode, selected_paths, repo_file_paths, local_root=local_root)
        if len(scope_paths) > MAX_CONTEXT_FILES:
            raise RuntimeError("Gitlink context contains too many files; select a smaller scope.")
        records = []
        included_file_count = 0
        omitted_for_budget = 0
        load_errors = 0

        for repo_path in scope_paths:
            normalized_path = _normalize_repo_path(repo_path)
            source_origin = "github"
            try:
                if local_root is not None:
                    if scope_mode != "selected":
                        # Full Repo Access sends context to a model without
                        # per-file approval. Reject secret and symlink aliases.
                        root = Path(local_root).expanduser().resolve()
                        resolved = _safe_local_target(root, normalized_path)
                        resolved_name = resolved.relative_to(root).as_posix()
                        if (not _is_repo_text_path(normalized_path)
                                or not _is_repo_text_path(resolved_name)
                                or (root / normalized_path).is_symlink()):
                            raise RuntimeError(
                                "Sensitive or symlinked file excluded from automatic repository context."
                            )
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
