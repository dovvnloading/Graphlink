"""Read-only browsing and previews for the workspace agent's Files panel.

The root is chosen by the harness intent through bound_root(), never from a
path supplied by the browser. Every child path still passes the same
resolve_under_root confinement and VCS-metadata gate as the agent's fs tools.
"""
from __future__ import annotations

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


def list_workspace_files(root: Path, relative_path: str = "") -> dict:
    """List one directory at a time; never recursively walk a project tree.

    Bound discovery before sorting: a huge directory cannot produce an
    unbounded server response or block the UI by enumerating every entry.
    Escaping symlinks and VCS metadata do not appear in the listing.
    """
    root = root.resolve()
    directory = _inside(root, relative_path)
    if not directory.is_dir():
        raise WorkspaceError("Not a workspace directory.")

    entries: list[dict] = []
    truncated = False
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
                    # Broken, unreadable, or out-of-root symlinks are not
                    # navigable and must not leak outside the grant.
                    continue
                if len(entries) == _MAX_ENTRIES:
                    truncated = True
                    break
                entries.append({
                    "name": child.name,
                    "path": target.relative_to(root).as_posix(),
                    "isDir": is_dir,
                    "size": size,
                })
    except OSError as exc:
        raise WorkspaceError(f"Could not list workspace folder: {exc}") from exc

    entries.sort(key=lambda entry: (not entry["isDir"], entry["name"].casefold()))
    return {
        "path": directory.relative_to(root).as_posix() if directory != root else "",
        "entries": entries,
        "truncated": truncated,
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
