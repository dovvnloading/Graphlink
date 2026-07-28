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

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("graphlink.desktop")

STARTUP_TIMEOUT_SECONDS = 15.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_for_health(base_url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1.0) as response:
                if response.status == 200:
                    return True
        except OSError:
            time.sleep(0.1)
    return False


def _start_backend(port: int, previous_run_crashed: bool = False) -> threading.Thread:
    import uvicorn

    from backend.app import create_app

    config = uvicorn.Config(
        create_app(previous_run_crashed=previous_run_crashed),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        # The desktop process owns lifetime: closing the window exits the
        # process, taking this daemon thread (and the server) with it.
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="graphlink-backend", daemon=True)
    thread.start()
    return thread


def main() -> int:
    # R6.7 (Qt-removal plan): real rotating-file logging plus unhandled-
    # exception/native-crash capture, replacing the old bare stderr-only
    # basicConfig call - see backend/crash_recovery.py's own docstring for
    # why these two calls (and the sentinel check/mark_running below) live
    # here, in the real entry point, rather than inside create_app().
    from backend.crash_recovery import (
        configure_logging,
        install_exception_handlers,
        mark_clean_exit,
        mark_running,
        previous_run_crashed,
    )

    configure_logging()
    install_exception_handlers()
    crashed = previous_run_crashed()
    mark_running()

    spa_index = REPO_ROOT / "web_ui" / "dist" / "app" / "index.html"
    if not spa_index.is_file():
        logger.error(
            "SPA build missing at %s - run: cd web_ui && npm run build",
            spa_index,
        )
        mark_clean_exit()
        return 1

    port = int(os.environ.get("GRAPHLINK_BACKEND_PORT", 0)) or _free_port()
    base_url = f"http://127.0.0.1:{port}"

    _start_backend(port, previous_run_crashed=crashed)
    if not _wait_for_health(base_url, STARTUP_TIMEOUT_SECONDS):
        logger.error("backend did not become healthy at %s within %.0fs", base_url, STARTUP_TIMEOUT_SECONDS)
        mark_clean_exit()
        return 1
    logger.info("backend healthy at %s", base_url)

    import webview  # pywebview - the native (non-Qt, non-browser) window

    webview.create_window(
        "Graphlink",
        url=base_url,
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
    webview.start(**start_kwargs)
    # Reached only on a normal window close - webview.start() blocks until
    # then. An uncaught exception anywhere above (or a hard kill/power loss)
    # never reaches this line, leaving running.lock in place - exactly the
    # signal previous_run_crashed() checks for on the NEXT launch.
    mark_clean_exit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
