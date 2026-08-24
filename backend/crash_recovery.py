"""Crash recovery (Qt-removal plan R6.7): a next-launch "did we crash last
time" notice, plus backend logging so unhandled exceptions land somewhere
durable instead of vanishing in a windowed app with no console.

REIMPLEMENTED, not imported, from graphlink_app/graphlink_crash.py and
graphlink_app/graphlink_logging.py - same "port the algorithm, never import
the Qt-adjacent legacy module" precedent every other backend/ module in this
migration follows (backend/chat_library.py, session_save.py, autosave.py,
...), since graphlink_app/ is slated for full deletion at the R7 cutover.

SENTINEL (ported faithfully): a JSON file at ~/.graphlink/running.lock is
written at startup (mark_running) and removed on a clean shutdown
(mark_clean_exit). If it's still there at the NEXT startup, the previous run
didn't reach the clean-exit path - previous_run_crashed() is the whole check.
Same file path/shape as the legacy app for continuity - it's plain JSON with
no Qt dependency, nothing here needs to change about it.

LOGGING + UNHANDLED-EXCEPTION CAPTURE (ported): configure_logging() attaches
a RotatingFileHandler to the root logger, same path/rotation as
graphlink_logging.py's configure_logging() (~/.graphlink/graphlink.log, 2MB
x3 backups). ADR-016 stage 16.1 changed the FILE handler's format from plain
text to JSON-lines (backend/observability.py's JsonLogFormatter) so the log
is machine-parseable; the stderr handler below stays plain text. So the
many logger.exception()/logger.error() calls already
scattered across backend/ (app.py, autosave.py, agents.py, canvas.py, ...)
land somewhere durable instead of going nowhere (no root handler is
configured anywhere in backend/ today) or to stderr, invisible in a
windowed app with no console. install_exception_handlers() installs
sys.excepthook/threading.excepthook (catches unhandled Python exceptions
escaping the main thread or a bare threading.Thread - exactly the failure
mode the R6.7 recon found has zero detection today, e.g. a crash in
graphlink_desktop.py's daemon backend thread after successful startup) plus
faulthandler (native/segfault crashes) into the same log, mirroring the
legacy module's channels 1 and 2 exactly.

Deliberately NOT ported: legacy's channel 3 (qInstallMessageHandler, routing
Qt's own qCritical/qFatal into the log) - there is no Qt in this stack to
route messages from. Also NOT ported: the separate per-crash structured JSON
report + prefilled "Open GitHub issue" URL (build_crash_report/
write_crash_report/build_github_issue_url in the legacy module). That's a
genuinely separate diagnostics-artifact-plus-user-action feature beyond this
increment's scoped "backend logging + in-app notice" - a crash's full
exception and traceback still lands in graphlink.log via the excepthook
above, so nothing is silently lost, it's just not additionally packaged into
its own JSON file with a prefilled issue URL.

BOTH configure_logging() and install_exception_handlers() mutate PROCESS-WIDE
global state (the root logger's handlers, sys.excepthook, threading.excepthook,
faulthandler) and are idempotent by design (a module-level flag makes the
second and later calls no-ops) - matching graphlink_logging.py's own
_configured guard exactly. Because of that, neither is called from
backend/app.py's create_app() (which real tests construct many times over
the life of a pytest run - repeatedly attaching handlers, or attaching one
pointed at a tmp_path that a later test's teardown deletes out from under a
"once installed, never reinstalled" handler, would leak across unrelated
tests). Both are called exactly once, for the whole real process, from
graphlink_desktop.py's own main() - the actual entry point, never invoked by
the test suite - the same relationship graphlink_app.py's main() had with
the legacy configure_logging()/install_crash_handlers() calls.
"""

from __future__ import annotations

import faulthandler
import json
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from backend.notifications import NotificationState
from backend.observability import JsonLogFormatter

logger = logging.getLogger(__name__)

_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUP_COUNT = 3
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_logging_configured = False
_handlers_installed = False
_faulthandler_file = None

CRASH_NOTICE_MESSAGE = (
    "Graphlink didn't shut down cleanly last time. Your work should be safe - "
    "autosave protects your session automatically. See graphlink.log for "
    "details if anything looks wrong."
)


def _data_dir(base_dir: Path | str | None = None) -> Path:
    return Path(base_dir) if base_dir is not None else Path.home() / ".graphlink"


