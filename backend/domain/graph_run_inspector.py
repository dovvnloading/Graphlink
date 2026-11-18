"""Privacy-preserving, read-only trace of an already-persisted Graph Runner run.

Never serialize run/step dictionaries into an export. They can contain free-form
node titles, errors, and, in older or malformed imports, arbitrary extra keys.
Only the explicitly enumerated, validated metadata below is exportable.
"""

from __future__ import annotations

import heapq
import re
from datetime import datetime
from typing import Any

from backend.domain.graph_run_external import restore_external_attempt

_SHA256 = re.compile(r"[a-f0-9]{64}")
_NODE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:/@+-]{1,192}")
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)
_KINDS = {"note", "chart", "artifact", "table", "model", "tool"}
_OPERATIONS = {"copy", "word_count", "group_sum", "model", "tool"}
_STATUSES = {
    "ready", "waiting", "current", "blocked", "failed", "pending",
    "completed", "partial", "interrupted", "running", "reconciliation-required",
}


def _timestamp(value: Any) -> str | None:
    """Only preserve bounded ISO-8601 timestamps, never arbitrary imported text."""
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _digest(value: Any) -> str | None:
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else None


def _identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None


def _node_id(value: Any) -> str:
    return value if isinstance(value, str) and _NODE_ID.fullmatch(value) else ""


def _duration_ms(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    elapsed = (
        datetime.fromisoformat(end.replace("Z", "+00:00")) -
        datetime.fromisoformat(start.replace("Z", "+00:00"))
    ).total_seconds() * 1000
    return round(elapsed) if 0 <= elapsed <= 366 * 24 * 3600 * 1000 else None


def _blocked_cause(step: dict[str, Any]) -> str | None:
    """Classify *untrusted* free-form details; do not copy their text to JSON."""
    status = step.get("status")
    if not isinstance(status, str) or status not in {"blocked", "failed", "reconciliation-required"}:
        return None
    if status == "reconciliation-required":
        return "External request outcome is uncertain; reconcile before retrying."
    detail = step.get("detail")
    message = detail.lower() if isinstance(detail, str) else ""
    if "upstream" in message or "prerequisite" in message:
        return "An upstream dependency was blocked, stale or incomplete."
    if "cycle" in message:
        return "A dependency cycle prevents execution."
    if "manual edit" in message or "edited" in message:
        return "The output has manual edits that must be reviewed."
    if "no longer exists" in message or "missing" in message:
        return "A saved source, destination or request identity is missing."
    if "adapter" in message or "not authorized" in message:
        return "No authorized adapter is available for this operation."
    if "changed" in message or "rebound" in message:
        return "The input, connection, configuration or output changed since preview."
    if "invalid" in message or "must" in message or "cannot" in message:
        return "The saved input or configuration did not pass validation."
    return "The step was blocked by a saved validation failure."


def _ordered_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable topological order using only the saved source/destination IDs."""
    by_destination: dict[str, int] = {}
    for index, step in enumerate(steps):
        dest = _node_id(step.get("nodeId"))
        if dest:
            by_destination.setdefault(dest, index)
    children: list[list[int]] = [[] for _ in steps]
    degrees = [0] * len(steps)
    for index, step in enumerate(steps):
        parent = by_destination.get(_node_id(step.get("sourceId")))
        if parent is not None:
            children[parent].append(index)
            degrees[index] += 1
    ready = [i for i, degree in enumerate(degrees) if degree == 0]
    heapq.heapify(ready)
    result: list[int] = []
    while ready:
        index = heapq.heappop(ready)
        result.append(index)
        for child in children[index]:
            degrees[child] -= 1
            if degrees[child] == 0:
                heapq.heappush(ready, child)
    # A corrupt/legacy cyclic plan cannot be topologically sorted. Retain
    # those steps without pretending that the cycle was executable.
    present = set(result)
    result.extend(i for i in range(len(steps)) if i not in present)
    return [steps[i] for i in result]


def build_graph_run_trace(run: dict[str, Any]) -> dict[str, Any]:
    """Build the only JSON shape the inspector is allowed to export.

    No node content, prompt, output text, free-form label/detail, credentials,
    endpoint URL, input reference or arbitrary extra persisted field is read
    into the returned structure.
    """
    started = _timestamp(run.get("startedAt"))
    finished = _timestamp(run.get("finishedAt"))
    raw_steps = run.get("steps")
    steps = _ordered_steps([s for s in raw_steps if isinstance(s, dict)]) if isinstance(raw_steps, list) else []
    trace_steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps, 1):
        step_started = _timestamp(step.get("startedAt"))
        step_finished = _timestamp(step.get("finishedAt"))
        attempt = restore_external_attempt(step.get("externalAttempt"))
        model = _identifier(step.get("modelId"))
        provider = _identifier(step.get("provider"))
        if attempt:
            model = _identifier(attempt["model"]) or model
            provider = _identifier(attempt["provider"]) or provider
        phase = (
            "reconciliation-required"
            if step.get("status") == "reconciliation-required" else attempt["phase"]
        ) if attempt else None
        attempts = [{
            "requestId": attempt["requestId"],
            "kind": attempt["kind"],
            "provider": provider,
            "model": _identifier(attempt["model"]),
            "tool": _identifier(attempt["tool"]),
            "inputFingerprint": _digest(attempt["inputHash"]),
            "persistedPhase": attempt["phase"],
            "phase": phase,
            "dispatchedAt": step_started,
            "resolvedAt": step_finished,
        }] if attempt else []
        version = step.get("outputVersion")
        trace_steps.append({
            "index": index,
            "sourceNodeId": _node_id(step.get("sourceId")),
            "destinationNodeId": _node_id(step.get("nodeId")),
            "kind": step.get("kind") if isinstance(step.get("kind"), str) and step["kind"] in _KINDS else "unknown",
            "operation": step.get("operation") if isinstance(step.get("operation"), str) and step["operation"] in _OPERATIONS else "unknown",
            "provider": provider,
            "model": model,
            "inputFingerprints": {
                "input": _digest(step.get("inputHash")),
                "configuration": _digest(step.get("configHash")),
                "destinationBefore": _digest(step.get("targetHash")),
                "endpoint": _digest(step.get("endpointHash")),
            },
            "outputVersion": {
                "sha256": _digest(step.get("outputHash")),
                "number": version if type(version) is int and 0 < version <= 2147483647 else None,
            },
            "status": step.get("status") if isinstance(step.get("status"), str) and step["status"] in _STATUSES else "unknown",
            "blockedCause": _blocked_cause(step),
            "startedAt": step_started,
            "finishedAt": step_finished,
            "durationMs": _duration_ms(step_started, step_finished),
            "attempts": attempts,
        })
    return {
        "schemaVersion": 1,
        "runId": _node_id(run.get("runId")),
        "status": run.get("status") if isinstance(run.get("status"), str) and run["status"] in _STATUSES else "unknown",
        "startedAt": started,
        "finishedAt": finished,
        "durationMs": _duration_ms(started, finished),
        "steps": trace_steps,
    }
