"""Saved Graph Runner records. Only the node references change on restore."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from backend.domain.graph_run_external import RECONCILIATION_DETAIL, restore_external_attempt
from backend.domain.graph_run_inspector import _identifier, _timestamp

RUN_HISTORY_LIMIT = 12
RUN_ID = re.compile(r"[a-zA-Z0-9_-]{8,100}")
DIGEST = re.compile(r"[a-f0-9]{64}")
_RUN_STATUSES = {"completed", "partial", "cancelled", "running", "interrupted", "reconciliation-required"}
_STEP_STATUSES = {"pending", "current", "completed", "blocked", "failed", "cancelled", "running", "reconciliation-required"}


def _superseded_tool_fence(step: dict[str, Any], history: Any) -> bool:
    """A later successful call can replace an acknowledged refresh barrier."""
    node_id = step.get("nodeId")
    barrier_time = _timestamp(step.get("finishedAt")) or _timestamp(step.get("startedAt"))
    if not isinstance(node_id, str) or not node_id or barrier_time is None or not isinstance(history, list):
        return False
    barrier = datetime.fromisoformat(barrier_time.replace("Z", "+00:00"))
    for run in history:
        if not isinstance(run, dict) or not isinstance(run.get("steps"), list):
            continue
        for candidate in run["steps"]:
            if (not isinstance(candidate, dict) or candidate.get("nodeId") != node_id
                    or candidate.get("kind") != "tool" or candidate.get("status") != "completed"
                    or candidate.get("errorType") == "tool_error"
                    or not isinstance(candidate.get("outputHash"), str) or not DIGEST.fullmatch(candidate["outputHash"])):
                continue
            attempt = restore_external_attempt(candidate.get("externalAttempt"))
            started = _timestamp(candidate.get("startedAt"))
            finished = _timestamp(candidate.get("finishedAt"))
            if (attempt is not None and attempt["kind"] == "tool" and attempt["phase"] == "completed"
                    and attempt["inputHash"] == candidate.get("inputHash") and started and finished
                    and datetime.fromisoformat(finished.replace("Z", "+00:00"))
                    >= datetime.fromisoformat(started.replace("Z", "+00:00")) > barrier):
                return True
    return False


def can_prune_run(run: Any, history: Any = None) -> bool:
    """Retire finished history only when it cannot erase an uncertain request.

    A cancelled/partial run can still contain a dispatched external fence.
    Check the steps and validated fences, never just the top-level status.
    Invalid journals remain visible for reconciliation rather than being
    mistaken for proof that an external request was never sent.
    An acknowledged tool request still invalidates older saved output across
    undo. Retain that barrier until a later successful call supersedes it.
    """
    if (not isinstance(run, dict) or not isinstance(run.get("status"), str)
            or run["status"] not in {"completed", "partial", "cancelled"}
            or not isinstance(run.get("steps"), list)):
        return False
    for step in run["steps"]:
        if (not isinstance(step, dict) or not isinstance(step.get("status"), str)
                or step["status"] not in _STEP_STATUSES or step["status"] in {"running", "reconciliation-required"}):
            return False
        if "externalAttempt" in step:
            attempt = restore_external_attempt(step["externalAttempt"])
            if attempt is None or attempt["phase"] not in {"completed", "reconciled"}:
                return False
            if attempt["kind"] == "tool" and attempt["phase"] == "reconciled" and not _superseded_tool_fence(step, history):
                return False
    return True


def _restore_step(raw: Any, ids: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    keys = ("nodeId", "sourceId", "operation", "kind", "status", "label", "detail", "inputHash", "configHash", "targetHash")
    if not all(isinstance(raw.get(key), str) for key in keys):
        return None
    if raw["status"] not in _STEP_STATUSES or raw["kind"] not in {"note", "chart", "artifact", "table", "model", "tool", "scientific"}:
        return None
    step = {key: raw[key] for key in keys}
    for key in ("inputHash", "configHash", "targetHash"):
        if step[key] and not DIGEST.fullmatch(step[key]):
            return None
    if raw["kind"] in {"model", "tool"} and "endpointHash" in raw:
        if not isinstance(raw["endpointHash"], str) or not DIGEST.fullmatch(raw["endpointHash"]):
            return None
        step["endpointHash"] = raw["endpointHash"]
    if raw.get("outputHash"):
        if not isinstance(raw["outputHash"], str) or not DIGEST.fullmatch(raw["outputHash"]):
            return None
        step["outputHash"] = raw["outputHash"]
    version = raw.get("outputVersion")
    if type(version) is int and 0 < version <= 2147483647:
        step["outputVersion"] = version
    for key in ("startedAt", "finishedAt"):
        timestamp = _timestamp(raw.get(key))
        if timestamp is not None:
            step[key] = timestamp
    if step["kind"] == "model":
        for key in ("provider", "modelId"):
            identifier = _identifier(raw.get(key))
            if identifier is not None:
                step[key] = identifier
    if step["kind"] == "tool":
        for key in ("serverId", "toolName"):
            value = raw.get(key)
            if isinstance(value, str) and 0 < len(value) <= 256:
                step[key] = value
        if raw.get("errorType") == "tool_error":
            step["errorType"] = "tool_error"
        if "sourceIds" in raw:
            raw_sources = raw["sourceIds"]
            if (not isinstance(raw_sources, list) or len(raw_sources) > 64
                    or any(not isinstance(source, str) or not source for source in raw_sources)
                    or len(set(raw_sources)) != len(raw_sources)):
                return None
            step["sourceIds"] = [ids.get(source, "") for source in raw_sources]
            if any(not source for source in step["sourceIds"]) and step["status"] in {"pending", "current"}:
                step.update(status="blocked", detail="A saved MCP input no longer exists.")
    if step["kind"] == "scientific":
        from graphlink_plugins.scientific.core import canonical_bytes, readable_result, MAX_RESULT_BYTES

        raw_sources = raw.get("sourceIds")
        if (not isinstance(raw_sources, list) or len(raw_sources) > 32
                or any(not isinstance(source, str) or not source for source in raw_sources)
                or len(set(raw_sources)) != len(raw_sources)):
            return None
        step["sourceIds"] = [ids.get(source, "") for source in raw_sources]
        elapsed = raw.get("elapsedMs")
        if type(elapsed) is int and 0 <= elapsed <= 86400000:
            step["elapsedMs"] = elapsed
        error_type = raw.get("errorType")
        if error_type in {"timeout", "cancelled", "invalid_input", "worker_error", "validation", "conflict"}:
            step["errorType"] = error_type
        if "scientificResult" in raw:
            result = raw["scientificResult"]
            try:
                pack = result["provenance"]["packId"]
                if (not isinstance(pack, str) or result["operationId"] != step["operation"]
                        or len(canonical_bytes(result)) > MAX_RESULT_BYTES
                        or not readable_result({"version": 1, "latest": result}, pack)):
                    return None
            except (KeyError, TypeError, AttributeError, ValueError):
                return None
            step["scientificResult"] = result

    for key in ("nodeId", "sourceId"):
        # Never leave an unmapped saved id pointing at an unrelated new n0/n1.
        step[key] = ids.get(step[key], "")
    if step["kind"] == "scientific" and raw["status"] == "running":
        # Isolated pure calculations have no external side effects or replay
        # ambiguity. An unfinished calculation is safe to attempt on resume;
        # the runner rechecks all pins and a previously committed result.
        step.update(status="pending", detail="Interrupted scientific calculation; recheck inputs on resume.")
    if step["kind"] == "scientific" and any(not source for source in step["sourceIds"]):
        step.update(status="blocked", detail="A saved scientific input no longer exists.")
    if "externalAttempt" in raw:
        attempt = restore_external_attempt(raw["externalAttempt"])
        if attempt is None:
            # Do not discard the run: that could make an already-issued remote
            # request look as though it never existed after a corrupt import.
            step.update(status="reconciliation-required", detail="External request history is invalid; do not retry.")
        else:
            # Preserve the *validated* request identity for reconciliation even
            # when the surrounding input or completion receipt is inconsistent.
            step["externalAttempt"] = attempt
            if (attempt["inputHash"] != step["inputHash"]
                    or (attempt["phase"] == "completed" and
                        ((step["status"] != "completed" and not (step["kind"] == "tool" and step["status"] == "failed"
                            and step.get("errorType") == "tool_error")) or not step.get("outputHash")))
                    or (attempt["phase"] == "reconciled" and step["status"] not in {"failed", "cancelled"})):
                step.update(status="reconciliation-required", detail="External request receipt is inconsistent; do not retry.")
            elif attempt["phase"] not in {"completed", "reconciled"}:
                # A persisted 'dispatched' fence does not prove the remote call
                # finished, failed, or even left the host. Never auto-replay it.
                step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)
    elif step["status"] in {"running", "reconciliation-required"}:
        # An in-flight remote step with no durable identity is unsafe to replay.
        step.update(status="reconciliation-required", detail="External request identity is missing; do not retry.")
    if step["status"] == "pending" and not (step["nodeId"] and (step["sourceId"] or step["kind"] in {"scientific", "tool"})):
        step.update(status="blocked", detail="The saved input or output no longer exists.")
    return step


def restore_graph_runs(raw: Any, ids: dict[str, str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in raw[-RUN_HISTORY_LIMIT:]:
        if not isinstance(record, dict):
            continue
        # Archives scrub credential-like "*Token" keys. The preview is a
        # content digest. Normalize records from older local databases too.
        preview_hash = record.get("previewHash", record.get("previewToken"))
        if not isinstance(preview_hash, str) or not DIGEST.fullmatch(preview_hash):
            continue
        keys = ("runId", "selectionHash", "startedAt", "status")
        if not all(isinstance(record.get(key), str) for key in keys):
            continue
        run = {key: record[key] for key in keys}
        run["previewHash"] = preview_hash
        # Legacy runs used sequential scheduling. Missing concurrency metadata
        # defaults to three only for new work remaining after an interruption.
        # Invalid imported limits never authorize unbounded fan-out.
        limit = record.get("maxConcurrency", 3)
        run["maxConcurrency"] = limit if type(limit) is int and 1 <= limit <= 8 else 3
        finished_at = _timestamp(record.get("finishedAt"))
        if finished_at is not None:
            run["finishedAt"] = finished_at
        if (not RUN_ID.fullmatch(run["runId"]) or run["runId"] in seen or run["status"] not in _RUN_STATUSES
                or not DIGEST.fullmatch(run["selectionHash"])
                or not isinstance(record.get("steps"), list)):
            continue
        restored_steps = [_restore_step(step, ids) for step in record["steps"]]
        if any(step is None for step in restored_steps):
            # Dropping a malformed prerequisite would change the approved plan.
            continue
        steps = [step for step in restored_steps if step is not None]
        run["steps"] = steps
        if any(step["status"] == "reconciliation-required" for step in steps):
            run["status"] = "reconciliation-required"
        elif run["status"] == "running":
            run["status"] = "interrupted"
        seen.add(run["runId"])
        runs.append(run)
    return runs
