"""Token counter state for the new architecture (Qt-removal plan R2).

TokenCounterBridge was always a passive display - window_actions.py pushed
counts into it via update_counts() after real tokenization elsewhere.
inputTokens tracks the live composer draft. outputTokens/contextTokens are
set by backend/canvas.py's send_message/regenerate_response intents once a
reply completes - outputTokens from the reply text itself, contextTokens
from the prior branch history the reply was generated from (excluding, for
a fresh send, the message just typed - inputTokens already owns that text).

ADR-016 stage 16.2 (partial): estimate_tokens delegates to
graphlink_token_estimator's tiktoken-backed TokenEstimator instead of a
whitespace word count - tiktoken has been a real dependency since it was
added for graphlink_chart_agent.py; this counter just hadn't been updated
to use it. Still a pre-flight estimate, not the provider's own reported
usage (that real-usage accounting is the rest of ADR-016).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.events import SessionBus
from graphlink_token_estimator import TokenEstimator


def estimate_tokens(text: str) -> int:
    return TokenEstimator().count_tokens(text)


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
