"""Crash visibility: native-fault capture, unhandled-exception reporting, and a
next-launch "did we crash last time" notice.

Before this, a windowed app with no console meant every unhandled exception and every
native Qt/llama.cpp fault was invisible - the user saw nothing, and the maintainer had no
way to know a session died or why (see doc/PRODUCTION_ROADMAP.md #5 "Crash reporting").

Three capture channels, installed by install_crash_handlers() as the first thing
graphlink_app.main() does (before QApplication exists):
  1. faulthandler - catches native crashes (Qt/llama.cpp segfaults) to a log file.
  2. sys.excepthook / threading.excepthook - catches unhandled Python exceptions on the
     main thread and on any bare threading.Thread, and writes a redacted JSON report.
  3. qInstallMessageHandler - routes Qt's own qCritical/qFatal messages into the same
     rotating log configure_logging() already sets up, instead of vanishing.

Redaction is structural, not textual: build_crash_report() only ever reads sys.exc_info()
and the explicit, pre-approved `context` dict a caller passes in (e.g. {"node_count": 3,
"provider_mode": "ollama"}) - it never reaches into scene/conversation state itself, so
chat content and prompts cannot appear in a report by construction. As defense in depth,
_scrub_home_paths() also collapses the user's home directory prefix (which embeds the
Windows username) to "~" wherever it appears in the traceback text.

No sentry-sdk or other phone-home telemetry: reports are local-only until the user
explicitly clicks "Open GitHub issue" (build_github_issue_url), which opens a prefilled
browser tab - the user sees exactly what would be submitted before anything leaves the
machine, and nothing is sent automatically.
"""

import faulthandler
import json
import os
import platform
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

GITHUB_ISSUE_URL = "https://github.com/dovvnloading/Graphlink/issues/new"

_installed = False
_faulthandler_file = None


def _crash_dir(base_dir=None):
    base = Path(base_dir) if base_dir is not None else Path.home() / ".graphlink"
    return base / "crash"


def _scrub_home_paths(text):
    """Collapse the user's home directory (which embeds the Windows username) to '~'."""
    home = str(Path.home())
    if not home:
        return text
    scrubbed = text.replace(home, "~")
    # Path.home() can differ from os.path.expanduser("~") in casing/separators on
    # Windows; catch that variant too so the username doesn't slip through unscrubbed.
    alt_home = os.path.expanduser("~")
    if alt_home and alt_home != home:
        scrubbed = scrubbed.replace(alt_home, "~")
    return scrubbed


def build_crash_report(exc_type, exc_value, exc_tb, *, version="unknown", thread_name=None, context=None):
    """Build a redacted, JSON-serializable crash report dict.

    Only reads sys.exc_info()-shaped arguments and the explicit `context` dict the caller
    supplies - it never touches app/scene state itself, so chat content and prompts cannot
    end up in a report regardless of what the caller has in memory elsewhere.
    """
    tb_text = _scrub_home_paths("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    message = _scrub_home_paths(str(exc_value))
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "app_version": version,
        "os": platform.platform(),
        "python_version": platform.python_version(),
        "thread": thread_name or threading.current_thread().name,
        "exception_type": exc_type.__name__ if exc_type else "UnknownError",
        "exception_message": message,
        "traceback": tb_text,
        "context": dict(context) if context else {},
    }


def format_crash_report_text(report):
    lines = [
        f"Graphlink crash report - {report.get('timestamp', '?')}",
        f"Version: {report.get('app_version', '?')}  OS: {report.get('os', '?')}  Python: {report.get('python_version', '?')}",
        f"Thread: {report.get('thread', '?')}",
        "",
        f"{report.get('exception_type', 'Error')}: {report.get('exception_message', '')}",
        "",
        report.get("traceback", ""),
    ]
    context = report.get("context") or {}
    if context:
        lines.append("Context: " + json.dumps(context, sort_keys=True))
    return "\n".join(lines)


