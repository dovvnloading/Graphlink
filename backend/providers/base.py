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
- `usage` events (6.8) and `tool_call` events (ADR-007) are named in the
  ADR's event union but deliberately absent from EVENT_TYPES until the stage
  that makes them real - an event type nothing can emit yet would just be an
  untestable claim.
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

EVENT_TYPES = ("text", "reasoning", "reset", "done")


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider (with its configured model) can actually do - the
    single source of truth orchestration and UI consult instead of
    provider-type string checks (ADR-006 §1). Complements - does not replace -
    graphlink_model_catalog.ModelDescriptor: the descriptor describes a MODEL
    in a catalog; this describes the live provider+model pair a request will
    actually hit."""

    streaming: bool = False
    reasoning: bool = False
    vision: bool = False
    audio: bool = False
    image_generation: bool = False
    # ADR-007 / 6.8 - declared now so capability consumers have a stable
    # shape, but nothing sets them True until the stage that implements them.
    tools: bool = False
    structured_output: bool = False


@dataclass(frozen=True)
class ChatRequest:
    """One chat completion request, provider-agnostic.

    `messages` uses the app's existing wire shape untouched ([{"role": ...,
    "content": str | [part-dict]}], with "image_bytes"/"audio_file" parts) -
    each provider owns converting that to its SDK's format, exactly the
    per-provider `prepare_messages` responsibility ADR-006 §1 assigns.
    `extra_kwargs` is the passthrough surface today's chat(**kwargs) callers
    rely on (e.g. the chart agent's format hints); it shrinks as stages
    6.3-6.8 give its remaining uses first-class fields."""

    task: str
    messages: list
    reasoning_level: str = "off"
    extra_kwargs: Mapping[str, Any] = field(default_factory=dict)


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
                   shape downstream response parsing depends on).
    """

    type: Literal["text", "reasoning", "reset", "done"]
    text: str = ""


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
        caller until the stage that moves it in here."""
        ...
