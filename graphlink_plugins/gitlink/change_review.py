"""Review-bound, conflict-safe file snapshots for Gitlink.

A proposal fingerprint protects *the proposal*, not the files on disk. These
helpers also bind each proposed operation to the original file bytes at the
instant its preview is generated. At Apply, ALL targets are checked before
the first write; even an edit to the last file rejects the whole proposal.

Only the server constructs expected_sha256. No client-supplied filesystem
contents or hashes are accepted by the apply intent. A restored proposal from
an older session without baselines must be regenerated before it can write.
This is optimistic conflict detection, not a filesystem transaction across
processes: a separate writer can still race between the last check and write.
"""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from graphlink_plugins.gitlink.agent import (
    _decode_text_bytes,
    _normalize_repo_path,
    _safe_local_target,
)

BASELINE_FIELD = "expected_sha256"
MAX_REVIEW_FILES = 128
MAX_REVIEW_FILE_BYTES = 8 * 1024 * 1024
MAX_REVIEW_TOTAL_BYTES = 32 * 1024 * 1024


class ReviewedTarget(NamedTuple):
    """A target and its byte-exact original, safe to use for rollback."""

    path: str
    target: Path
    original: bytes | None


def read_review_bytes(target: Path, path: str) -> bytes | None:
    """Read at most the review cap plus one byte, distinguishing empty/missing."""
    if target.exists() and not target.is_file():
        raise RuntimeError(f"Proposed target `{path}` is not a regular file.")
    try:
        with target.open("rb") as stream:
            raw = stream.read(MAX_REVIEW_FILE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > MAX_REVIEW_FILE_BYTES:
        raise RuntimeError(f"File `{path}` exceeds the Gitlink safe-review size limit.")
    return raw


def _reject_linked_target(root: Path, relative: str) -> None:
    """Do not mutate a symlink or a target reached through a symlinked parent."""
    cursor = root
    for part in PurePosixPath(relative).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RuntimeError(f"Gitlink cannot apply changes through a symlink: `{relative}`.")


def _snapshot_change_set(
    local_root: str | Path,
    changes: list[dict[str, Any]],
    *,
    require_baselines: bool,
) -> list[ReviewedTarget]:
    root = Path(local_root).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("Choose an existing local repository directory before reviewing changes.")
    if not changes or len(changes) > MAX_REVIEW_FILES:
        raise RuntimeError(f"Gitlink requires 1–{MAX_REVIEW_FILES} proposed file changes per review.")

    seen: set[str] = set()
    total = 0
    snapshots: list[ReviewedTarget] = []
    for change in changes:
        if not isinstance(change, dict):
            raise RuntimeError("Gitlink received an invalid proposed file change.")
        path = change.get("path")
        if not isinstance(path, str):
            raise RuntimeError("Gitlink received a proposed file with no valid path.")
        relative = _normalize_repo_path(path)
        folded = relative.casefold()
        if folded in seen:
            raise RuntimeError(f"Gitlink proposal contains duplicate file changes for `{relative}`.")
        seen.add(folded)

        operation = change.get("operation", "update")
        if operation not in {"create", "update", "delete"}:
            raise RuntimeError(f"Gitlink proposal has an unknown operation for `{relative}`.")
        if operation != "delete":
            content = change.get("content")
            if not isinstance(content, str):
                raise RuntimeError(f"Proposed change for `{relative}` is missing its file content.")
            proposed_size = len(content.encode("utf-8"))
            if proposed_size > MAX_REVIEW_FILE_BYTES:
                raise RuntimeError(f"Proposed content for `{relative}` exceeds the safe-review size limit.")
            total += proposed_size

        _reject_linked_target(root, relative)
        target = _safe_local_target(root, relative)
        original = read_review_bytes(target, relative)
        total += len(original) if original is not None else 0
        if total > MAX_REVIEW_TOTAL_BYTES:
            raise RuntimeError("Gitlink change set exceeds the safe-review total size limit.")

        actual = hashlib.sha256(original).hexdigest() if original is not None else None
        if require_baselines:
            if BASELINE_FIELD not in change:
                raise RuntimeError(
                    "This proposal has no local-file baselines. Regenerate and review it before applying."
                )
            expected = change[BASELINE_FIELD]
            if expected is not None and (
                not isinstance(expected, str)
                or len(expected) != 64
                or any(c not in "0123456789abcdef" for c in expected)
            ):
                raise RuntimeError(f"Gitlink proposal has an invalid baseline for `{relative}`.")
            if actual != expected:
                raise RuntimeError(
                    f"Local file `{relative}` changed since the preview. "
                    "Nothing was written; regenerate and review the proposal."
                )
        elif operation == "create" and original is not None:
            raise RuntimeError(f"Cannot create `{relative}`: the file already exists locally.")
        elif operation in {"update", "delete"} and original is None:
            raise RuntimeError(f"Cannot {operation} `{relative}`: the local file is missing.")

        snapshots.append(ReviewedTarget(relative, target, original))
    return snapshots


def prepare_change_review(
    files: list[dict[str, Any]], local_root: str,
) -> tuple[list[dict[str, Any]], dict[str, str | None] | None]:
    """Bind server-read hashes to the proposal and capture the diff's originals.

    The preview must use the *same* bytes as the baselines. Returning a text
    snapshot avoids a second disk read that could silently show a different
    original from the one the approval guards.
    """
    clean_files = [dict(item) for item in files]
    if not clean_files or not (local_root or "").strip():
        # Preview generation with no checkout is still allowed for discovery,
        # but Apply will reject the unbound proposal.
        return clean_files, None
    snapshots = _snapshot_change_set(local_root, clean_files, require_baselines=False)
    originals: dict[str, str | None] = {}
    for change, snapshot in zip(clean_files, snapshots):
        change[BASELINE_FIELD] = (
            hashlib.sha256(snapshot.original).hexdigest() if snapshot.original is not None else None
        )
        originals[snapshot.path] = (
            _decode_text_bytes(snapshot.original) if snapshot.original is not None else None
        )
    return clean_files, originals


def select_gitlink_changes(
    pending_changes: list[dict[str, Any]], selected_paths: list[str] | None,
) -> list[dict[str, Any]]:
    """Freeze a caller-selected *subset* of a server-held approved proposal.

    The caller supplies only exact paths, never file contents, operations or
    baselines. None retains the old two-argument Apply intent's all-files
    behavior; an explicitly empty list means apply nothing and is rejected.
    The full proposal fingerprint is checked by the dispatcher BEFORE this
    selector runs, so selecting files cannot evade the existing approval.
    """
    if not isinstance(pending_changes, list) or not pending_changes or len(pending_changes) > MAX_REVIEW_FILES:
        raise RuntimeError("Gitlink has no valid approved file changes to select.")

    available: dict[str, dict[str, Any]] = {}
    canonical_paths: set[str] = set()
    for change in pending_changes:
        if not isinstance(change, dict) or not isinstance(change.get("path"), str):
            raise RuntimeError("Gitlink proposal contains an invalid file entry.")
        path = change["path"]
        if _normalize_repo_path(path) != path:
            raise RuntimeError("Gitlink proposal contains a noncanonical file path.")
        folded = path.casefold()
        if folded in canonical_paths:
            raise RuntimeError("Gitlink proposal contains duplicate file paths.")
        canonical_paths.add(folded)
        available[path] = change

    if selected_paths is None:
        requested = set(available)
    else:
        if not isinstance(selected_paths, list) or not selected_paths or len(selected_paths) > len(available):
            raise RuntimeError("Select at least one valid Gitlink file before applying.")
        if any(not isinstance(path, str) or path not in available for path in selected_paths):
            raise RuntimeError("Selected Gitlink files no longer match the approved proposal.")
        requested = set(selected_paths)
        if len(requested) != len(selected_paths):
            raise RuntimeError("The Gitlink selection contains duplicate file paths.")

    # Never follow user order or accept client-supplied file payloads. Apply
    # only the server's approved rows, in the same order as the preview.
    return [dict(change) for path, change in available.items() if path in requested]


def verify_change_review(
    local_root: str | Path, changes: list[dict[str, Any]],
) -> list[ReviewedTarget]:
    """Fail closed on stale/legacy proposals, before ANY filesystem writes."""
    return _snapshot_change_set(local_root, changes, require_baselines=True)


def assert_target_unchanged(local_root: str | Path, change: dict[str, Any], snapshot: ReviewedTarget) -> None:
    """Recheck one reviewed target just before writing it to narrow race windows."""
    root = Path(local_root).expanduser().resolve()
    _reject_linked_target(root, snapshot.path)
    target = _safe_local_target(root, change["path"])
    if target != snapshot.target or read_review_bytes(target, snapshot.path) != snapshot.original:
        raise RuntimeError(
            f"Local file `{snapshot.path}` changed while applying the proposal. "
            "Remaining changes were stopped."
        )
