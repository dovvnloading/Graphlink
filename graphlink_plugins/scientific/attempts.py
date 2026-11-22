"""Transient scientific execution state. No request payload or numerical output is retained."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from weakref import ReferenceType, ref

from backend.domain.model import SceneNode

ATTEMPT_LIMIT = 4096
RETENTION_SECONDS = 30 * 60
_ID = re.compile(r"[a-f0-9]{32}")


@dataclass
class Attempt:
    attempt_id: str
    node: ReferenceType[SceneNode]
    operation_id: str
    request_hash: str
    started: float
    started_at_ms: int
    status: str = "running"
    finished: float | None = None
    error_type: str = ""
    message: str = ""
    receipt_hash: str = ""

    def finish(self, result: dict, receipt_hash: str = "") -> None:
        self.finished = time.monotonic()
        if result.get("ok") is True:
            self.status = "succeeded"
            self.receipt_hash = receipt_hash
            return
        self.error_type = result.get("errorType") or ("conflict" if result.get("conflict") else "validation")
        self.status = "cancelled" if self.error_type == "cancelled" else "failed"
        self.message = str(result.get("error") or "The calculation did not finish.")[:800]

    def wire(self) -> dict:
        result = {"attemptId": self.attempt_id, "operationId": self.operation_id, "status": self.status,
                  "startedAtMs": self.started_at_ms}
        if self.finished is not None:
            result["elapsedMs"] = max(0, int(1000 * (self.finished - self.started)))
        if self.error_type:
            result.update(errorType=self.error_type, error=self.message)
        if self.receipt_hash:
            result["receiptHash"] = self.receipt_hash
        return result


class AttemptLedger:
    """Shared plugin registrations must scope both lookups and serialization to the actual node object."""

    def __init__(self):
        self.entries: dict[tuple[int, str], Attempt] = {}
        self.latest: dict[int, Attempt] = {}
        self.next_sweep = 0.0

    def _prune(self) -> None:
        now = time.monotonic()
        if now < self.next_sweep:
            return
        self.next_sweep = now + 30
        for key, item in list(self.entries.items()):
            if item.node() is None or (item.finished is not None and now - item.finished >= RETENTION_SECONDS):
                del self.entries[key]
                if self.latest.get(key[0]) is item:
                    del self.latest[key[0]]

    def get(self, node, attempt_id: str) -> Attempt | None:
        self._prune()
        item = self.entries.get((id(node), attempt_id))
        return item if item is not None and item.node() is node else None

    def current(self, node) -> Attempt | None:
        self._prune()
        item = self.latest.get(id(node))
        return item if item is not None and item.node() is node else None

    def reserve(self, node, attempt_id: str, operation_id: str, request_hash: str) -> Attempt:
        if not isinstance(attempt_id, str) or not _ID.fullmatch(attempt_id):
            raise ValueError("A calculation needs a 32-character hexadecimal attempt ID.")
        self._prune()
        if len(self.entries) >= ATTEMPT_LIMIT:
            raise ValueError("Calculation history is full. Try again after older attempts expire.")
        item = Attempt(attempt_id, ref(node), operation_id, request_hash, time.monotonic(), int(time.time() * 1000))
        self.entries[id(node), attempt_id] = self.latest[id(node)] = item
        return item
