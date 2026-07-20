"""The search-overlay island's outbound wire contract, as a typed Python
dataclass.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale. The query text itself is deliberately NOT part of
this payload: it is pure client-side React state (an uncontrolled input),
exactly like HelpDialog's activeSection - Python never needs to know what the
user is currently typing, only how many matches it found and which one is
current. Reopening always starts with an empty query (matching the legacy
widget's own search_input.clear() on every show), so there is no query state
to restore either.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SearchOverlayStatePayload:
    schemaVersion: int
    revision: int
    currentIndex: int  # 0-based; -1 when there is no current match
    totalMatches: int
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
