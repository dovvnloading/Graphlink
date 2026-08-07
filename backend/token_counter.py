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
whitespace word count.

ADR-006 stage 6.8: real, provider-reported usage. When a chat reply's
provider reports actual token counts (see backend/providers/base.py's
normalize_usage), set_real_usage records them and payload() switches the
total to prompt+completion. The two modes are ALTERNATIVES, never additive:
the provider's prompt count already covers context + input (the entire
request), so summing it with the estimate columns would double-count. The
four estimate keys stay in the payload either way - they remain the honest
pre-flight view, and the only view for providers that report nothing
(llama.cpp streams). A user-editable pricing config is deliberately out of
scope here - that is ADR-016 stage 16.2's job; _MODEL_PRICES_PER_MTOK is a
small built-in table for the common families only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.events import SessionBus
from graphlink_token_estimator import TokenEstimator


def estimate_tokens(text: str) -> int:
    return TokenEstimator().count_tokens(text)


# ADR-006 stage 6.8: (input, output) USD per MILLION tokens, matched by
# model-id prefix (first match wins). Local providers cost 0.0 - stated
# explicitly rather than falling through to None, so the UI can show a
# genuine $0.00 for local models. Unknown cloud models return None (no
# guess). Kept deliberately small; see the module docstring.
_MODEL_PRICES_PER_MTOK: tuple[tuple[str, tuple[float, float]], ...] = (
    ("claude-opus", (15.0, 75.0)),
    ("claude-sonnet", (3.0, 15.0)),
    ("claude-haiku", (0.80, 4.0)),
    ("claude-3-5-haiku", (0.80, 4.0)),
    ("claude-3", (3.0, 15.0)),
    ("gpt-4o-mini", (0.15, 0.60)),
    ("gpt-4o", (2.50, 10.0)),
    ("gpt-4.1-mini", (0.40, 1.60)),
    ("gpt-4.1-nano", (0.10, 0.40)),
    ("gpt-4.1", (2.0, 8.0)),
    ("gpt-5-mini", (0.25, 2.0)),
    ("gpt-5", (1.25, 10.0)),
    ("o3", (2.0, 8.0)),
    ("o4-mini", (1.10, 4.40)),
    ("gemini-2.5-pro", (1.25, 10.0)),
    ("gemini-2.5-flash-lite", (0.10, 0.40)),
    ("gemini-2.5-flash", (0.30, 2.50)),
    ("gemini-2.0-flash", (0.10, 0.40)),
)

_LOCAL_PROVIDERS = {"ollama", "llama.cpp", "llamacpp", "local"}


def estimate_cost_usd(provider, model, prompt_tokens, completion_tokens) -> float | None:
    """Best-effort USD cost for one reply. None when the model is unknown
    (never guess a price) or no real counts exist; 0.0 for local providers."""
    if prompt_tokens is None and completion_tokens is None:
        return None
    if str(provider or "").strip().lower() in _LOCAL_PROVIDERS:
        return 0.0
    normalized_model = str(model or "").strip().lower()
    for prefix, (input_price, output_price) in _MODEL_PRICES_PER_MTOK:
        if normalized_model.startswith(prefix):
            return round(
                (prompt_tokens or 0) / 1_000_000 * input_price
                + (completion_tokens or 0) / 1_000_000 * output_price,
                6,
            )
    return None


@dataclass
class TokenCounterState:
    input_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0
    # ADR-006 stage 6.8 - see the module docstring.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    usage_is_real: bool = False
    provider_name: str = ""
    model_id: str = ""

    def set_input_text(self, text: str) -> None:
        self.input_tokens = estimate_tokens(text)
        # ADR-006 stage 6.8: typing a new draft starts a NEW request - the
        # previous reply's exact usage no longer describes what's on screen.
        self.usage_is_real = False

    def set_output_text(self, text: str) -> None:
        self.output_tokens = estimate_tokens(text)

    def set_context_text(self, text: str) -> None:
        self.context_tokens = estimate_tokens(text)

    def reset_real_usage(self) -> None:
        """ADR-006 stage 6.8 review fix (stale real usage): called at
        request START (send/regenerate, alongside set_context_text) so a
        request that never reports usage - a provider without usage
        support, a killed stream, a cancel - can't leave the PREVIOUS
        request's exact numbers on display. Estimates take over unless
        fresh usage lands for THIS request."""
        self.prompt_tokens = None
        self.completion_tokens = None
        self.usage_is_real = False

    def set_real_usage(self, prompt_tokens, completion_tokens, *, provider: str = "", model: str = "") -> None:
        """Record provider-reported counts for the reply that just
        completed. provider/model feed the cost estimate; omitted values
        keep whatever was last recorded."""
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.usage_is_real = prompt_tokens is not None or completion_tokens is not None
        if provider:
            self.provider_name = provider
        if model:
            self.model_id = model

    def payload(self) -> dict[str, Any]:
        if self.usage_is_real:
            # prompt already includes context+input - alternatives, not
            # additive (see the module docstring).
            total = (self.prompt_tokens or 0) + (self.completion_tokens or 0)
        else:
            total = self.input_tokens + self.output_tokens + self.context_tokens
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "contextTokens": self.context_tokens,
            "totalTokens": total,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "usageIsReal": self.usage_is_real,
            "estimatedCostUsd": (
                estimate_cost_usd(
                    self.provider_name, self.model_id, self.prompt_tokens, self.completion_tokens
                )
                if self.usage_is_real
                else None
            ),
        }


def register_token_counter(bus: SessionBus) -> TokenCounterState:
    state = TokenCounterState()
    bus.register_topic("token-counter", state.payload)
    return state
