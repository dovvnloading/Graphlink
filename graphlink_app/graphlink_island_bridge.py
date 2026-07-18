"""Transport-agnostic core for every desktop <-> web island bridge.

A bridge owns exactly two responsibilities that never change across transports:
building a JSON-serializable state snapshot, and running the publish/dispose
lifecycle around it (schemaVersion, monotonic revision, sorted-keys JSON,
idempotent teardown). How the serialized snapshot actually reaches the web
side - a Qt Signal over QWebChannel today, something else if the desktop host
ever changes - is a subclass concern, not this module's. Nothing here imports
Qt, so this class is usable and testable independent of any GUI toolkit.
"""

from __future__ import annotations

import json
from typing import Any


class IslandBridge:
    """Base class for every island's desktop-side state/intent bridge.

    Subclasses provide two things:
    - _build_state_payload() -> dict: the current state, without schemaVersion
      or revision (publish() adds both, so every island gets them for free and
      consistently).
    - _transport_send(payload_json: str) -> None: hand the serialized snapshot
      to whatever transport the concrete bridge uses.

    Optional hooks:
    - _after_publish(payload, serialized): side-channel emissions that piggyback
      on a publish without becoming part of the core snapshot contract.
    - _on_dispose(): real teardown work (disconnect signals, release
      references). Called exactly once; publish() becomes a no-op afterward,
      so a bridge that outlives its transport by a few event-loop ticks during
      shutdown can't emit into a torn-down page.
    """

    SCHEMA_VERSION = 1

    def __init__(self) -> None:
        self._revision = 0
        self._disposed = False

    @property
    def disposed(self) -> bool:
        return self._disposed

    def publish(self) -> None:
        """Rebuild the full state snapshot and send it. A no-op after dispose()."""
        if self._disposed:
            return
        self._revision += 1
        payload = dict(self._build_state_payload())
        payload["schemaVersion"] = self.SCHEMA_VERSION
        payload["revision"] = self._revision
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self._transport_send(serialized)
        self._after_publish(payload, serialized)

    def dispose(self) -> None:
        """Tear the bridge down. Idempotent - safe to call more than once."""
        if self._disposed:
            return
        self._disposed = True
        self._on_dispose()

    def _build_state_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def _transport_send(self, payload_json: str) -> None:
        raise NotImplementedError

    def _after_publish(self, payload: dict[str, Any], serialized: str) -> None:
        """Optional hook for side-channel emissions after the state channel
        has sent. No-op by default."""

    def _on_dispose(self) -> None:
        """Optional hook for subclass teardown. No-op by default."""
