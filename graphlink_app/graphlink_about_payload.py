"""The about-dialog island's outbound wire contract, as a typed Python
dataclass.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. Every field here is a build-time constant (app
name/version, project/developer links, copyright text) - there is no live
app state on this surface at all, the only migrated island where that's
true. Field names are camelCase to match the JSON keys
AboutBridge._build_state_payload() emits and
web_ui/src/lib/bridge-core/generated/about-state.ts mirrors.

Cross-checked against a live AboutBridge snapshot by
tests/test_about_payload_schema.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AboutStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    appName: str
    appVersion: str
    repositoryUrl: str
    developerName: str
    developerWebsiteUrl: str
    developerGithubUrl: str
    copyrightText: str
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
