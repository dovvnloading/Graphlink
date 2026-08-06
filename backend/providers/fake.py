"""ADR-006 stage 6.1: FakeProvider - the protocol-conforming test double.

This is what finally makes the real streaming path testable: instead of
monkeypatching api_provider.chat_stream wholesale (the suite-wide conftest
stub, which replaces the entire streaming machinery with one synthetic
chunk), a test installs a FakeProvider at the provider seam and the REAL
event-consumption code runs against scripted events.
"""

from __future__ import annotations

from backend.providers.base import (
    CancelToken,
    ChatRequest,
    ProviderCapabilities,
    ProviderEvent,
)


class FakeProvider:
    """Yields a scripted event sequence and records every request.

    `events` is yielded verbatim, ending with a synthesized "done" (carrying
    the concatenation of the scripted text deltas) unless the script already
    ends with one - so simple tests can script just the deltas. `error`, when
    set, is raised after the scripted events instead of the "done" - the
    mid-stream-failure case. Cancellation is honored between events, matching
    the real providers' between-chunk cooperative checks."""

    def __init__(
        self,
        events: list[ProviderEvent] | None = None,
        *,
        capabilities: ProviderCapabilities | None = None,
        error: Exception | None = None,
    ):
        self.events = list(events or [])
        self.capabilities = capabilities or ProviderCapabilities(streaming=True)
        self.error = error
        self.requests: list[ChatRequest] = []
        self.cancelled_after: int | None = None

    def stream(self, request: ChatRequest, cancel: CancelToken):
        self.requests.append(request)
        emitted_done = False
        for index, event in enumerate(self.events):
            if cancel.is_set():
                self.cancelled_after = index
                # Mirrors the real providers: cooperative cancellation raises
                # the app's one cancellation sentinel, translated nowhere.
                from api_provider import RequestCancelledError

                raise RequestCancelledError("cancelled")
            emitted_done = event.type == "done"
            yield event
        if self.error is not None:
            raise self.error
        if not emitted_done:
            full = "".join(e.text for e in self.events if e.type == "text")
            yield ProviderEvent("done", full)
