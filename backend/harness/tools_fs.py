"""H1's read-only workspace tools: fs.read / fs.list / fs.grep, all under
the fs.read scope with `auto` approval (the read-only posture every other
auto tool in backend/tools.py's vocabulary already takes).

Every handler resolves its path arguments through
workspace.resolve_under_root against the run's ONE bound root - the single
confinement choke point - and bounds its result before returning it (PLAN
§2.3: results are capped at the boundary so no tool can flood the context;
the same posture the MCP result caps and builder activity caps already
establish).

Handlers get that root from the duck-typed run context via _bound_root:
the loop resolves the root once at run start (a managed scratch dir, or a
trusted user directory) and sets ctx.harness_workspace_dir; a context with
neither that nor a scratch id degrades to an ordinary error ToolResult,
never a crash.
"""

from __future__ import annotations

import asyncio
import ctypes
import difflib
import errno
import hashlib
import os
import secrets
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import regex as re

from backend.harness.image_read import IMAGE_SUFFIXES, read_image_preview
from backend.harness.workspace import WorkspaceError, resolve_under_root, workspace_dir
from backend.providers.base import ToolCall, ToolSpec
from backend.tools import FS_READ, FS_WRITE, RunContext, ToolRegistry, ToolResult

_READ_CAP_CHARS = 40_000
# A result cap isn't a disk-read cap: fs.read previously loaded an entire
# arbitrarily large file into RAM before trimming its output to 40K chars.
# fs.edit did the same before a replacement. Bound input before decoding.
_MAX_FS_FILE_BYTES = 8 * 1024 * 1024
# Path.glob can enumerate millions of entries before fs.list applies its
# output cap. Bound discovery before sorting; require a narrower glob.
_MAX_GLOB_DISCOVERY = 10_000
_PROTECTED_METADATA_DIRS = frozenset({".git", ".hg", ".svn"})
# H2 write bounds: a single fs.write/fs.edit call's content ceiling. Wire
# input is untrusted model output - the same bounds-everywhere posture
# every other cap in this file takes.
_WRITE_CAP_CHARS = 200_000
_LIST_CAP_ENTRIES = 500
_GREP_CAP_MATCHES = 100
_GREP_LINE_CAP = 400
_GREP_FILE_SIZE_CAP = 2_000_000  # bytes; larger files are skipped, and say so
_GREP_SCANNED_FILE_CAP = 5_000  # bounded scan even for zero matches
_GREP_PATTERN_CAP = 500
# Python stdlib re has no timeout. An attacker-controlled pattern such as
# (a+)+$ against a matching file can run for hours under an auto-approved
# read scope. The regex engine is already pinned in the dependency lock;
# enforce per-match, per-file and aggregate deadlines.
_GREP_MATCH_TIMEOUT_SECONDS = 0.05
_GREP_FILE_BUDGET_SECONDS = 0.25  # cumulative scanning of many individually quick lines
_GREP_TOTAL_BUDGET_SECONDS = 3.0

FS_READ_SPEC = ToolSpec(
    name="fs.read",
    description=(
        "Read workspace text, images (PNG, JPEG, WebP, GIF), PDF (.pdf), "
        "Word (.docx), or Excel (.xlsx/.xlsm). Images yield a bounded "
        "visual preview on vision models or size/format text on other models. "
        "path is workspace-relative. Optional offset (0-based line) "
        "and limit (line count) window the extracted lines. PDF page_start/"
        "page_end and Excel sheet_start/sheet_end are 1-based inclusive "
        "ranges; Excel sheet selects a sheet by name instead. The input "
        f"file cap is {_MAX_FS_FILE_BYTES} bytes and output is truncated "
        f"past {_READ_CAP_CHARS} characters."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
            "page_start": {"type": "integer"},
            "page_end": {"type": "integer"},
            "sheet_start": {"type": "integer"},
            "sheet_end": {"type": "integer"},
            "sheet": {"type": "string"},
        },
        "required": ["path"],
    },
)

FS_LIST_SPEC = ToolSpec(
    name="fs.list",
    description=(
        "List files in the workspace. Optional glob pattern (e.g. '**/*.py') "
        "filters the listing; default lists everything. Returns "
        "workspace-relative paths, directories suffixed with '/'."
    ),
    input_schema={
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": [],
    },
)

