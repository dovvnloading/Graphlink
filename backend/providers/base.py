"""ADR-006 stage 6.1: the Provider protocol - the seam that replaces
api_provider.py's ~30-site if/elif dispatch surface, one provider at a time.

Deliberate staging (per ADR-006's own stage table, recorded here so the gap
between this file and the ADR's §1 sketch reads as a plan, not a drift):

- The ADR's target protocol is async-native (`async def stream(...) ->
  AsyncIterator[ProviderEvent]`). THIS stage's protocol is a plain sync
  iterator, because today every provider call runs inside a worker thread
  (`asyncio.to_thread` in backend/agents.py) against sync SDKs - an async
  protocol here would mean nesting a private event loop inside each worker
  thread for zero behavioral gain. Stage 6.2 (non-blocking dispatch) and 6.3
  (async SDK ports) flip this async-native; the EVENT vocabulary below is
  already the ADR's, so that flip changes the iteration keyword, not the
  data model.
- `usage` events (6.8, done) and `tool_call` events (ADR-007 stage 7.1, done)
  were named in the ADR's event union ahead of the stage that made them real
  - an event type nothing can emit yet would just be an untestable claim.
  Both are now live; see EVENT_TYPES' own comment for `tool_call`'s shape.
- Cancellation rides the same `threading.Event` the whole run pipeline
  already uses (RunLifecycle claims one per run); `CancelToken` wraps it so
  the protocol owns the NAME while stage 6.2 swaps the mechanism underneath
  without touching provider implementations. The cancellation ERROR remains
  api_provider.RequestCancelledError for now - every consumer
  (backend/agents.py's dispatch, the WS error path) catches that exact type,
  and moving the class is a rename-across-the-codebase that belongs to the
  stage that owns dispatch (6.2), not the one introducing the seam.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Mapping, Protocol, runtime_checkable

from graphlink_model_catalog import ModelRef

# ADR-006 stage 6.8: "usage" joins the union the module docstring reserved
# for it. Convention: providers do NOT emit a separate "usage" event - they
# attach normalized usage to the terminal "done" event (done.usage), which
# keeps the chat_stream consuming loop simple. The type stays in this union
# so the vocabulary matches the ADR's event union.
#
# ADR-007 stage 7.1: "tool_call" DOES stand alone (unlike usage/reasoning),
# because a single turn can request MULTIPLE independent tool calls and the
# caller needs each one as its own addressable unit to invoke and answer
# separately - collapsing them onto "done" would lose that plurality. Each
# provider is responsible for accumulating its own native incremental shape
# (OpenAI streams tool-call arguments as JSON text fragments keyed by
# index; Anthropic streams them as input_json_delta events on a tool_use
# content block; Ollama and Gemini both deliver a tool call as one already-
# complete object, nothing to accumulate) into exactly ONE ProviderEvent
# per complete call, with `arguments` always a parsed dict - never a raw
# JSON string leaking a provider's wire format to the caller. The stream
# still ends with exactly one "done" event after any tool_call events (its
# `text` may be empty - a turn that is pure tool-calling has no answer
# text yet); that keeps chat_stream's "ends with exactly one done" contract
# true regardless of how many tools were called.
EVENT_TYPES = ("text", "reasoning", "reset", "done", "usage", "tool_call")


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider (with its configured model) can actually do - the
    single source of truth orchestration and UI WILL consult instead of
    provider-type string checks (ADR-006 §1; nothing consumes these yet -
    the first consumers arrive with 6.4's streaming gate and the runtime
    switcher/attachment gating in 6.5/ADR-012). Complements - does not replace -
    graphlink_model_catalog.ModelDescriptor: the descriptor describes a MODEL
    in a catalog; this describes the live provider+model pair a request will
    actually hit."""

    streaming: bool = False
    reasoning: bool = False
    vision: bool = False
    audio: bool = False
    image_generation: bool = False
    # ADR-007 stage 7.1: True where a provider's stream() actually translates
    # ToolSpecs into native tool params and normalizes ToolCallEvents back -
    # OpenAI, Anthropic, Gemini always (current model families all support
    # native tool use); Ollama per-model via the same cached show() probe
    # capabilities/vision/audio already use (some models genuinely lack
    # tool support server-side); llama.cpp stays False - no reliable local
    # detection of whether a GGUF's chat template supports tool syntax, and
    # the ADR's own stage-7.1 provider list never names it. A caller whose
    # target provider has tools=False must use the structured_output/
    # respond_json fallback (7.3), not attempt native tools.
    tools: bool = False
    # ADR-007 stage 7.3 - declared now so capability consumers have a
    # stable shape, but nothing sets it True until that stage.
    structured_output: bool = False


