"""Desktop-side state bridge for the token-counter island.

Read-only display, the simplest of the three interaction shapes Phase 2's
pilot islands cover: state flows one way, Python -> JS, with no intents
coming back. ChatWindow calls update_counts()/reset() directly (same public
API graphlink_widgets/tokens.py's TokenCounterWidget exposed) wherever it
used to update the old widget's labels; every call publishes the full
current snapshot, matching every other island's publish-the-whole-state
convention rather than sending deltas.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from graphlink_island_bridge import IslandBridge


class TokenCounterBridge(IslandBridge, QObject):
    stateChanged = Signal(str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        IslandBridge.__init__(self)
        self._input_tokens = 0
        self._output_tokens = 0
        self._context_tokens = 0
        self._total_tokens = 0

    def _transport_send(self, payload_json: str) -> None:
        self.stateChanged.emit(payload_json)

    def _build_state_payload(self) -> dict[str, Any]:
        return {
            "inputTokens": self._input_tokens,
            "outputTokens": self._output_tokens,
            "contextTokens": self._context_tokens,
            "totalTokens": self._total_tokens,
        }

    @Slot()
    def ready(self):
        self.publish()

    def update_counts(self, input_tokens=None, output_tokens=None, context_tokens=None, total_tokens=None):
        """Partial update - same semantics as the widget this replaces: any
        argument left as None leaves that field unchanged."""
        if input_tokens is not None:
            self._input_tokens = max(0, int(input_tokens))
        if output_tokens is not None:
            self._output_tokens = max(0, int(output_tokens))
        if context_tokens is not None:
            self._context_tokens = max(0, int(context_tokens))
        if total_tokens is not None:
            self._total_tokens = max(0, int(total_tokens))
        self.publish()

    def reset(self):
        self._input_tokens = 0
        self._output_tokens = 0
        self._context_tokens = 0
        self._total_tokens = 0
        self.publish()
