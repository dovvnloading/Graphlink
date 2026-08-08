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
(llama.cpp streams). _MODEL_PRICES_PER_MTOK is a small built-in table for
the common families only; ADR-016 stage 16.2 adds a user-editable local
override on top (SettingsManager.get_pricing_overrides), keyed by EXACT
model id (not prefix-matched like the built-in table - a user typing their
own model id wants that exact string priced, not a substring guess) and
checked first, so a user can price a model this table doesn't know about,
or correct a stale built-in price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

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


def estimate_cost_usd(
    provider, model, prompt_tokens, completion_tokens, *, overrides: dict[str, dict[str, float]] | None = None,
) -> float | None:
    """Best-effort USD cost for one reply. None when the model is unknown
    (never guess a price) or no real counts exist; 0.0 for local providers.

    `overrides` (ADR-016 stage 16.2): a user-editable local pricing table
    (SettingsManager.get_pricing_overrides), keyed by the EXACT model id -
    checked BEFORE the built-in prefix table, so a user's own price always
    wins over a built-in guess. Local-provider $0.00 still short-circuits
    first: overriding a free local model's price is not a use case this
    supports."""
    if prompt_tokens is None and completion_tokens is None:
        return None
    if str(provider or "").strip().lower() in _LOCAL_PROVIDERS:
        return 0.0
    normalized_model = str(model or "").strip().lower()
    if overrides:
        entry = overrides.get(normalized_model)
        if entry is not None:
            return round(
                (prompt_tokens or 0) / 1_000_000 * float(entry.get("input", 0.0))
                + (completion_tokens or 0) / 1_000_000 * float(entry.get("output", 0.0)),
                6,
            )
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
    # ADR-016 stage 16.2: cumulative across every real-usage reply this
    # session has seen so far (never reset by set_input_text/
    # reset_real_usage - those describe the CURRENT draft/reply only). Session-
    # scoped because TokenCounterState itself is one-per-session (see
    # register_token_counter) - restarting the app or evicting the session
    # starts a fresh counter, matching every other in-memory session total in
    # this codebase (no persistence claim is made here).
    session_prompt_tokens: int = 0
    session_completion_tokens: int = 0
    session_estimated_cost_usd: float = 0.0
    # A zero-arg accessor rather than a stored dict: register_token_counter
    # wires this to settings_manager.get_pricing_overrides so every read
    # sees the LIVE value (a setPricingOverrides intent takes effect on the
    # very next reply, no restart needed) - the same "read fresh, don't
    # cache" posture every other settings-backed payload in this codebase
    # already has.
    pricing_overrides_fn: Callable[[], dict[str, dict[str, float]]] | None = None

    def _pricing_overrides(self) -> dict[str, dict[str, float]] | None:
        return self.pricing_overrides_fn() if self.pricing_overrides_fn is not None else None

    def estimate_cost_for(self, prompt_tokens, completion_tokens, *, provider: str, model: str) -> float | None:
        """Public wrapper around estimate_cost_usd, using this counter's own
        LIVE pricing-overrides accessor. ADR-016 stage 16.2: lets a caller
        (backend/api/intents_chat.py's _on_usage) compute the SAME cost
        estimate the composer's token counter would, to stamp onto a reply
        node as a point-in-time snapshot - see ChatState.estimated_cost_usd's
        own comment for why that's a snapshot, not a live recomputation."""
        return estimate_cost_usd(provider, model, prompt_tokens, completion_tokens, overrides=self._pricing_overrides())

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
        keep whatever was last recorded. ADR-016 stage 16.2: also folds this
        reply's counts/cost into the session-cumulative totals, exactly
        once per real reply (never re-accumulated by reset_real_usage or a
        later payload() read, which are non-mutating)."""
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.usage_is_real = prompt_tokens is not None or completion_tokens is not None
        if provider:
            self.provider_name = provider
        if model:
            self.model_id = model
        if self.usage_is_real:
            self.session_prompt_tokens += prompt_tokens or 0
            self.session_completion_tokens += completion_tokens or 0
            reply_cost = estimate_cost_usd(
                self.provider_name, self.model_id, prompt_tokens, completion_tokens,
                overrides=self._pricing_overrides(),
            )
            if reply_cost is not None:
                self.session_estimated_cost_usd += reply_cost

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
                    self.provider_name, self.model_id, self.prompt_tokens, self.completion_tokens,
                    overrides=self._pricing_overrides(),
                )
                if self.usage_is_real
                else None
            ),
            "sessionPromptTokens": self.session_prompt_tokens,
            "sessionCompletionTokens": self.session_completion_tokens,
            "sessionEstimatedCostUsd": self.session_estimated_cost_usd,
        }


def register_token_counter(bus: SessionBus, settings_manager=None) -> TokenCounterState:
    # ADR-016 stage 16.2: settings_manager is optional (many existing tests
    # construct a bare register_token_counter(bus)) - None means "no
    # overrides", i.e. exactly the pre-16.2 built-in-table-only behavior.
    pricing_overrides_fn = settings_manager.get_pricing_overrides if settings_manager is not None else None
    state = TokenCounterState(pricing_overrides_fn=pricing_overrides_fn)
    bus.register_topic("token-counter", state.payload)
    return state