@dataclass(frozen=True)
class ToolSpec:
    """ADR-007 stage 7.1: one tool a model may call, in the app's neutral
    shape - each provider translates this into its own native tool
    parameter (OpenAI `tools[].function`, Anthropic `tools[]`, Gemini
    `tools[].functionDeclarations[]`, Ollama `tools[].function`).

    `input_schema` is a JSON Schema object (Draft 2020-12, matching
    `respond_json`'s contract in 7.3) describing the call's arguments -
    NOT a full Draft 2020-12 feature set on every provider: Gemini's
    `parameters` field is an OpenAPI 3.0 Schema subset (no `$defs`, no
    `additionalProperties`), a known, documented impedance mismatch rather
    than a bug - callers targeting Gemini should keep schemas to the
    OpenAPI-compatible subset (type/properties/required/enum/items).

    Deliberately NO `annotations` (read_only/destructive/idempotent/
    requires_approval) field yet - the ADR's own §1 sketch marks that
    "optional", and it is ToolRegistry's field to own (7.2), not the
    provider-facing spec's: the provider only needs name/description/
    schema to build its native tool param, never the approval policy."""

    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class ToolCall:
    """ADR-007 stage 7.1: one complete, normalized tool call a model
    requested - `arguments` is always an already-parsed dict, regardless of
    whether the provider streamed it as JSON text fragments (OpenAI,
    Anthropic) or delivered it whole (Ollama, Gemini). `id` correlates this
    call to the tool-result message fed back on the next turn; Ollama and
    Gemini don't provide one natively, so their providers synthesize a
    stable per-turn id (see each provider's own comment)."""

    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ChatRequest:
    """One chat completion request, provider-agnostic.

    `messages` uses the app's existing wire shape untouched ([{"role": ...,
    "content": str | [part-dict]}], with "image_bytes"/"audio_file" parts) -
    each provider owns converting that to its SDK's format, exactly the
    per-provider `prepare_messages` responsibility ADR-006 §1 assigns.
    ADR-007 stage 7.1 widens that shape by exactly two new message roles,
    both provider-translated the same way as everything else: an assistant
    turn that called tools carries a `tool_calls: list[dict]` key (each
    `{"id", "name", "arguments"}`, `content` may still carry lead-in text);
    a tool's result is fed back as `{"role": "tool", "tool_call_id": id,
    "name": tool_name, "content": result_text}`.

    `extra_kwargs` is the passthrough surface today's chat(**kwargs) callers
    rely on (e.g. the chart agent's format hints); it shrinks as stages
    6.3-6.8 give its remaining uses first-class fields.

    `tools`: ToolSpecs available this turn, or empty for no tool access -
    ADR-007 stage 7.1. A provider whose `capabilities.tools` is False must
    never receive a non-empty `tools` here (the caller's job to check
    capabilities first, mirroring how streaming/vision gating already
    works); providers do not re-validate this against their own
    capabilities, matching every other field's caller-trusts-caller posture.

    Deliberately NO reasoning_level field: the level is provider
    configuration (each provider is constructed with it, from the caller's
    own settings snapshot), not per-request data - a field here would be a
    second source of truth that a provider could silently ignore
    (adversarial-review finding on the first draft, which carried exactly
    that dead field). Stage 6.5's per-session ProviderRuntime owns where the
    level ultimately lives.

    `model_ref` (ADR-018 stage 18.1): the resolved graphlink_model_catalog.
    ModelRef that DROVE this provider instance's construction - carried here
    for TRACEABILITY (diagnostics, "why this model" inspectability), not as
    a second source of truth a provider re-derives its model from. A
    provider still gets its model at construction time exactly as before
    (each `*Provider.__init__` takes `model`); this field never overrides
    that. None for every call site that predates ADR-018 (nothing has
    broken; `model_ref` is purely additive)."""

    task: str
    messages: list
    extra_kwargs: Mapping[str, Any] = field(default_factory=dict)
    tools: tuple[ToolSpec, ...] = ()
    model_ref: ModelRef | None = None


