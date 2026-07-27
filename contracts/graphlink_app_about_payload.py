"""The SPA About topic's wire contract (Qt-removal plan R2.5).

Field-for-field identical to graphlink_about_payload.py::AboutStatePayload,
but registered as a separate codegen artifact under a distinct topic name
("about") so the SPA's generated validator doesn't collide with the
legacy island's own about-state.ts - same split rationale as
graphlink_app_composer_payload.py (R2.3) and graphlink_scene_payload.py
(R1). The legacy file and its Qt-coupled bridge are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppAboutStatePayload:
    schemaVersion: int
    revision: int
    appName: str
    appVersion: str
    repositoryUrl: str
    developerName: str
    developerWebsiteUrl: str
    developerGithubUrl: str
    copyrightText: str
    minCompatibleSchemaVersion: int | None = None
