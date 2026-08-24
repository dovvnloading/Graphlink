"""ADR-005 stage 5.3: shared location, permissioning, and garbage collection
for the two per-node scratch directories LLM-generated/executed code can
read and write - Py-Coder's REPL cwd (graphlink_plugins/pycoder/domain.py)
and Execution Sandbox's venv base_dir (graphlink_plugins/code_sandbox/
domain.py).

Location stays under tempfile.gettempdir(), unchanged from stage 5.1 - an
earlier draft of this stage's ADR entry proposed moving both under
~/.graphlink (the app's own persistent data dir) for consistency with
session.dat/chats.db, but the actual security gap this stage closes
(0700 below) does not depend on location: a directory's own POSIX mode
governs who can list/read/enter it regardless of the parent's permissions,
so a mode-1777 shared /tmp does not expose a 0700 child. Moving location
would also mix ephemeral execution scratch space (already reused across
runs by design - see prepare_scratch_dir's own docstring) into the same
directory tree as the app's small, deliberately-backed-up config/secrets
files, for no security benefit. Kept simple instead.

GC is needed because nothing else ever removes these directories on its
own: Py-Coder's REPL persists across disconnects by design (see
PythonREPL's own docstring) and is torn down only by explicit node
deletion; Execution Sandbox's VirtualEnvSandbox is reconstructed fresh per
run and never even holds a reference to a previous run's directory. Three
triggers, matching this stage's exit criterion:
  - node delete (backend/api/intents_nodes.py's remove_nodes) - exact,
    synchronous removal of the one deleted node's own directory.
  - session evict (backend/app.py's _evict_idle_session) - process-only,
    via AgentDispatcher.dispose_all_pycoder_repls(); deliberately does NOT
    call remove_scratch_dir here, see that method's own docstring for why.
  - age sweep on launch (graphlink_desktop.py's main()) - a crash/
    abandoned-session cleanup net for whatever the first two triggers
    never got to (e.g. a hard process kill, no clean node-delete ever
    happened), not the primary mechanism.
"""

import logging
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PYCODER_REPL_ROOT = Path(tempfile.gettempdir()) / "graphlink_pycoder_repls"
EXECUTION_SANDBOX_ROOT = Path(tempfile.gettempdir()) / "graphlink_execution_sandboxes"
# Agent-harness workspaces (backend/harness/workspace.py): one directory per
# harness node, keyed by HarnessState.harness_workspace_id - the same
# durable-id-not-node-id posture PYCODER_REPL_ROOT's children take. The
# node's transcript.jsonl lives INSIDE its workspace so all three GC
# triggers (delete/evict/age-sweep) cover conversation history and working
# files as one unit.
HARNESS_WORKSPACE_ROOT = Path(tempfile.gettempdir()) / "graphlink_harness_workspaces"