@dataclass(frozen=True)
class ProviderEvent:
    """One streaming event. Types:

    - "text":      an incremental visible-content delta (never cumulative).
    - "reasoning": an incremental thinking delta - surfaced as its own channel
                   here even though today's UI never displays it (chat_stream's
                   on_chunk contract deliberately drops these; a future stage
                   can render them without touching providers).
    - "reset":     discard everything accumulated so far (a provider-internal
                   retry discarded the prior attempt's partial output).
    - "done":      terminal; `text` carries the COMPLETE final content (for
                   Ollama that is the composed "<think>...</think>\\n{answer}"
                   shape downstream response parsing depends on) - EMPTY when
                   the turn was pure tool-calling with no answer text yet.
                   ADR-006 stage 6.8: `usage`, when the provider reported it,
                   rides the done event as {"prompt_tokens": int | None,
                   "completion_tokens": int | None} - normalized keys, never
                   a separate event.
    - "usage":     reserved in the union for protocol completeness; today no
                   provider emits it standalone (usage rides "done" - see
                   EVENT_TYPES' own comment).
    - "tool_call": ADR-007 stage 7.1: `tool_call` carries one complete,
                   normalized `ToolCall`. UNLIKE usage, this stands alone
                   (not bundled onto "done") - see EVENT_TYPES' own comment
                   for why. Zero or more of these precede the terminal
                   "done" event; never after it.
    """

    type: Literal["text", "reasoning", "reset", "done", "usage", "tool_call"]
    text: str = ""
    usage: dict | None = None
    tool_call: ToolCall | None = None


def normalize_usage(prompt_tokens, completion_tokens) -> dict | None:
    """ADR-006 stage 6.8: the ONE normalized usage shape every provider
    attaches to its done event - {"prompt_tokens": int | None,
    "completion_tokens": int | None}, or None when the provider reported
    nothing at all. Tolerates non-int server values (returns None fields)."""
    def _as_int(value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    prompt = _as_int(prompt_tokens)
    completion = _as_int(completion_tokens)
    if prompt is None and completion is None:
        return None
    return {"prompt_tokens": prompt, "completion_tokens": completion}


class CancelToken:
    """Cooperative cancellation handle passed into every provider call.

    Wraps the run's existing threading.Event (or None for uncancellable
    callers) so providers depend on this protocol type, never on the event
    directly - stage 6.2 changes what is underneath, not the provider code."""

    __slots__ = ("event",)

    def __init__(self, event: threading.Event | None = None):
        self.event = event

    def is_set(self) -> bool:
        return self.event is not None and self.event.is_set()


@runtime_checkable
class Provider(Protocol):
    """The seam. One concrete class per provider; adding a 6th provider is
    one new class implementing this, not 15 edits across api_provider.py."""

    capabilities: ProviderCapabilities

    def stream(self, request: ChatRequest, cancel: CancelToken) -> Iterator[ProviderEvent]:
        """Yield ProviderEvents for one completion, ending with exactly one
        "done" event carrying the full final text. Raise (never swallow) on
        failure; exception translation to user-facing messages stays with the
        caller until the stage that moves it in here.

        LAZY-GENERATOR CONTRACT (adversarial-review finding, pinned here so
        stage 6.2 designs against it, not around it): implementations are
        generator functions, so NOTHING in the body - including entry cancel
        checks and request prep/validation - runs at call time; it runs on
        the first next(). Callers must therefore consume the iterator inside
        whatever try/except owns error translation (both api_provider call
        sites do), and must never treat a successful stream() CALL as "the
        request validated"."""
        ...
