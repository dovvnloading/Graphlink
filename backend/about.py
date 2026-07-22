"""About panel state for the new architecture (Qt-removal plan R2.5).

The simplest surface in the whole migration (confirmed by recon against
graphlink_about_bridge.py's own docstring): zero live state, one
parameterized intent. Every field here is a build-time literal except
appVersion, which comes from graphlink_version.py directly - NOT via
graphlink_update.py, which imports PySide6 at module scope (QThread) and
would silently reintroduce Qt into backend/ if imported here.
"""

from __future__ import annotations

from typing import Any

from graphlink_version import APP_VERSION

from backend.events import SessionBus

APP_NAME = "Graphlink"
REPOSITORY_URL = "https://github.com/dovvnloading/Graphlink"
DEVELOPER_NAME = "Matthew Robert Wesney"
DEVELOPER_WEBSITE_URL = "https://mattwesney.com"
DEVELOPER_GITHUB_URL = "https://github.com/dovvnloading"
COPYRIGHT_TEXT = "© 2026"


def about_payload() -> dict[str, Any]:
    return {
        "appName": APP_NAME,
        "appVersion": APP_VERSION,
        "repositoryUrl": REPOSITORY_URL,
        "developerName": DEVELOPER_NAME,
        "developerWebsiteUrl": DEVELOPER_WEBSITE_URL,
        "developerGithubUrl": DEVELOPER_GITHUB_URL,
        "copyrightText": COPYRIGHT_TEXT,
    }


def register_about(bus: SessionBus) -> None:
    # Topic name "app-about" (not "about"): avoids colliding with the
    # legacy island's already-generated about-state.ts schema, which
    # requires no fields this payload omits - same split as composer/scene.
    bus.register_topic("app-about", about_payload)
    # No intents: opening external links is plain client-side window.open()
    # in the SPA (there is no Python-owned window for openExternal to act
    # on behalf of), and close is owned entirely by the R2.1 overlay system.