FS_WRITE_SPEC = ToolSpec(
    name="fs.write",
    description=(
        "Create or overwrite one text file in the workspace with the given "
        "content. path is workspace-relative; parent directories are "
        "created as needed. Overwrites silently - fs.read first if the "
        "current content matters."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
)

FS_EDIT_SPEC = ToolSpec(
    name="fs.edit",
    description=(
        "Edit one file by exact string replacement: old_string must occur "
        "EXACTLY ONCE in the file (the call fails on zero or multiple "
        "occurrences - include more surrounding context to disambiguate); "
        "it is replaced with new_string."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["path", "old_string", "new_string"],
    },
)

FS_GREP_SPEC = ToolSpec(
    name="fs.grep",
    description=(
        "Search workspace files for a Python-flavored regular expression. "
        "Optional glob restricts which files are searched (default "
        "'**/*'). Returns 'path:line: text' matches, first "
        f"{_GREP_CAP_MATCHES} only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "glob": {"type": "string"},
        },
        "required": ["pattern"],
    },
)


def _bound_root(ctx: RunContext) -> Path | None:
    """The run's ONE confinement root, off the context. The loop resolves
    it once (scratch dir or trusted user dir) and sets harness_workspace_dir;
    an older caller that set only harness_workspace_id still works, binding
    that id's scratch dir. None means no workspace is bound at all."""
    root = getattr(ctx, "harness_workspace_dir", None)
    if isinstance(root, Path):
        return root.resolve()
    workspace_id = getattr(ctx, "harness_workspace_id", None)
    if isinstance(workspace_id, str) and workspace_id:
        return workspace_dir(workspace_id).resolve()
    return None


def _is_metadata_path(path: Path) -> bool:
    # .git/hooks and .git/config can turn a mundane file edit into code
    # execution on the NEXT git command. Do not let the FS_READ auto scope
    # send private VCS metadata to a model, or FS_WRITE modify hooks.
    return any(part.casefold() in _PROTECTED_METADATA_DIRS for part in path.parts)


def _read_bounded_bytes(path: Path, *, max_bytes: int | None = None) -> bytes:
    # Read one extra byte to detect growth even when a preceding stat()
    # claimed that the file was within bounds. Office parsers consume only
    # these checked bytes, never a second unbounded read of the source path.
    if max_bytes is None:
        max_bytes = _MAX_FS_FILE_BYTES
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise WorkspaceError(f"File exceeds the {max_bytes}-byte harness read limit.")
    return data


def _read_bounded_text(path: Path, *, errors: str = "strict", max_bytes: int | None = None) -> str:
    return _read_bounded_bytes(path, max_bytes=max_bytes).decode("utf-8", errors=errors)


def _create_exclusively_without_links(staged_path: Path, target: Path) -> None:
    """Atomically create only if absent on filesystems without hard links.

    An exclusive open followed by a copy publishes an empty/partial file.
    Use a native no-replace rename instead. If the filesystem cannot honor
    that primitive, fail closed rather than weaken an approved write.
    """
    if os.name == "nt":
        # Windows os.rename uses MoveFileExW without REPLACE_EXISTING.
        os.rename(staged_path, target)
        return

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        cwd_fd, flags = getattr(os, "AT_FDCWD", -100), 0x1  # RENAME_NOREPLACE
    elif sys.platform == "darwin":
        rename = getattr(libc, "renameatx_np", None)
        cwd_fd, flags = getattr(os, "AT_FDCWD", -2), 0x4  # RENAME_EXCL
    else:
        rename = None
    if rename is None:
        raise WorkspaceError("This filesystem cannot atomically create an approved file without hard links.")
    rename.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(cwd_fd, os.fsencode(staged_path), cwd_fd, os.fsencode(target), flags) != 0:
        error = ctypes.get_errno()
        if error in (errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL):
            raise WorkspaceError(
                "This filesystem cannot atomically create an approved file without hard links."
            ) from OSError(error, os.strerror(error))
        raise OSError(error, os.strerror(error))

