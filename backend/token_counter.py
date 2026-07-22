"""Token counter state for the new architecture (Qt-removal plan R2).

TokenCounterBridge was always a passive display - window_actions.py pushed
counts into it via update_counts() after real tokenization elsewhere.
Nothing populates outputTokens/contextTokens yet (no send flow until R4, no
conversation history until R3), so this mirrors that: inputTokens tracks the
live composer draft for real (a whitespace-split estimate - tiktoken is not
a dependency yet; swap in real tokenization here once R4 wires it), the rest
sit at 0 rather than showing a fabricated number.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.events import SessionBus


def estimate_tokens(text: str) -> int:
    return len(text.split())


@dataclass
class TokenCounterState:
    input_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0

    def set_input_text(self, text: str) -> None:
        self.input_tokens = estimate_tokens(text)

    def payload(self) -> dict[str, Any]:
        total = self.input_tokens + self.output_tokens + self.context_tokens
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "contextTokens": self.context_tokens,
            "totalTokens": total,
        }


def register_token_counter(bus: SessionBus) -> TokenCounterState:
    state = TokenCounterState()
    bus.register_topic("token-counter", state.payload)
    return state