def log_path(base_dir: Path | str | None = None) -> Path:
    return _data_dir(base_dir) / "graphlink.log"


def sentinel_path(base_dir: Path | str | None = None) -> Path:
    return _data_dir(base_dir) / "running.lock"


def crash_dir(base_dir: Path | str | None = None) -> Path:
    return _data_dir(base_dir) / "crash"


def _chmod_0600(path: Path) -> None:
    """SECURITY-FIX (OBS-3): best-effort POSIX 0600 tightening for a file
    that can carry chat content or absolute paths, mirroring the exact
    chmod idiom already used everywhere else in this codebase for files
    like this (graphlink_settings_store.py's session.dat,
    backend/chat_library.py's chats.db and its WAL sidecars,
    graphlink_scratch_dirs.py's prepare_scratch_dir) - graphlink.log,
    faulthandler.log, and the diagnostic bundle JSON were the one remaining
    exception. No-op on Windows (os.chmod there only ever toggles the
    read-only attribute, never real ACLs - the user's own profile ACLs are
    the actual access control there). A refused chmod is logged and
    swallowed, never raised: a permissions tightening that can't be applied
    must not crash logging/diagnostics themselves. Reused directly by
    backend/api/intents_diagnostics.py for the bundle file, the same way
    that module already reaches into this one's _data_dir()."""
    if sys.platform == "win32":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        logger.warning("could not chmod %s to 0600 - continuing with existing permissions", path)


class _Chmod0600RotatingFileHandler(logging.handlers.RotatingFileHandler):
    """SECURITY-FIX (OBS-3): plain RotatingFileHandler opens every file it
    ever touches (the initial graphlink.log AND each rotated-open that
    doRollover triggers) via the stdlib's unguarded open(), subject to the
    process umask each time - chmod'ing the handler once at construction
    only covers the first file. doRollover itself renames the current log
    down the chain (graphlink.log -> .1 -> .2 -> .3, oldest discarded) and
    then reopens a BRAND NEW file at the original path; the renamed-away
    .1/.2/.3 copies need no extra treatment since POSIX rename preserves
    the source inode's mode bits (same reasoning backend/chat_library.py's
    corrupt-db quarantine rename already relies on) - so as long as the
    active file was 0600 before rotation, every file it rotates into stays
    0600 automatically. Only the freshly reopened current file needs a new
    chmod after each rollover, which is all this override adds."""

    def doRollover(self) -> None:
        super().doRollover()
        _chmod_0600(Path(self.baseFilename))


def configure_logging(base_dir: Path | str | None = None, level: int = logging.INFO) -> None:
    """Attach a rotating file handler to the root logger. Idempotent - later
    calls are no-ops so handlers are never duplicated across a process's
    lifetime (see this module's own docstring for why that also means this
    must never be called from create_app())."""
    global _logging_configured
    if _logging_configured:
        return

    resolved = log_path(base_dir)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT)

    handler = _Chmod0600RotatingFileHandler(
        resolved, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT, encoding="utf-8",
    )
    # SECURITY-FIX (OBS-3): this file can hold chat content and absolute
    # paths (per this module's own docstring and the many
    # logger.exception()/logger.error() call sites this handler collects
    # from across backend/), so it needs the same 0600 tightening every
    # OTHER file in ~/.graphlink that can carry user content already gets.
    # Runs on every real call, not just when the file is first created -
    # this function is itself idempotent per-PROCESS only, so a fresh
    # process re-attaching to an already-existing graphlink.log (the normal
    # case on every launch after the first) still self-heals its
    # permissions here, same "self-heal on every launch" posture
    # graphlink_settings_store.py's SettingsManager.__init__ already takes
    # for session.dat. Rotation-created files are covered separately by
    # _Chmod0600RotatingFileHandler.doRollover above.
    _chmod_0600(resolved)
    # ADR-016 stage 16.1: the file is JSON-lines (one JSON object per line,
    # with run_id/session/kind/node_id when a call site supplies them via
    # extra=) so it is machine-parseable - by the diagnostics bundle builder
    # (stage 16.4) and by grep/jq alike. See observability.py's own
    # docstring for why stderr below stays plain text.
    handler.setFormatter(JsonLogFormatter())

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    # Audit fix: this function replaced graphlink_desktop.py's own
    # logging.basicConfig() call, which had given the root logger a stderr
    # StreamHandler. Attaching only the file handler silently took the console
    # away - and because logging.lastResort only fires when root has NO
    # handler, `python graphlink_desktop.py` with a missing SPA build printed
    # absolutely nothing and exited 1, despite that path logging a perfectly
    # good error. Running from a terminal is the documented launch story for
    # this entry point, so the console stays. The windowed-app argument was
    # always a reason to ADD the file, never to remove stderr.
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    root_logger.setLevel(level)

    _logging_configured = True


