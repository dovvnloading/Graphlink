"""Harness workspace binding (PLAN-2026-08-24 §3.2.5, H1: managed scratch
roots only).

A harness session binds to exactly one directory under
graphlink_scratch_dirs.HARNESS_WORKSPACE_ROOT, keyed by the node's own
durable harness_workspace_id (minted once at node creation - the
pycoder_repl_id precedent: node ids are reassigned by array position on
session reload, so they are not durable enough to name an on-disk
directory). Creation goes through prepare_scratch_dir so the 0700 chmod
and the private-root ownership check apply unchanged; ordinary use bumps
the directory's own mtime via touch_scratch_dir_usage so the launch age
sweep never mistakes an active workspace for an abandoned one.

Every path a tool touches resolves through resolve_in_workspace - the one
confinement choke point. Confinement here is a correctness/UX layer over
READ access (H1 ships read-only tools); the OS-level guard story for
mutating tools is H2's, per the plan's permissions-vs-sandbox split.
"""

from __future__ import annotations

from pathlib import Path

from graphlink_scratch_dirs import (
    HARNESS_WORKSPACE_ROOT,
    prepare_scratch_dir,
    safe_scratch_id,
    touch_scratch_dir_usage,
)


class WorkspaceError(ValueError):
    """A path that names something outside the bound workspace. Raised (not
    silently clamped) so tool handlers can feed the refusal back to the
    model as an ordinary error ToolResult it can reason about."""


def workspace_dir(workspace_id: str) -> Path:
    """The deterministic on-disk directory for a workspace id - computable
    from the raw id alone, the same recompute-don't-hold-a-reference shape
    remove_scratch_dir_for_id relies on for node-delete GC."""
    if not workspace_id:
        # A blank id would resolve to the shared "default" bucket more than
        # one node could collide on - the exact hazard
        # remove_scratch_dir_for_id refuses to delete through.
        raise WorkspaceError("harness workspace id is blank")
    return HARNESS_WORKSPACE_ROOT / safe_scratch_id(workspace_id)


def ensure_workspace(workspace_id: str) -> Path:
    """Create-or-reuse, with the shared permissioning applied; called at
    run start (and by tests). Also refreshes the usage mtime - a run IS a
    use, whether or not any tool ends up writing."""
    path = workspace_dir(workspace_id)
    prepare_scratch_dir(path)
    touch_scratch_dir_usage(path)
    return path


def resolve_in_workspace(workspace_id: str, raw_path: str) -> Path:
    """The confinement choke point every fs tool routes through: interprets
    `raw_path` (relative, or absolute-but-inside) against the workspace and
    returns a fully resolved Path proven to live under it.

    resolve() (not a lexical prefix check) so both `..` traversal and a
    symlink planted inside the workspace pointing out of it land on the
    REAL target before the containment test - the same class of hostile-
    disk input the scratch-root ownership check already treats as in-scope.
    The workspace root itself resolves first for the comparison, so a
    symlinked temp dir (macOS /tmp -> /private/tmp) never produces false
    refusals."""
    root = workspace_dir(workspace_id).resolve()
    candidate = Path(raw_path) if raw_path else Path(".")
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise WorkspaceError(
            f"path {raw_path!r} is outside this run's workspace - only files "
            "inside the workspace are accessible"
        )
    return resolved
