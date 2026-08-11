"""Boots the real Graphlink backend (backend/app.py's create_app()) under
uvicorn for Playwright's E2E suite (ADR-015 stage 15.6).

This is NOT graphlink_desktop.py's launch path - there is no pywebview
window here at all. Playwright automates a real Chromium browser, and
pywebview on Windows is itself a thin wrapper around WebView2, which is
Chromium-based - so pointing Chromium straight at this same backend is a
faithful proxy for what a user actually sees, without needing a native
window in a CI runner. See web_ui/playwright.config.ts's own comment for
the other half of this design (its `webServer` block runs this exact
script and polls /api/health before handing control to the tests).

Isolation: every run gets a FRESH temporary directory (tempfile.mkdtemp(),
not a fixed committed path) for settings_state_file/chat_db_path - never
~/.graphlink/, which is a real user's actual data. A fresh mkdtemp() per
run, rather than a fixed tests_e2e/.e2e_data/ dir this script clears at
startup, was chosen so two overlapping local runs (a developer running
`npx playwright test` while CI or another shell is mid-run) can never
collide on the same session.dat/chats.db, and so there is nothing under
version control to .gitignore or to accidentally commit. The directory is
deliberately NOT cleaned up on exit - this process is killed (not asked to
shut down) by Playwright's webServer teardown, so there is no reliable
"after the last test" hook to clean up from; the OS temp directory's own
normal housekeeping (or a developer's `%TEMP%` cleanup) is left to reclaim
it, exactly as pytest's own tmp_path fixture leaves its directories behind
for inspection after a run.

auth_token=None disables the /api and /ws capability-token requirement
entirely - create_app()'s own docstring calls this "expected only in
tests", which is exactly this use case: there is no graphlink_desktop.py
mint_token() step here for Playwright's Chromium to receive the token
from (that mechanism hands it to pywebview via a URL fragment main() builds
itself - see that file's own docstring), and standing up an equivalent for
a test-only harness would add real complexity for zero real coverage: the
token gate itself already has direct backend unit test coverage
(backend/tests/), so this E2E suite's job is the OTHER thing unit tests
cannot cover - a real browser driving the real built SPA against a real
running backend end to end.

Runs in the FOREGROUND, blocking, on purpose: Playwright's own `webServer`
config (web_ui/playwright.config.ts) starts this process, polls
/api/health until it answers, runs the test suite, and tears the process
down afterward - so this script must never daemonize or background itself,
or Playwright would have nothing to wait on and nothing to kill.

Usage: `python tests_e2e/run_backend.py` from the repo root, or
`python ../tests_e2e/run_backend.py` from web_ui/ (exactly how Playwright's
webServer.command invokes it, since playwright.config.ts's own cwd is
web_ui/) - REPO_ROOT is always computed from this file's own location
(__file__), never the process's current working directory, so both
invocations resolve the same backend/web_ui/dist paths regardless of
where the command was run from. Mirrors graphlink_desktop.py's own
`REPO_ROOT = Path(__file__).resolve().parent; sys.path.insert(0,
str(REPO_ROOT))` pattern exactly, one directory level up.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Fixed, deliberately non-configurable E2E-only port - not used by anything
# else in this repo (graphlink_desktop.py's own real launches always bind an
# OS-assigned free port, or GRAPHLINK_BACKEND_PORT when a developer sets it;
# neither path ever touches this constant). Keeping it a plain literal
# (rather than reading an env var) matches the task's own "keep this
# simple" instruction - Playwright's config hardcodes the same value in its
# `use.baseURL`/`webServer.url`, so the two sides never need to agree on
# anything at runtime.
E2E_PORT = 8799


def main() -> None:
    import uvicorn

    from backend.app import create_app

    e2e_data_dir = Path(tempfile.mkdtemp(prefix="graphlink-e2e-"))

    app = create_app(
        spa_dir=REPO_ROOT / "web_ui" / "dist" / "app",
        settings_state_file=e2e_data_dir / "session.dat",
        chat_db_path=e2e_data_dir / "chats.db",
        auth_token=None,
    )
    uvicorn.run(app, host="127.0.0.1", port=E2E_PORT, log_level="warning")


if __name__ == "__main__":
    main()
