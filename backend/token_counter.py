"""Token counter state for the new architecture (Qt-removal plan R2).

TokenCounterBridge was always a passive display - window_actions.py pushed
counts into it via update_counts() after real tokenization elsewhere.
inputTokens tracks the live composer draft (a whitespace-split estimate -
tiktoken is not a dependency yet; swap in real tokenization here if it ever
becomes one). outputTokens/contextTokens are set by backend/canvas.py's
send_message/regenerate_response intents once a reply completes -
outputTokens from the reply text itself, contextTokens from the prior
branch history the reply was generated from (excluding, for a fresh send,
the message just typed - inputTokens already owns that text).
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

    def set_output_text(self, text: str) -> None:
        self.output_tokens = estimate_tokens(text)

    def set_context_text(self, text: str) -> None:
        self.context_tokens = estimate_tokens(text)

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
