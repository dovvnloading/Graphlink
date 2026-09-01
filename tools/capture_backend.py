"""Boots the real backend for tools/capture_screenshots.mjs.

A sibling of tests_e2e/run_backend.py with two differences, both so the
screenshots are reproducible rather than dependent on whoever is running
them:

  * the port and data directory are arguments, so the capture script owns
    the temp dir it seeded a chats.db into (tools/seed_demo_graph.py) and
    can hand the same one here;
  * nothing else. This is create_app() exactly as shipped, serving the real
    built SPA out of web_ui/dist/app - a screenshot of a mocked app would be
    worth nothing.

auth_token=None disables the /api and /ws capability token, the same
"expected only in tests" posture run_backend.py documents: there is no
desktop shell here to mint one, and this binds to loopback only.

    python tools/capture_backend.py <port> <data-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python tools/capture_backend.py <port> <data-dir>")
    port = int(sys.argv[1])
    data_dir = Path(sys.argv[2])
    data_dir.mkdir(parents=True, exist_ok=True)

    import uvicorn

    from backend.app import create_app

    app = create_app(
        spa_dir=REPO_ROOT / "web_ui" / "dist" / "app",
        settings_state_file=data_dir / "session.dat",
        chat_db_path=data_dir / "chats.db",
        auth_token=None,
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
