"""Graphlink desktop entry point (Qt-removal plan R0, doc/QT_REMOVAL_PLAN.md).

Launches the app as a NATIVE DESKTOP WINDOW with zero Qt:

  1. starts the Python backend (FastAPI/uvicorn) on a free localhost port,
     in a daemon thread inside this same process;
  2. waits for /api/health to answer;
  3. opens a pywebview window - the OS's own embedded webview component
     (WebView2 on Windows), NOT the user's browser: no tabs, no address bar,
     just an application window rendering the built SPA.

`python graphlink_desktop.py` is the whole launch story, same as the Qt
entry point it replaces. When the R7 cutover deletes the Qt app, this file
becomes graphlink_app.py.

Environment:
  GRAPHLINK_BACKEND_PORT  pin the backend port (default: OS-assigned free port)
  GRAPHLINK_DEBUG_WEBVIEW set to 1 to enable the webview's devtools

ADR-004 stage 4.1: this entry point also mints the per-launch capability
token that gates /api/* and /ws (see backend/auth.py). It is passed to
create_app() and handed to the window as a URL fragment; nothing is
persisted. A developer running the SPA from a vite dev server instead of
this shell sets GRAPHLINK_DEV_AUTH_TOKEN (and GRAPHLINK_DEV_WS_ORIGIN) -
neither is ever set by this file.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # ADR-015 stage 15.3: _start_backend/_shutdown_backend's own annotations
    # reference uvicorn, but both import it LOCALLY (deferred, function-scope)
    # to keep this module importable without uvicorn eagerly loaded - this
    # TYPE_CHECKING-only import satisfies static analysis (ruff's F821,
    # mypy) without changing that runtime behavior at all.
    import uvicorn

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("graphlink.desktop")

STARTUP_TIMEOUT_SECONDS = 15.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_for_health(
    base_url: str,
    timeout: float,
    thread: threading.Thread | None = None,
    auth_token: str | None = None,
) -> bool:
    """thread, when supplied, is checked for liveness on every poll
    iteration BEFORE attempting a request - a dead backend thread (e.g.
    create_app()/server.run() raised immediately) is a distinct, terminal
    failure, not "still connecting": without this check, a thread that died
    in the first millisecond still made the caller wait out the full
    timeout before reporting a misleading "did not become healthy".

    auth_token (ADR-004 stage 4.1) is required because /api/health is gated
    like every other /api route - this poll is the one legitimate caller,
    and it has the token because main() minted it just above."""
    deadline = time.monotonic() + timeout
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
    while time.monotonic() < deadline:
        if thread is not None and not thread.is_alive():
            return False
        try:
            request = urllib.request.Request(f"{base_url}/api/health", headers=headers)
            with urllib.request.urlopen(request, timeout=1.0) as response:
                if response.status == 200:
                    return True
        except OSError:
            # HTTPError (a 401 from a token mismatch) is a subclass of
            # OSError, so a broken token shows up here as "never became
            # healthy" rather than as a crash - the same terminal outcome,
            # logged by the caller.
            time.sleep(0.1)
    return False


def _start_backend(
    port: int, previous_run_crashed: bool = False, auth_token: str | None = None
) -> tuple[uvicorn.Server, threading.Thread]:
    import uvicorn

    from backend.app import create_app

    config = uvicorn.Config(
        create_app(previous_run_crashed=previous_run_crashed, auth_token=auth_token),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        # The desktop process owns lifetime: closing the window exits the
        # process, taking this daemon thread (and the server) with it -
        # UNLESS the caller shuts it down cooperatively first, see
        # _shutdown_backend below.
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="graphlink-backend", daemon=True)
    thread.start()
    return server, thread


def _shutdown_backend(server: uvicorn.Server, thread: threading.Thread) -> None:
    """Cooperative shutdown: server.should_exit is uvicorn's documented
    signal, checked in its own serve loop, so an in-flight request gets a
    chance to finish rather than being killed outright when the daemon
    thread is simply abandoned at process exit. join(timeout=...) bounds
    how long the caller waits, so a hung request can't hang app exit
    forever. Safe to call even if the thread already died on its own -
    should_exit becomes a no-op and join() returns immediately."""
    server.should_exit = True
    thread.join(timeout=5.0)


def main() -> int:
    # R6.7 (Qt-removal plan): real rotating-file logging plus unhandled-
    # exception/native-crash capture, replacing the old bare stderr-only
    # basicConfig call - see backend/crash_recovery.py's own docstring for
    # why these two calls (and the sentinel check/mark_running below) live
    # here, in the real entry point, rather than inside create_app().
    from backend.auth import mint_token
    from backend.crash_recovery import (
        configure_logging,
        install_exception_handlers,
        mark_clean_exit,
        mark_running,
        previous_run_crashed,
    )
    from graphlink_scratch_dirs import sweep_stale_scratch_dirs_on_launch
    from graphlink_settings_store import SettingsManager

    # ADR-016 stage 16.1: read the persisted log level before attaching
    # handlers, so the very first log line already honors it. A throwaway
    # SettingsManager here (create_app() below constructs its own, separate
    # instance moments later, reading the same file) is safe: _load_state/
    # _save_state and the secrets migration it runs are idempotent, and this
    # function is the real entry point, never invoked by the test suite (see
    # backend/crash_recovery.py's own docstring on that same guarantee).
    configured_level = getattr(logging, SettingsManager().get_log_level(), logging.INFO)
    configure_logging(level=configured_level)
    install_exception_handlers()
    crashed = previous_run_crashed()
    mark_running()
    # ADR-005 stage 5.3: the age-sweep GC trigger - a crash/abandoned-
    # session cleanup net for scratch dirs that node-delete/session-evict
    # never got to (e.g. a hard kill). Best-effort and non-fatal, same
    # posture as every other call in this boot sequence - see that
    # function's own docstring in graphlink_scratch_dirs.py.
    try:
        sweep_stale_scratch_dirs_on_launch()
    except Exception:
        logger.exception("scratch-dir age sweep failed at launch")

    spa_index = REPO_ROOT / "web_ui" / "dist" / "app" / "index.html"
    if not spa_index.is_file():
        logger.error(
            "SPA build missing at %s - run: cd web_ui && npm run build",
            spa_index,
        )
        mark_clean_exit()
        return 1

    raw_port = os.environ.get("GRAPHLINK_BACKEND_PORT")
    try:
        port = int(raw_port) if raw_port else _free_port()
    except ValueError:
        # Guarded so a bad env value can't raise here, AFTER mark_running()
        # has already written the crash sentinel above - an uncaught raise
        # at this point used to skip mark_clean_exit() entirely, leaving a
        # false "previous run crashed" notice on the NEXT launch.
        logger.warning("GRAPHLINK_BACKEND_PORT=%r is not a valid port, ignoring", raw_port)
        port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    # ADR-004 stage 4.1: one fresh capability token per launch, never
    # persisted - see backend/auth.py's docstring for the threat it closes
    # (audit C5: any local process can drive all 131 intents, including the
    # approve-code-execution gate itself).
    auth_token = mint_token()

    server, backend_thread = _start_backend(
        port, previous_run_crashed=crashed, auth_token=auth_token
    )
    if not _wait_for_health(
        base_url, STARTUP_TIMEOUT_SECONDS, thread=backend_thread, auth_token=auth_token
    ):
        logger.error("backend did not become healthy at %s within %.0fs", base_url, STARTUP_TIMEOUT_SECONDS)
        _shutdown_backend(server, backend_thread)
        mark_clean_exit()
        return 1
    logger.info("backend healthy at %s", base_url)

    import webview  # pywebview - the native (non-Qt, non-browser) window

    # The token reaches the SPA as a URL FRAGMENT, which the browser never
    # sends to the server - so it cannot land in an access log or a Referer
    # header the way a query string could, and this window has no address bar
    # to display it. web_ui/src/lib/auth/token.ts reads it once at module
    # load. Never logged here either: `base_url` (without the fragment) is
    # what goes to the log line above, deliberately.
    window_url = f"{base_url}/#token={auth_token}"

    webview.create_window(
        "Graphlink",
        url=window_url,
        width=1440,
        height=900,
        min_size=(960, 600),
        background_color="#1a1a1a",
    )
    # R8a: the real app icon. Without an explicit `icon=`, pywebview's
    # Windows backend falls back to extracting icon index 0 from
    # sys.executable (webview/platforms/winforms.py) - i.e. python.exe's own
    # icon, which is why the title bar showed the Python logo. Passed as a
    # str because the backend does its own os.path.isfile() check on it.
    #
    # Rebuild assets/graphlink.ico with: python tools/build_app_icon.py
    icon_path = REPO_ROOT / "assets" / "graphlink.ico"
    start_kwargs = {"debug": bool(os.environ.get("GRAPHLINK_DEBUG_WEBVIEW"))}
    if icon_path.is_file():
        start_kwargs["icon"] = str(icon_path)
    else:
        # Not fatal - the window still opens, just with the interpreter's
        # icon. Logged rather than raised so a missing asset can never be
        # the reason the app won't launch.
        logger.warning("app icon missing at %s - falling back to the default", icon_path)
    # webview.start() blocks until the window closes (normal exit) or raises
    # (e.g. the WebView2 runtime is missing). Both paths must still shut the
    # backend down and clear the crash sentinel - previously an exception
    # here skipped mark_clean_exit() entirely, leaving a false "previous run
    # crashed" notice on the NEXT launch. A hard kill/power loss still
    # bypasses all of this, which is exactly the case running.lock exists
    # to detect.
    try:
        webview.start(**start_kwargs)
    except Exception:
        logger.exception("webview.start() failed to launch the native window")
        return 1
    finally:
        _shutdown_backend(server, backend_thread)
        mark_clean_exit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