def _exchange_approved_file(target: Path, staged_path: Path) -> Path:
    """Publish while retaining the *actual* displaced file for validation.

    A pre-replace hash alone is a TOCTOU check. On POSIX, rename exchange
    atomically swaps the two inodes; on Windows, ReplaceFileW keeps the
    displaced target as a backup. Never fall back to an unconditional
    os.replace for an approved overwrite on an unsupported filesystem.
    Returns the path holding the displaced original.
    """
    if os.name == "nt":
        backup = staged_path.with_name(staged_path.name + ".before")
        replace_file = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
        replace_file.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
        ]
        replace_file.restype = ctypes.c_int
        if not replace_file(str(target), str(staged_path), str(backup), 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        return backup

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(libc, "renameatx_np", None)
        cwd_fd = getattr(os, "AT_FDCWD", -2)
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        cwd_fd = getattr(os, "AT_FDCWD", -100)
    else:
        rename = None
        cwd_fd = -1
    if rename is None:
        raise WorkspaceError("This filesystem cannot safely commit a version-guarded overwrite.")
    rename.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    # Linux RENAME_EXCHANGE and macOS RENAME_SWAP are both flag 0x2.
    if rename(cwd_fd, os.fsencode(target), cwd_fd, os.fsencode(staged_path), 0x2) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return staged_path


def _displaced_file_matches_approval(displaced: Path, approved: FileApprovalState) -> bool:
    """Validate the actual displaced inode, ignoring rename-induced ctime.

    Unlike ctime, mode and ownership do not change when an inode is renamed.
    They MUST still match the preview: a raced chmod/chown is a stale approval
    even if the inode, mtime and contents are unchanged.
    """
    if approved.digest is None or approved.version is None or len(approved.version) != 7:
        return False
    expected = (
        approved.version[0], approved.version[1], approved.version[2],
        *approved.version[4:],
    )

    def matches() -> bool:
        info = displaced.lstat()
        return stat.S_ISREG(info.st_mode) and (
            info.st_dev, info.st_ino, info.st_mtime_ns,
            stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid,
        ) == expected

    try:
        if not matches():
            return False
        digest = hashlib.sha256(_read_bounded_bytes(displaced)).hexdigest()
        return digest == approved.digest and matches()
    except (OSError, WorkspaceError):
        return False


class _ApprovedWriteRecoveryError(WorkspaceError):
    """The staging/backup path holds data that cleanup must not unlink."""


def _staged_output_identity(path: Path) -> tuple[int, ...]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise WorkspaceError("The staged approved write is no longer a regular file.")
    return (
        info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size,
        stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid,
    )


def _is_our_published_file(
    path: Path, identity: tuple[int, ...], digest: str, *, ctime_ns: int | None = None,
) -> bool:
    """Check that a recovery would move only the version we published.

    This is a best-effort recovery precondition; an external rename after the
    exchange must never be blindly swapped out and then unlinked as our temp.
    """
    try:
        before = path.lstat()
        if (ctime_ns is not None and before.st_ctime_ns != ctime_ns):
            return False
        if _staged_output_identity(path) != identity:
            return False
        actual = hashlib.sha256(_read_bounded_bytes(path)).hexdigest()
        after = path.lstat()
        return (
            actual == digest
            and after.st_ctime_ns == before.st_ctime_ns
            and (ctime_ns is None or after.st_ctime_ns == ctime_ns)
            and _staged_output_identity(path) == identity
        )
    except (OSError, WorkspaceError):
        return False


def _replace_approved_existing(target: Path, staged_path: Path, approved: FileApprovalState) -> None:
    # Fingerprint our staged inode before publishing. If someone writes to
    # the published pathname afterwards, rollback cannot treat it as ours.
    staged_identity = _staged_output_identity(staged_path)
    staged_digest = hashlib.sha256(_read_bounded_bytes(staged_path)).hexdigest()
    try:
        displaced = _exchange_approved_file(target, staged_path)
    except BaseException as exc:
        # Native exchange errors normally leave both paths untouched, but
        # defensively retain staging if a partial OS failure or wrapper has
        # already moved the displaced external version there.
        backup = staged_path.with_name(staged_path.name + ".before")
        external_may_be_staged = (
            staged_path.exists()
            and not _is_our_published_file(staged_path, staged_identity, staged_digest)
        )
        if external_may_be_staged or (os.name == "nt" and backup.exists()):
            raise _ApprovedWriteRecoveryError(
                f"{_STALE_FILE_APPROVAL} Publication failed after moving filesystem "
                f"data; inspect {target}, {staged_path} and {backup}: {exc}"
            ) from exc
        raise

    restored = False
    try:
        try:
            published_ctime = target.lstat().st_ctime_ns
        except OSError:
            published_ctime = None

        if not _displaced_file_matches_approval(displaced, approved):
            # If target has changed since our exchange, a rollback would
            # overwrite a newer writer's result. Preserve BOTH paths.
            if published_ctime is None or not _is_our_published_file(
                target, staged_identity, staged_digest, ctime_ns=published_ctime,
            ):
                raise _ApprovedWriteRecoveryError(
                    f"{_STALE_FILE_APPROVAL} A newer destination was left untouched. "
                    f"The displaced external version is preserved at {displaced}; "
                    f"inspect both paths before retrying."
                )
            try:
                if displaced == staged_path:
                    _exchange_approved_file(target, staged_path)
                else:
                    # Windows ReplaceFileW keeps the displaced version in a
                    # backup. Rename moves the current target out of the way;
                    # unlike replace, the second rename won't overwrite a
                    # new file that appeared at the destination.
                    os.rename(target, staged_path)
                    os.rename(displaced, target)
            except (OSError, WorkspaceError) as exc:
                raise _ApprovedWriteRecoveryError(
                    f"{_STALE_FILE_APPROVAL} Automatic restoration failed; "
                    f"recovery data is preserved at {displaced} and possibly {staged_path}. "
                    f"The destination may contain the unapproved agent version: {exc}"
                ) from exc
            # A writer can race even this conditional restore. Check what
            # landed at staging and never unlink it unless it is still ours.
            if not _is_our_published_file(staged_path, staged_identity, staged_digest):
                raise _ApprovedWriteRecoveryError(
                    f"{_STALE_FILE_APPROVAL} A concurrent write was displaced during "
                    f"recovery and is preserved at {staged_path}; inspect both paths."
                )
            restored = True
            raise WorkspaceError(_STALE_FILE_APPROVAL)

        if displaced != staged_path:
            displaced.unlink(missing_ok=True)
    except BaseException as exc:
        if restored or isinstance(exc, _ApprovedWriteRecoveryError):
            raise
        # Any unexpected exception AFTER the first exchange can leave the
        # external version at staging. Do not let caller cleanup destroy it.
        raise _ApprovedWriteRecoveryError(
            f"{_STALE_FILE_APPROVAL} Validation or cleanup failed after publication; "
            f"data was retained at {displaced} (and possibly {staged_path}). "
            f"Inspect the destination and recovery paths: {exc}"
        ) from exc

def _write_atomic_text(
    target: Path, content: str, *, approval_state: FileApprovalState | None = None,
) -> None:
    """Replace text only after the complete UTF-8 payload is safely staged.

    In particular, a failed fs.edit must never truncate the source file.
    Using a temp file beside the destination keeps os.replace atomic on the
    same filesystem. newline="" preserves caller-supplied LF/CRLF exactly.
    Existing POSIX mode bits (including executable) survive an edit.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staged_path: Path | None = None
    try:
        # The exclusive "x" mode creates a unique sibling with the same
        # 0o666 & umask permissions as an ordinary new text file. mkstemp/
        # NamedTemporaryFile force 0o600 even with umask 0o022, which made
        # new files under shared user workspaces unexpectedly private.
        # A random, unguessable name and O_EXCL prevent overwriting another
        # file; keep the temp path only after creation succeeds.
        for _attempt in range(3):
            candidate = target.parent / f".{target.name}.{secrets.token_hex(16)}.tmp"
            try:
                with candidate.open("x", encoding="utf-8", newline="") as staged:
                    staged_path = candidate
                    if target.exists():
                        # A new "x" file respects the default umask (often
                        # world-readable 0644). For an OVERWRITE, make the
                        # temporary private BEFORE any new content is written;
                        # restore the original mode only after staging ends.
                        os.chmod(candidate, 0o600)
                    staged.write(content)
                    staged.flush()
                    os.fsync(staged.fileno())
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError("Could not allocate an exclusive staging file.")
        if target.exists():
            os.chmod(staged_path, stat.S_IMODE(target.stat().st_mode))
        # Check as late as possible, after staging but before committing any
        # bytes. A human-approved diff is conditional on the exact version
        # disclosed, not permission to overwrite an external edit.
        if approval_state is not None:
            if not _file_approval_state_matches(target, approval_state):
                raise WorkspaceError(_STALE_FILE_APPROVAL)
            if approval_state.digest is None:
                # Atomic create-if-absent: even a file appearing between the
                # last check and commit must not be overwritten.
                try:
                    os.link(staged_path, target)
                except FileExistsError as exc:
                    raise WorkspaceError(_STALE_FILE_APPROVAL) from exc
                except (OSError, NotImplementedError) as exc:
                    # Writable FAT/exFAT and some shared mounts reject hard
                    # links; an exclusive create still protects absence.
                    unsupported = {errno.EPERM, errno.EACCES, errno.ENOSYS,
                                   errno.EOPNOTSUPP, errno.EXDEV, errno.EINVAL}
                    if isinstance(exc, OSError) and exc.errno not in unsupported:
                        raise
                    try:
                        _create_exclusively_without_links(staged_path, target)
                    except FileExistsError as conflict:
                        raise WorkspaceError(_STALE_FILE_APPROVAL) from conflict
                return
            _replace_approved_existing(target, staged_path, approval_state)
            return
        os.replace(staged_path, target)
    except _ApprovedWriteRecoveryError:
        # A stale-write conflict or failed rollback can leave the external
        # writer's only copy in staging. It must survive this error path.
        staged_path = None
        raise
    finally:
        if staged_path is not None:
            try:
                staged_path.unlink(missing_ok=True)
            except OSError:
                pass


def _iter_under_root(root: Path, pattern: str):
    """Bounded and stable root-relative iteration, excluding VCS metadata."""
    discovered: list[tuple[Path, str]] = []
    for path in root.glob(pattern or "**/*"):
        resolved = resolve_under_root(root, str(path))
        if _is_metadata_path(resolved):
            continue
        discovered.append((resolved, resolved.relative_to(root).as_posix()))
        if len(discovered) > _MAX_GLOB_DISCOVERY:
            raise WorkspaceError("Too many files match the glob; use a narrower pattern.")
    for item in sorted(discovered, key=lambda row: row[1]):
        yield item


_STALE_FILE_APPROVAL = "File changed since approval preview; review the current file and retry."


@dataclass(frozen=True)
class FileApprovalState:
    # A missing file has no digest/version; a present file is bound to both
    # exact bytes and its disk identity/timestamps (including same-byte rewrites).
    target: Path
    digest: str | None
    version: tuple[int, int, int, int, int, int, int] | None


@dataclass(frozen=True)
class FileApprovalPreview:
    summary: str
    available: bool
    state: FileApprovalState | None = None


def _file_version(target: Path) -> tuple[int, int, int, int, int, int, int]:
    info = target.stat()
    return (
        info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns,
        stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid,
    )


def _file_approval_state_matches(target: Path, approved: FileApprovalState) -> bool:
    """Fail closed if the disclosed target, absence or bytes changed."""
    if target != approved.target:
        return False
    try:
        if approved.digest is None:
            return not target.exists() and not target.is_symlink()
        if not target.is_file() or _file_version(target) != approved.version:
            return False
        actual = hashlib.sha256(_read_bounded_bytes(target)).hexdigest()
        return actual == approved.digest and _file_version(target) == approved.version
    except (WorkspaceError, OSError, UnicodeError):
        return False


def preview_harness_file_change(call: ToolCall, root: Path | None) -> FileApprovalPreview:
    """Disclose the *result* of a pending fs.write/fs.edit, without writing.

    Resolve and read through the same confined, byte-bounded helpers as the
    actual tools. An overwrite or edit is shown as a unified diff against the
    file on disk when consent is requested; a new file shows its entire body.
    Never invent a diff if the current file cannot safely be read.
    """
    path = str(call.arguments.get("path") or "")
    heading = f"{call.name} {path}"
    if root is None:
        return FileApprovalPreview(f"{heading}\nPreview unavailable: No harness workspace is bound to this run.", False)

    try:
        target = resolve_under_root(root, path)
        if target == root:
            raise WorkspaceError("path names the workspace root, not a file.")
        if _is_metadata_path(target):
            raise WorkspaceError("VCS metadata cannot be modified by harness filesystem tools.")
        is_new = not target.exists()
        if is_new:
            if call.name == "fs.edit":
                raise WorkspaceError(f"Not a file: {path!r}.")
            current = ""
            state = FileApprovalState(target, None, None)
        else:
            if not target.is_file():
                raise WorkspaceError(f"Not a file: {path!r}.")
            source = _read_bounded_bytes(target)
            current = source.decode("utf-8")
            state = FileApprovalState(
                target, hashlib.sha256(source).hexdigest(), _file_version(target),
            )

        if call.name == "fs.write":
            proposed = call.arguments.get("content")
            if not isinstance(proposed, str):
                raise WorkspaceError("fs.write needs string content.")
            if len(proposed) > _WRITE_CAP_CHARS:
                raise WorkspaceError(f"Content longer than {_WRITE_CAP_CHARS} characters.")
        elif call.name == "fs.edit":
            old = call.arguments.get("old_string")
            new = call.arguments.get("new_string")
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise WorkspaceError("fs.edit needs a non-empty old_string and a new_string.")
            if len(new) > _WRITE_CAP_CHARS:
                raise WorkspaceError(f"Replacement longer than {_WRITE_CAP_CHARS} characters.")
            count = current.count(old)
            if count != 1:
                raise WorkspaceError(f"old_string occurs {count} times; expected exactly once.")
            proposed = current.replace(old, new, 1)
            if len(proposed.encode("utf-8")) > _MAX_FS_FILE_BYTES:
                raise WorkspaceError("Edited file would exceed the permitted size.")
        else:
            raise WorkspaceError("Not a workspace file mutation.")
    except (WorkspaceError, OSError, UnicodeError) as exc:
        return FileApprovalPreview(f"{heading}\nPreview unavailable: {exc}\nReview the current file before approving.", False)

    if is_new:
        # Do not add a diff prefix to proposed content: it must be shown
        # exactly as supplied, including any whitespace and final newline.
        return FileApprovalPreview(f"{heading}\n--- new file: {path} (full content)\n{proposed}", True, state)

    relative = target.relative_to(root.resolve()).as_posix()
    changes = difflib.unified_diff(
        current.splitlines(keepends=True),
        proposed.splitlines(keepends=True),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
        lineterm="\n",
    )
    preview: list[str] = []
    for line in changes:
        preview.append(line)
        if not line.endswith(("\n", "\r")):
            # The last line of a text file need not have a newline.
            # Without a marker, -old and +new would run together.
            preview.append("\n\\ No newline at end of file\n")
    return FileApprovalPreview(f"{heading}\n{''.join(preview)}" if preview else f"{heading}\nNo content changes.", True, state)


def register_harness_fs_tools(registry: ToolRegistry) -> None:
    async def fs_read(call: ToolCall, ctx: RunContext) -> ToolResult:
        root = _bound_root(ctx)
        if root is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        try:
            target = resolve_under_root(root, str(call.arguments.get("path") or ""))
        except WorkspaceError as exc:
            return ToolResult(content=str(exc), is_error=True)
        if _is_metadata_path(target):
            return ToolResult(content="VCS metadata is not accessible through harness filesystem tools.", is_error=True)
        if not target.is_file():
            return ToolResult(content=f"Not a file: {call.arguments.get('path')!r}.", is_error=True)
        suffix = target.suffix.lower()
        selectors = ("page_start", "page_end", "sheet_start", "sheet_end", "sheet")
        if suffix in (".doc", ".xls"):
            return ToolResult(
                content="Legacy .doc/.xls files are not supported. Convert to .docx/.xlsx first.",
                is_error=True,
            )
        if suffix not in (".pdf", ".docx", ".xlsx", ".xlsm") and any(
            call.arguments.get(name) is not None for name in selectors
        ):
            return ToolResult(content="Page and sheet selectors require a PDF or Excel file.", is_error=True)
        if suffix in IMAGE_SUFFIXES and any(
            call.arguments.get(name) is not None for name in ("offset", "limit")
        ):
            return ToolResult(content="Line offsets and limits require a text or document file.", is_error=True)
        try:
            if suffix in IMAGE_SUFFIXES:
                data = await asyncio.to_thread(_read_bounded_bytes, target)
                preview = await asyncio.to_thread(read_image_preview, data)
                return ToolResult(content=preview.description, image_bytes=preview.image_bytes)
            if suffix in (".pdf", ".docx", ".xlsx", ".xlsm"):
                from backend.harness.document_read import extract_document_text

                # Keep source I/O off the event loop, just like extraction.
                data = await asyncio.to_thread(_read_bounded_bytes, target)
                text = await asyncio.to_thread(
                    extract_document_text,
                    data,
                    suffix,
                    **{name: call.arguments.get(name) for name in selectors},
                )
            else:
                # Text reads can be large; do not block other read tasks
                # on the event loop while this one performs disk I/O.
                text = await asyncio.to_thread(_read_bounded_text, target, errors="replace")
        except (OSError, WorkspaceError, ValueError) as exc:
            return ToolResult(content=f"Could not read file: {exc}", is_error=True)
        except Exception as exc:
            # An encrypted/malformed Office package or a third-party parser
            # error is a normal failed tool call, not a harness-loop crash.
            return ToolResult(content=f"Could not read document: {exc}", is_error=True)
        lines = text.splitlines()
        try:
            offset = max(0, int(call.arguments.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = int(call.arguments.get("limit") or 0)
        except (TypeError, ValueError):
            limit = 0
        window = lines[offset : offset + limit] if limit > 0 else lines[offset:]
        body = "\n".join(window)
        if len(body) > _READ_CAP_CHARS:
            body = body[:_READ_CAP_CHARS] + f"\n…[truncated at {_READ_CAP_CHARS} characters]"
        header = f"{len(lines)} line(s) total; showing from line {offset + 1}.\n"
        return ToolResult(content=header + body)

    def _fs_list_sync(call: ToolCall, ctx: RunContext) -> ToolResult:
        root = _bound_root(ctx)
        if root is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        pattern = str(call.arguments.get("pattern") or "**/*")
        entries: list[str] = []
        truncated = False
        try:
            for resolved, relative in _iter_under_root(root, pattern):
                if len(entries) >= _LIST_CAP_ENTRIES:
                    truncated = True
                    break
                entries.append(relative + "/" if resolved.is_dir() else relative)
        except (WorkspaceError, OSError, ValueError) as exc:
            return ToolResult(content=f"Could not list {pattern!r}: {exc}", is_error=True)
        if not entries:
            return ToolResult(content="The workspace has no files matching that pattern.")
        suffix = f"\n…[capped at {_LIST_CAP_ENTRIES} entries]" if truncated else ""
        return ToolResult(content="\n".join(entries) + suffix)

    async def fs_list(call: ToolCall, ctx: RunContext) -> ToolResult:
        # Directory traversal and glob expansion also need a worker thread
        # for concurrent reads to make progress.
        return await asyncio.to_thread(_fs_list_sync, call, ctx)

    def _fs_grep_sync(call: ToolCall, ctx: RunContext) -> ToolResult:
        root = _bound_root(ctx)
        if root is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        raw_pattern = str(call.arguments.get("pattern") or "")
        if not raw_pattern:
            return ToolResult(content="fs.grep needs a non-empty pattern.", is_error=True)
        if len(raw_pattern) > _GREP_PATTERN_CAP:
            return ToolResult(content=f"Pattern longer than {_GREP_PATTERN_CAP} characters.", is_error=True)
        try:
            expression = re.compile(raw_pattern)
        except re.error as exc:
            return ToolResult(content=f"Invalid regular expression: {exc}", is_error=True)
        file_glob = str(call.arguments.get("glob") or "**/*")
        matches: list[str] = []
        skipped_large = 0
        scanned_files = 0
        scan_capped = False
        search_deadline = time.monotonic() + _GREP_TOTAL_BUDGET_SECONDS
        try:
            for resolved, relative in _iter_under_root(root, file_glob):
                if time.monotonic() >= search_deadline:
                    return ToolResult(content="fs.grep exceeded its search time budget.", is_error=True)
                if len(matches) >= _GREP_CAP_MATCHES:
                    break
                if not resolved.is_file():
                    continue
                if scanned_files >= _GREP_SCANNED_FILE_CAP:
                    scan_capped = True
                    break
                scanned_files += 1
                try:
                    # The file may grow after stat(); bound actual read bytes.
                    text = _read_bounded_text(
                        resolved, errors="replace", max_bytes=_GREP_FILE_SIZE_CAP,
                    )
                except WorkspaceError:
                    skipped_large += 1
                    continue
                except OSError:
                    continue
                if "\x00" in text[:1024]:
                    continue  # binary
                file_deadline = time.monotonic() + _GREP_FILE_BUDGET_SECONDS
                for line_number, line in enumerate(text.splitlines(), start=1):
                    now = time.monotonic()
                    if now >= search_deadline:
                        return ToolResult(content="fs.grep exceeded its search time budget.", is_error=True)
                    if now >= file_deadline:
                        return ToolResult(content="fs.grep exceeded its per-file regex time budget.", is_error=True)
                    try:
                        found = expression.search(
                            line,
                            timeout=min(_GREP_MATCH_TIMEOUT_SECONDS, file_deadline - now, search_deadline - now),
                            concurrent=True,  # immutable line; release the GIL while matching
                        )
                    except TimeoutError:
                        # The regex engine, not a thread timeout, interrupts
                        # both pathological matches and exhausted deadlines.
                        now = time.monotonic()
                        if now >= search_deadline:
                            return ToolResult(content="fs.grep exceeded its search time budget.", is_error=True)
                        if now >= file_deadline:
                            return ToolResult(content="fs.grep exceeded its per-file regex time budget.", is_error=True)
                        return ToolResult(content="fs.grep regular expression timed out; simplify the pattern.", is_error=True)
                    if found:
                        snippet = line if len(line) <= _GREP_LINE_CAP else line[:_GREP_LINE_CAP] + "…"
                        matches.append(f"{relative}:{line_number}: {snippet}")
                        if len(matches) >= _GREP_CAP_MATCHES:
                            break
        except (WorkspaceError, OSError, ValueError) as exc:
            return ToolResult(content=f"Could not search: {exc}", is_error=True)
        if not matches:
            notes = []
            if skipped_large:
                notes.append(f"{skipped_large} file(s) skipped as too large")
            if scan_capped:
                notes.append(f"search capped at {_GREP_SCANNED_FILE_CAP} files")
            note = f" ({'; '.join(notes)})" if notes else ""
            return ToolResult(content=f"No matches{note}.")
        footer = ""
        if len(matches) >= _GREP_CAP_MATCHES:
            footer = f"\n…[capped at {_GREP_CAP_MATCHES} matches]"
        if skipped_large:
            footer += f"\n[{skipped_large} file(s) skipped as too large]"
        if scan_capped:
            footer += f"\n[search capped at {_GREP_SCANNED_FILE_CAP} files]"
        return ToolResult(content="\n".join(matches) + footer)

    async def fs_grep(call: ToolCall, ctx: RunContext) -> ToolResult:
        # Glob, file I/O and regex searches are synchronous and may be costly.
        # Run them off the FastAPI event loop. Threading only provides
        # responsiveness; the regex engine's timeout enforces the CPU bound.
        return await asyncio.to_thread(_fs_grep_sync, call, ctx)

    async def fs_write(call: ToolCall, ctx: RunContext) -> ToolResult:
        root = _bound_root(ctx)
        if root is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        content = call.arguments.get("content")
        if not isinstance(content, str):
            return ToolResult(content="fs.write needs string content.", is_error=True)
        if len(content) > _WRITE_CAP_CHARS:
            return ToolResult(
                content=f"Content longer than {_WRITE_CAP_CHARS} characters - split it into parts.",
                is_error=True,
            )
        try:
            target = resolve_under_root(root, str(call.arguments.get("path") or ""))
        except WorkspaceError as exc:
            return ToolResult(content=str(exc), is_error=True)
        if target == root:
            return ToolResult(content="path names the workspace root, not a file.", is_error=True)
        if _is_metadata_path(target):
            return ToolResult(content="VCS metadata cannot be modified by harness filesystem tools.", is_error=True)
        states = getattr(ctx, "harness_file_approval_states", {})
        approved_state = states.pop(call.id, None)
        if approved_state is not None and not _file_approval_state_matches(target, approved_state):
            return ToolResult(content=_STALE_FILE_APPROVAL, is_error=True)
        try:
            _write_atomic_text(target, content, approval_state=approved_state)
        except (OSError, WorkspaceError) as exc:
            return ToolResult(content=f"Could not write file: {exc}", is_error=True)
        return ToolResult(content=f"Wrote {len(content)} characters to {call.arguments.get('path')}.")

    async def fs_edit(call: ToolCall, ctx: RunContext) -> ToolResult:
        root = _bound_root(ctx)
        if root is None:
            return ToolResult(content="No harness workspace is bound to this run.", is_error=True)
        old_string = call.arguments.get("old_string")
        new_string = call.arguments.get("new_string")
        if not isinstance(old_string, str) or not old_string or not isinstance(new_string, str):
            return ToolResult(content="fs.edit needs a non-empty old_string and a new_string.", is_error=True)
        if len(new_string) > _WRITE_CAP_CHARS:
            return ToolResult(
                content=f"Replacement longer than {_WRITE_CAP_CHARS} characters.", is_error=True,
            )
        try:
            target = resolve_under_root(root, str(call.arguments.get("path") or ""))
        except WorkspaceError as exc:
            return ToolResult(content=str(exc), is_error=True)
        if _is_metadata_path(target):
            return ToolResult(content="VCS metadata cannot be modified by harness filesystem tools.", is_error=True)
        states = getattr(ctx, "harness_file_approval_states", {})
        approved_state = states.pop(call.id, None)
        if approved_state is not None and not _file_approval_state_matches(target, approved_state):
            return ToolResult(content=_STALE_FILE_APPROVAL, is_error=True)
        if not target.is_file():
            return ToolResult(content=f"Not a file: {call.arguments.get('path')!r}.", is_error=True)
        try:
            text = _read_bounded_text(target)
        except (OSError, UnicodeError, WorkspaceError) as exc:
            return ToolResult(content=f"Could not read file: {exc}", is_error=True)
        occurrences = text.count(old_string)
        if occurrences == 0:
            return ToolResult(content="old_string was not found in the file - fs.read it and retry with exact text.", is_error=True)
        if occurrences > 1:
            return ToolResult(
                content=f"old_string occurs {occurrences} times - include more surrounding context so it is unique.",
                is_error=True,
            )
        replacement = text.replace(old_string, new_string, 1)
        if len(replacement.encode("utf-8")) > _MAX_FS_FILE_BYTES:
            return ToolResult(content="Edited file would exceed the permitted size.", is_error=True)
        try:
            _write_atomic_text(target, replacement, approval_state=approved_state)
        except (OSError, WorkspaceError) as exc:
            return ToolResult(content=f"Could not write file: {exc}", is_error=True)
        return ToolResult(content=f"Edited {call.arguments.get('path')}.")

    registry.register(FS_READ_SPEC, fs_read, scopes={FS_READ}, approval="auto", parallel_safe=True)
    registry.register(FS_LIST_SPEC, fs_list, scopes={FS_READ}, approval="auto", parallel_safe=True)
    registry.register(FS_GREP_SPEC, fs_grep, scopes={FS_READ}, approval="auto", parallel_safe=True)
    # H2: writes prompt once per distinct call in a run (the managed-
    # workspace posture the plan's §3.2.6 sets; "always" with its
    # fingerprint memory would re-prompt only on ARGUMENT changes anyway -
    # "once" is the honest name for a scratch-confined write).
    registry.register(FS_WRITE_SPEC, fs_write, scopes={FS_WRITE}, approval="once")
    registry.register(FS_EDIT_SPEC, fs_edit, scopes={FS_WRITE}, approval="once")


USAGE_HINT = "fs.list locates files; fs.read reads text/images/PDF/Word/Excel (page and sheet ranges supported); fs.grep searches text; fs.write and fs.edit change files (with approval)."


def register_workspace_tools(registry: ToolRegistry, dispatcher, document) -> None:
    register_harness_fs_tools(registry)
