"""The token-counter island's outbound wire contract, as typed Python
dataclasses.

THIS IS A WIRE FORMAT, NOT A DOMAIN MODEL - see graphlink_composer_payload.py
for the fuller rationale (identical here, just applied to a much smaller
payload). Field names are camelCase to match the JSON keys
TokenCounterBridge._build_state_payload() emits and
web_ui/src/lib/bridge-core/generated/token-counter-state.ts mirrors.

Cross-checked against a live TokenCounterBridge snapshot by
tests/test_token_counter_payload_schema.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenCounterStatePayload:
    """The complete published snapshot, including the envelope fields
    IslandBridge.publish() adds to every island's payload."""

    schemaVersion: int
    revision: int
    inputTokens: int
    outputTokens: int
    contextTokens: int
    totalTokens: int
    # See ComposerStatePayload's identical field for the full negotiation
    # rationale; optional for the same reason (models a sender predating this
    # field, not today's - IslandBridge.publish() always emits it).
    minCompatibleSchemaVersion: int | None = None