# A generous default: this is a crash/abandoned-session net, not the
# primary GC path (delete/evict are) - it should never race a normal,
# still-in-use scratch dir just because a user stepped away for a day.
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def safe_scratch_id(raw_id: str) -> str:
    """The exact sanitization PythonREPL/VirtualEnvSandbox already applied
    to node_id/sandbox_id inline before this module existed - factored out
    here so any caller that only has the raw id (not a live REPL/Sandbox
    object to read .cwd/.base_dir from) can still compute the identical
    directory name, e.g. backend/api/intents_nodes.py's remove_nodes for a
    deleted code_sandbox node's sandbox_id."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw_id or "default")


def _ensure_private_scratch_root(root: Path) -> None:
    """SECURITY-FIX (PYC-5): prepare_scratch_dir's own chmod 0700 on the
    LEAF only keeps other users out of it going forward - it does nothing
    to stop a user who already owns/controls the SHARED root (one of
    PYCODER_REPL_ROOT/EXECUTION_SANDBOX_ROOT, both predictable, fixed paths
    under the system temp dir) from renaming or symlink-swapping the
    leaf's own directory entry out from under this process: owning a
    directory grants rename/unlink rights over every entry inside it
    regardless of that entry's own mode, so a pre-existing, attacker-owned
    root turns the window between this call and the caller's later
    Popen(cwd=leaf) into a real TOCTOU race. Secures the ROOT itself first:
    creates it 0700 if new, and - the actual fix - refuses (raises
    OSError) to use a PRE-EXISTING root owned by a different user, rather
    than silently trusting it. This is the same "don't touch what you
    don't own" posture CPython's own tempfile.mkdtemp() takes for exactly
    this class of shared-/tmp attack. Callers (PythonREPL.start,
    VirtualEnvSandbox.ensure_base_environment) already run inside a
    run-level try/except that reports setup failures gracefully - a raise
    here becomes a failed run, not a crash.

    os.getuid() does not exist on Windows at all; this is only ever
    reached from prepare_scratch_dir's own POSIX-only branch, but a
    missing getuid is tolerated the same way as an OSError below (log,
    skip the ownership check, still attempt the chmod) rather than
    crashing outright - real POSIX systems always have it."""
    root.mkdir(parents=True, exist_ok=True)
    try:
        this_uid = os.getuid()
    except AttributeError:
        this_uid = None
    if this_uid is not None:
        try:
            owner_uid = root.stat().st_uid
        except OSError:
            owner_uid = None
        if owner_uid is not None and owner_uid != this_uid:
            raise OSError(
                f"refusing to use scratch root {root}: owned by uid {owner_uid}, "
                f"expected this process's own uid {this_uid} - possible shared-tmp pre-creation attack"
            )
    try:
        os.chmod(root, 0o700)
    except OSError:
        logger.warning("could not chmod scratch root %s to 0700 - continuing with existing permissions", root)


def prepare_scratch_dir(path: Path) -> None:
    """mkdir -p, then chmod 0700 on POSIX (no-op on Windows, matching every
    other chmod call site in this codebase - os.chmod there only ever
    toggles the read-only attribute, not real ACLs - see
    graphlink_settings_store.py's own chmod calls). A refused chmod is
    logged and otherwise ignored: a permissions tightening that can't be
    applied must not stop code execution from running, the same fail-open
    stance graphlink_settings_store.py's 0600 file chmods already take.

    SECURITY-FIX (PYC-5): on POSIX, also secures path.parent (the shared
    scratch root) via _ensure_private_scratch_root BEFORE creating
    anything inside it - see that function's own docstring for why a
    leaf's own chmod alone is not enough. This DOES raise (not
    log-and-swallow) on a detected hostile pre-existing root, unlike the
    leaf's own best-effort chmod below - see _ensure_private_scratch_root's
    docstring for why that asymmetry is deliberate."""
    if sys.platform != "win32":
        _ensure_private_scratch_root(path.parent)
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        return
    try:
        os.chmod(path, 0o700)
    except OSError:
        logger.warning("could not chmod %s to 0700 - continuing with existing permissions", path)


def remove_scratch_dir(path: Path) -> None:
    """Best-effort recursive delete, with one short retry: a directory
    whose owning subprocess is still exiting (e.g. code_sandbox's
    cooperative, ~100ms-polled cancel, which does not block the caller -
    see AgentDispatcher.remove_code_sandbox_scratch_dir's own docstring)
    can briefly hold an open file handle even after this is called - one
    retry after a short delay meaningfully improves the odds with no
    architectural change (no live object reference needed, no wait/poll
    loop threaded through the caller). A missing directory is silently
    fine (nothing to do, no retry needed); any other failure that
    survives the retry too (e.g. a genuine permissions error) is logged
    and swallowed rather than raised - GC must never be the reason a node
    delete/session evict/launch sweep itself fails. Leftover directories
    a failed removal leaves behind are still caught later by the age
    sweep."""
    for attempt in (1, 2):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == 2:
                logger.warning("could not remove scratch dir %s - leaving it in place", path)
                return
            time.sleep(0.25)


def remove_scratch_dir_for_id(root: Path, raw_id: str) -> None:
    """Node-delete GC entry point: recomputes the deterministic scratch
    path from a raw (unsanitized) id and removes it - the same pattern
    PythonREPL.cwd/VirtualEnvSandbox.base_dir already use to build their
    own path, so this works whether or not a live REPL/Sandbox object is
    currently around to ask (closes a real bug: a REPL already popped
    from AgentDispatcher._pycoder_repls - e.g. by a prior execute timeout
    - used to make a later real node-delete's removal silently no-op,
    since it only ever looked in that dict).

    Refuses to act on a blank/falsy raw_id: safe_scratch_id("") resolves
    to the literal "default" bucket, which is not this node's own
    directory - it is a shared fallback that more than one blank-id node
    could collide on (reachable via a malformed/hand-edited session
    payload; session_load.py's restore functions now self-heal a missing
    id on load specifically to avoid this in the normal case, but cannot
    guarantee every payload was produced by this app). Destructively
    rmtree-ing that shared bucket because ONE node with a blank id was
    deleted would take another still-live node's directory down with
    it."""
    if not raw_id:
        logger.warning("refusing to remove the shared scratch bucket for a blank id under %s", root)
        return
    remove_scratch_dir(root / safe_scratch_id(raw_id))


def touch_scratch_dir_usage(path: Path) -> None:
    """Bumps `path`'s own mtime to now, marking it as actively used for
    gc_stale_by_age's purposes. Needed because ordinary use of these
    directories - PythonREPL.execute() re-running the same subprocess,
    VirtualEnvSandbox writing to already-existing files - never adds,
    removes, or renames a direct child of the directory itself, the one
    thing that would otherwise bump its own mtime automatically: venv
    creation and script/requirements writes all open an EXISTING file for
    truncate+rewrite after the very first run, which does not touch the
    parent directory's own mtime on either POSIX or NTFS. Without this, a
    directory used daily for months looks exactly as stale to the age
    sweep as one abandoned the day after creation. Called at the start of
    every real use (PythonREPL.execute(), VirtualEnvSandbox._run_subprocess),
    not just creation. Best-effort: a failure (e.g. the directory was
    removed by a concurrent GC trigger between use and this call) is
    logged and swallowed, never allowed to interrupt the actual
    execution/subprocess run this is called alongside."""
    try:
        os.utime(path, None)
    except OSError:
        logger.warning("could not refresh mtime for %s - it may look falsely stale to the age sweep", path)


def gc_stale_by_age(root: Path, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> list:
    """Removes every immediate child of `root` whose mtime is older than
    max_age_seconds. Directory mtime updates whenever a direct child is
    added/removed/renamed - not on writes deep inside a nested
    subdirectory - so this is an approximation of "last touched", not an
    exact usage timestamp; acceptable for a crash-cleanup net, the same
    honest approximation the rest of this codebase already accepts for the
    session-idle TTL (also a monotonic-time heuristic, not real usage
    tracking). Safe against a root that does not exist yet (a fresh
    install/profile that has never created either scratch root). Returns
    the list of paths removed, for logging by the caller."""
    if not root.is_dir():
        return []
    cutoff = time.time() - max_age_seconds
    removed = []
    for child in root.iterdir():
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            remove_scratch_dir(child)
            removed.append(child)
    return removed


def sweep_stale_scratch_dirs_on_launch(max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS) -> None:
    """The one-time-per-launch age sweep - called from
    graphlink_desktop.py's main(), see this module's own docstring for why
    this trigger exists alongside delete/evict. Iterates both scratch
    roots; a failure sweeping one root is logged and does not stop the
    other from being swept, and neither failure is allowed to abort app
    startup (matching mark_running()/configure_logging()'s own
    best-effort-and-log stance in backend/crash_recovery.py)."""
    for root in (PYCODER_REPL_ROOT, EXECUTION_SANDBOX_ROOT, HARNESS_WORKSPACE_ROOT):
        try:
            removed = gc_stale_by_age(root, max_age_seconds)
        except OSError:
            logger.warning("scratch-dir age sweep failed for %s", root, exc_info=True)
            continue
        if removed:
            logger.info("age-swept %d stale scratch dir(s) under %s", len(removed), root)
