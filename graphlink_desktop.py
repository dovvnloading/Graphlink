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


def _start_backend(port: int) -> threading.Thread:
    import uvicorn

    from backend.app import create_app

    config = uvicorn.Config(
        create_app(),
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
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    spa_index = REPO_ROOT / "web_ui" / "dist" / "app" / "index.html"
    if not spa_index.is_file():
        logger.error(
            "SPA build missing at %s - run: cd web_ui && GRAPHLINK_ISLAND=app npx vite build",
            spa_index,
        )
        return 1

    port = int(os.environ.get("GRAPHLINK_BACKEND_PORT", 0)) or _free_port()
    base_url = f"http://127.0.0.1:{port}"

    _start_backend(port)
    if not _wait_for_health(base_url, STARTUP_TIMEOUT_SECONDS):
        logger.error("backend did not become healthy at %s within %.0fs", base_url, STARTUP_TIMEOUT_SECONDS)
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
    webview.start(debug=bool(os.environ.get("GRAPHLINK_DEBUG_WEBVIEW")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
