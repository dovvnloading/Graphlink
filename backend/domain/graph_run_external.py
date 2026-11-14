"""Crash-safe dispatch fence for future Graph Runner model and tool steps.

The caller must authorize the operation and pin its input artifact before entry.
Only non-secret provider/model/tool identifiers and a versioned input reference
are journaled. This module does not retry, grant permissions, or make API calls.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from backend.domain.model import SceneError

_IDENTIFIER = re.compile(r"[A-Za-z0-9._:/@+-]{1,192}")
_INPUT_REF = re.compile(r"[A-Za-z0-9._:/@+-]{1,256}")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_REQUEST_ID = re.compile(r"graph-[a-f0-9]{32}")
_ATTEMPT_FIELDS = frozenset(
    {"requestId", "kind", "provider", "model", "tool", "inputRef", "inputHash", "phase"}
)
_PHASES = {"dispatched", "completed", "reconciliation-required"}

RECONCILIATION_DETAIL = (
    "The external request may have run or incurred a charge. "
    "Reconcile its request ID with the provider before starting another attempt."
)


@dataclass(frozen=True)
class ExternalCall:
    """Immutable, non-secret identity of one *already authorized* external call."""

    kind: Literal["model", "tool"]
    provider_name: str
    input_ref: str
    input_hash: str
    model_id: str = ""
    tool: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("model", "tool"):
            raise SceneError("External calls must be model or tool operations.")
        for name, value in (("provider", self.provider_name), ("input reference", self.input_ref)):
            pattern = _INPUT_REF if name == "input reference" else _IDENTIFIER
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise SceneError(f"Use a non-secret, bounded {name} identifier.")
        if not isinstance(self.input_hash, str) or not _SHA256.fullmatch(self.input_hash):
            raise SceneError("The external input must have a pinned SHA-256 fingerprint.")
        if self.kind == "model":
            if not isinstance(self.model_id, str) or not _IDENTIFIER.fullmatch(self.model_id) or self.tool:
                raise SceneError("A model call needs one explicit model and no tool.")
        elif not isinstance(self.tool, str) or not _IDENTIFIER.fullmatch(self.tool) or self.model_id:
            raise SceneError("A tool call needs one explicit tool and no model.")

    def journal_record(self) -> dict[str, str]:
        return {
            "requestId": f"graph-{uuid.uuid4().hex}",
            "kind": self.kind,
            "provider": self.provider_name,
            "model": self.model_id,
            "tool": self.tool,
            "inputRef": self.input_ref,
            "inputHash": self.input_hash,
            # The *durable* pre-dispatch record is conservatively uncertain
            # after a crash, including a crash before the network send.
            "phase": "dispatched",
        }


def restore_external_attempt(raw: Any) -> dict[str, str] | None:
    """Read only the version-one journal fields; malformed records fail closed."""
    if not isinstance(raw, dict) or set(raw) != _ATTEMPT_FIELDS:
        return None
    if any(not isinstance(value, str) for value in raw.values()):
        return None
    if not _REQUEST_ID.fullmatch(raw["requestId"]) or raw["phase"] not in _PHASES:
        return None
    try:
        ExternalCall(
            kind=raw["kind"],  # type: ignore[arg-type]
            provider_name=raw["provider"],
            model_id=raw["model"],
            tool=raw["tool"],
            input_ref=raw["inputRef"],
            input_hash=raw["inputHash"],
        )
    except SceneError:
        return None
    return {key: raw[key] for key in _ATTEMPT_FIELDS}


async def _drain_checkpoint(checkpoint: Callable[[], Awaitable[bool]]) -> None:
    """Require a confirmed durable write and drain it on cancellation."""
    task = asyncio.ensure_future(checkpoint())
    try:
        saved = await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
    if saved is not True:
        raise SceneError("The external request checkpoint was not confirmed.")


async def dispatch_external_attempt(
    step: dict[str, Any],
    call: ExternalCall,
    *,
    checkpoint: Callable[[], Awaitable[bool]],
    invoke: Callable[[str], Awaitable[Any]],
    apply_result: Callable[[Any], Awaitable[str]],
) -> str:
    """Persist the request fence, invoke once, then checkpoint output + receipt.

    `checkpoint` must return True after durably saving the *same* graph and
    step journal in one transaction. `apply_result` must write the output in memory and return
    its SHA-256 digest; it must not separately persist an output. The caller
    must hold the graph mutation guard throughout this function. A timeout,
    cancellation, or failed result checkpoint is **never** an automatic retry.
    """
    if step.get("status") != "pending" or "externalAttempt" in step:
        raise SceneError("This external step was already attempted. Inspect its saved request.")
    if step.get("inputHash") != call.input_hash or not step.get("sourceId") or not step.get("nodeId"):
        raise SceneError("The external step's pinned input or node identity changed.")

    prior_detail = step.get("detail", "")
    attempt = call.journal_record()
    step["externalAttempt"] = attempt
    step.update(status="running", detail="External request reserved; awaiting its outcome.")
    try:
        # No invoke call of any kind is allowed before this durable fence.
        await _drain_checkpoint(checkpoint)
    except BaseException:
        # Dispatch has not happened. The caller can preview again after
        # resolving the persistence error, but not on this failed request.
        step.pop("externalAttempt")
        step.update(status="pending", detail=prior_detail)
        raise

    try:
        response = await invoke(attempt["requestId"])
        output_hash = await apply_result(response)
        if not isinstance(output_hash, str) or not _SHA256.fullmatch(output_hash):
            raise SceneError("External output must produce a SHA-256 artifact fingerprint.")
    except BaseException:
        attempt["phase"] = "reconciliation-required"
        step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)
        try:
            await _drain_checkpoint(checkpoint)
        except BaseException:
            # The already-durable 'dispatched' fence is sufficient to block
            # automatic replay even if the newer uncertain state cannot save.
            pass
        raise

    attempt["phase"] = "completed"
    step.update(status="completed", detail="External output checkpointed.", outputHash=output_hash)
    try:
        # The adapter's output and the completion receipt must be atomic.
        await _drain_checkpoint(checkpoint)
    except BaseException:
        attempt["phase"] = "reconciliation-required"
        step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)
        raise
    return output_hash