def install_exception_handlers(base_dir: Path | str | None = None) -> None:
    """Installs faulthandler (native crashes) and sys.excepthook/
    threading.excepthook (unhandled Python exceptions on the main thread or
    any bare threading.Thread) so both land in the same log configure_logging
    sets up, instead of vanishing. Idempotent, same reasoning as
    configure_logging."""
    global _handlers_installed, _faulthandler_file
    if _handlers_installed:
        return

    directory = crash_dir(base_dir)
    directory.mkdir(parents=True, exist_ok=True)
    faulthandler_path = directory / "faulthandler.log"
    _faulthandler_file = open(faulthandler_path, "a", encoding="utf-8")
    # SECURITY-FIX (OBS-3): native/segfault tracebacks can embed the same
    # absolute paths and in-flight state as graphlink.log - same 0600
    # tightening as that file, applied right after open() creates it (or
    # confirms it already exists) so there's no window at the default umask.
    _chmod_0600(faulthandler_path)
    faulthandler.enable(file=_faulthandler_file)

    crash_logger = logging.getLogger("graphlink.crash")

    def _excepthook(exc_type, exc_value, exc_tb):
        crash_logger.error(
            "Unhandled exception on %s", threading.current_thread().name,
            exc_info=(exc_type, exc_value, exc_tb),
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _threading_excepthook(args):
        crash_logger.error(
            "Unhandled exception on %s", getattr(args.thread, "name", None) or "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook

    # Set LAST, matching configure_logging above. Audit finding: this used to
    # be set first, so if mkdir or open() raised (an unwritable ~/.graphlink/
    # crash, a full disk) the flag was already latched and every later retry
    # became a silent no-op - leaving the hooks this function exists to
    # install permanently absent, with no second chance.
    _handlers_installed = True


def mark_running(base_dir: Path | str | None = None) -> None:
    path = sentinel_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )


def mark_clean_exit(base_dir: Path | str | None = None) -> None:
    """Removes this process's own sentinel. Audit fix: this used to unlink
    unconditionally, which made two concurrent instances corrupt each other -
    B starts while A is running and overwrites the lock with B's pid, then A
    exits cleanly and deletes it, so a later crash of B left no evidence and
    went unreported on the next launch. Only the pid that wrote the sentinel
    may remove it.

    KNOWN, UNFIXED: the other half of that scenario - B seeing A's live lock
    at startup and reporting a crash that never happened - needs a real
    is-that-pid-alive check, which has no safe portable form here (os.kill's
    signal-0 liveness probe is POSIX-only; on Windows os.kill calls
    TerminateProcess and would kill the other instance). Left alone
    deliberately rather than fixed with something dangerous."""
    path = sentinel_path(base_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        owner = payload.get("pid")
    except (FileNotFoundError, ValueError, OSError):
        # Unreadable or malformed: fall back to the old unconditional
        # behavior rather than leaving a stale lock that would report a
        # phantom crash forever.
        owner = os.getpid()

    if owner != os.getpid():
        return

    try:
        path.unlink()
    except FileNotFoundError:
        pass


def previous_run_crashed(base_dir: Path | str | None = None) -> bool:
    return sentinel_path(base_dir).exists()


def maybe_show_crash_notice(notifications: NotificationState, crashed: bool) -> None:
    """Called once per session configuration (backend/app.py's
    _configure_session), before any WS connection has subscribed - a no-op
    unless the sentinel above found evidence the prior run didn't reach a
    clean shutdown. No publish() call needed: a subscribe's send_snapshot
    reads current state fresh, and no connection can exist yet at the point
    _configure_session runs."""
    if crashed:
        notifications.show(CRASH_NOTICE_MESSAGE, "warning")