def write_crash_report(report, crash_dir=None):
    """Write the report as crash-<timestamp>.json under the crash directory. Returns the path."""
    directory = _crash_dir(crash_dir)
    directory.mkdir(parents=True, exist_ok=True)
    safe_timestamp = report.get("timestamp", "").replace(":", "-")
    path = directory / f"crash-{safe_timestamp}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return path


def build_github_issue_url(report, repo_issue_url=GITHUB_ISSUE_URL):
    """A prefilled 'new issue' URL. Opening it is the only way anything leaves the
    machine - nothing here performs a network request itself."""
    title = f"Crash: {report.get('exception_type', 'Error')}: {report.get('exception_message', '')}"[:200]
    body = "```\n" + format_crash_report_text(report) + "\n```"
    return f"{repo_issue_url}?title={quote(title)}&body={quote(body)}"


def _handle_exception(exc_type, exc_value, exc_tb, *, version, thread_name=None):
    try:
        report = build_crash_report(exc_type, exc_value, exc_tb, version=version, thread_name=thread_name)
        path = write_crash_report(report)
        import logging
        logging.getLogger("graphlink.crash").error(
            "Unhandled exception on %s, report saved to %s\n%s",
            report["thread"], path, format_crash_report_text(report),
        )
    except Exception:
        # The crash handler itself must never be the thing that crashes the app further.
        pass


def _make_excepthook(version):
    def _excepthook(exc_type, exc_value, exc_tb):
        _handle_exception(exc_type, exc_value, exc_tb, version=version)
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    return _excepthook


def _make_threading_excepthook(version):
    def _threading_excepthook(args):
        _handle_exception(
            args.exc_type, args.exc_value, args.exc_traceback,
            version=version, thread_name=getattr(args.thread, "name", None),
        )
    return _threading_excepthook


def _make_qt_message_handler():
    def _qt_message_handler(msg_type, context, message):
        import logging
        from PySide6.QtCore import QtMsgType
        logger = logging.getLogger("graphlink.qt")
        scrubbed = _scrub_home_paths(message)
        if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            logger.error("Qt %s: %s", msg_type.name, scrubbed)
        elif msg_type == QtMsgType.QtWarningMsg:
            logger.warning("Qt %s: %s", msg_type.name, scrubbed)
        else:
            logger.debug("Qt %s: %s", msg_type.name, scrubbed)
    return _qt_message_handler


def install_crash_handlers(version="unknown", crash_dir=None):
    """Install all three capture channels. Idempotent - safe to call more than once."""
    global _installed, _faulthandler_file
    if _installed:
        return
    _installed = True

    directory = _crash_dir(crash_dir)
    directory.mkdir(parents=True, exist_ok=True)
    _faulthandler_file = open(directory / "faulthandler.log", "a", encoding="utf-8")
    faulthandler.enable(file=_faulthandler_file)

    sys.excepthook = _make_excepthook(version)
    threading.excepthook = _make_threading_excepthook(version)

    try:
        from PySide6.QtCore import qInstallMessageHandler
        qInstallMessageHandler(_make_qt_message_handler())
    except ImportError:
        pass


# --- "did the previous run crash" sentinel ---
#
# A JSON file at ~/.graphlink/running.lock is written at startup (mark_running) and
# removed on a clean shutdown (mark_clean_exit). If it's still there at the NEXT startup,
# the previous run didn't exit cleanly. Kept deliberately minimal here (pid/version/start
# time only) - a later crash-recovery workstream can extend this same file with the active
# chat id without changing this format.

def _sentinel_path(base_dir=None):
    base = Path(base_dir) if base_dir is not None else Path.home() / ".graphlink"
    return base / "running.lock"


def mark_running(version="unknown", sentinel_dir=None):
    path = _sentinel_path(sentinel_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": os.getpid(), "version": version, "started_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )


def mark_clean_exit(sentinel_dir=None):
    path = _sentinel_path(sentinel_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def previous_run_crashed(sentinel_dir=None):
    return _sentinel_path(sentinel_dir).exists()
