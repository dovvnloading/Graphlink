"""Read-only browsing and previews for the workspace agent's Files panel.

The root is chosen by the harness intent through bound_root(), never from a
path supplied by the browser. Every child path still passes the same
resolve_under_root confinement and VCS-metadata gate as the agent's fs tools.
"""
from __future__ import annotations

import base64
import binascii
import heapq
import json
import os
import subprocess
import sys
from pathlib import Path

from backend.harness.tools_fs import _is_metadata_path, _read_bounded_bytes
from backend.harness.workspace import WorkspaceError, resolve_under_root

_MAX_PATH_CHARS = 4_000
_MAX_ENTRIES = 300
_MAX_PREVIEW_CHARS = 40_000
_DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".xlsx", ".xlsm"})


def _inside(root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or len(relative_path) > _MAX_PATH_CHARS:
        raise WorkspaceError("Invalid workspace path.")
    target = resolve_under_root(root, relative_path)
    if _is_metadata_path(target):
        raise WorkspaceError("VCS metadata is not available in workspace files.")
    return target


def _entry_key(entry: dict) -> tuple[bool, str, str]:
    # Directories first, then case-insensitive names with an exact-name
    # tie-breaker for filesystems which permit case-only name differences.
    name = entry["name"]
    return not entry["isDir"], name.casefold(), name


def _page_key(cursor: str | None) -> tuple[bool, str, str] | None:
    if cursor is None:
        return None
    if not isinstance(cursor, str) or not 0 < len(cursor) <= 1024:
        raise WorkspaceError("Invalid workspace page cursor.")
    try:
        is_dir, name = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if type(is_dir) is not bool or not isinstance(name, str) or not 0 < len(name) <= 255:
            raise ValueError("Malformed cursor")
    except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
        raise WorkspaceError("Invalid workspace page cursor.") from exc
    return not is_dir, name.casefold(), name


def _next_cursor(entry: dict) -> str:
    payload = json.dumps([entry["isDir"], entry["name"]], ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def list_workspace_files(root: Path, relative_path: str = "", cursor: str | None = None) -> dict:
    """List a deterministic, cursor-paginated page of one directory.

    Each page scans the directory but holds at most _MAX_ENTRIES + 1
    candidates in memory. Entries beyond the first page remain reachable,
    including in flat directories. Every result still passes the workspace
    boundary and VCS-metadata checks before it can be returned.
    """
    root = root.resolve()
    directory = _inside(root, relative_path)
    if not directory.is_dir():
        raise WorkspaceError("Not a workspace directory.")
    after = _page_key(cursor)

    def candidates():
        try:
            with os.scandir(directory) as iterator:
                for child in iterator:
                    if child.name.casefold() in {".git", ".hg", ".svn"}:
                        continue
                    try:
                        target = _inside(root, str(Path(directory) / child.name))
                        is_dir = target.is_dir()
                        if not is_dir and not target.is_file():
                            continue
                        size = None if is_dir else target.stat().st_size
                    except (OSError, WorkspaceError):
                        # Broken, unreadable, or out-of-root symlinks never
                        # appear on any page.
                        continue
                    entry = {
                        "name": child.name,
                        "path": target.relative_to(root).as_posix(),
                        "isDir": is_dir,
                        "size": size,
                    }
                    if after is None or _entry_key(entry) > after:
                        yield entry
        except OSError as exc:
            raise WorkspaceError(f"Could not list workspace folder: {exc}") from exc

    page = heapq.nsmallest(_MAX_ENTRIES + 1, candidates(), key=_entry_key)
    more = len(page) > _MAX_ENTRIES
    entries = page[:_MAX_ENTRIES]
    return {
        "path": directory.relative_to(root).as_posix() if directory != root else "",
        "entries": entries,
        "truncated": more,
        "nextCursor": _next_cursor(entries[-1]) if more else None,
    }


def read_workspace_file(root: Path, relative_path: str) -> dict:
    """Provide a bounded text preview for Document View, never raw binary."""
    root = root.resolve()
    if not relative_path:
        raise WorkspaceError("Select a file to open.")
    target = _inside(root, relative_path)
    if not target.is_file():
        raise WorkspaceError("Not a workspace file.")
    suffix = target.suffix.lower()
    if suffix in {".doc", ".xls"}:
        raise WorkspaceError("Legacy .doc/.xls previews are unsupported; convert the file first.")

    try:
        data = _read_bounded_bytes(target)
        if suffix in _DOCUMENT_SUFFIXES:
            from backend.harness.document_read import extract_document_text

            content = extract_document_text(data, suffix)
        else:
            if b"\x00" in data:
                raise WorkspaceError("Binary files cannot be opened in Document View.")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkspaceError("This file is not UTF-8 text and cannot be previewed.") from exc
    except OSError as exc:
        raise WorkspaceError(f"Could not open workspace file: {exc}") from exc
    except ValueError as exc:
        if isinstance(exc, WorkspaceError):
            raise
        raise WorkspaceError(f"Could not preview workspace document: {exc}") from exc

    truncated = len(content) > _MAX_PREVIEW_CHARS
    return {
        "path": target.relative_to(root).as_posix(),
        "content": content[:_MAX_PREVIEW_CHARS],
        "truncated": truncated,
    }


def reveal_workspace(root: Path) -> None:
    """Open the already-authorized workspace root in the native file manager."""
    folder = str(root.resolve())
    if sys.platform == "win32":
        os.startfile(folder)  # type: ignore[attr-defined]  # Windows only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", folder], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
