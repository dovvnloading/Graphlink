"""Harness workspace binding (PLAN-2026-08-24 §3.2.5).

A harness run binds to exactly ONE root directory, resolved once at run
start (backend/harness/loop.py) and carried on the run context. It is one
of two kinds:

- a MANAGED scratch directory under graphlink_scratch_dirs.
  HARNESS_WORKSPACE_ROOT, keyed by the node's durable harness_workspace_id
  (the pycoder_repl_id precedent: node ids are reassigned by array
  position on reload, so they cannot name an on-disk directory). Created
  via prepare_scratch_dir so the 0700 chmod and private-root ownership
  check apply; usage bumps its mtime so the launch age-sweep never treats
  an active workspace as abandoned. This is the default.

- a USER directory the person explicitly granted through the folder
  picker (harness_workspace_path). This is real work in a real project
  folder, so it is NOT chmod-restricted, NOT age-swept, and NOT deleted
  when the node is deleted - it is the user's own directory, not scratch.

THE TRUST GATE (bound_root) is the security boundary for the user-dir
case: a node's harness_workspace_path is honored ONLY if that exact
resolved path is in the settings trust list, checked at RUN time. A
session file is untrusted input (hand-editable, importable), so a path
that is not currently trusted on THIS install degrades to the node's
scratch workspace rather than silently operating on a folder this user
never granted. The grant is made by the person picking the folder in a
native dialog (the pick IS the consent - the gitlink local-root
precedent); nothing the model does can add a grant.

Every path a tool touches resolves through resolve_under_root - the one
confinement choke point - against whichever root the run bound.
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
    """The deterministic scratch directory for a workspace id - computable
    from the raw id alone, the same recompute-don't-hold-a-reference shape
    remove_scratch_dir_for_id relies on for node-delete GC."""
    if not workspace_id:
        # A blank id would resolve to the shared "default" bucket more than
        # one node could collide on - the exact hazard
        # remove_scratch_dir_for_id refuses to delete through.
        raise WorkspaceError("harness workspace id is blank")
    return HARNESS_WORKSPACE_ROOT / safe_scratch_id(workspace_id)


def ensure_workspace(workspace_id: str) -> Path:
    """Create-or-reuse the managed scratch dir, with the shared
    permissioning applied; called at run start (and by tests). Also
    refreshes the usage mtime - a run IS a use, whether or not any tool
    ends up writing."""
    path = workspace_dir(workspace_id)
    prepare_scratch_dir(path)
    touch_scratch_dir_usage(path)
    return path


def bound_root(
    workspace_id: str,
    workspace_path: str | None,
    *,
    settings_manager=None,
) -> tuple[Path, bool]:
    """Resolve a run's ONE bound root. Returns (root, is_user_dir).

    A non-empty workspace_path is honored only when it passes the trust
    gate: settings_manager.get_harness_trusted_dirs() must contain its
    resolved absolute string AND it must still be an existing directory.
    Any failure - untrusted, missing, gone, no settings manager - falls
    back silently to the managed scratch workspace. This is the load-
    bearing check: a session file naming a directory is a REQUEST, never a
    grant."""
    if workspace_path and settings_manager is not None:
        try:
            candidate = Path(workspace_path).resolve()
        except OSError:
            candidate = None
        if candidate is not None and candidate.is_dir():
            try:
                trusted = set(settings_manager.get_harness_trusted_dirs())
            except Exception:
                trusted = set()
            if str(candidate) in {str(Path(t).resolve()) for t in trusted if isinstance(t, str)}:
                return candidate, True
    return ensure_workspace(workspace_id), False


def resolve_under_root(root: Path, raw_path: str) -> Path:
    """The confinement choke point every fs/shell tool routes through:
    interprets `raw_path` (relative, or absolute-but-inside) against
    `root` and returns a fully resolved Path proven to live under it.

    resolve() (not a lexical prefix check) so both `..` traversal and a
    symlink planted inside the root pointing out of it land on the REAL
    target before the containment test - the same class of hostile-disk
    input the scratch-root ownership check already treats as in-scope. The
    root itself resolves first for the comparison, so a symlinked temp dir
    (macOS /tmp -> /private/tmp) never produces false refusals."""
    root = root.resolve()
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


def resolve_in_workspace(workspace_id: str, raw_path: str) -> Path:
    """Scratch-workspace confinement by id - the H1 signature, kept for
    callers and tests that bind a scratch workspace directly. Delegates to
    resolve_under_root against the id's scratch dir."""
    return resolve_under_root(workspace_dir(workspace_id), raw_path)
