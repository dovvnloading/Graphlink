"""Saved Graph Runner records. Only the node references change on restore."""

from __future__ import annotations

import re
from typing import Any

from backend.domain.graph_run_external import RECONCILIATION_DETAIL, restore_external_attempt
from backend.domain.graph_run_inspector import _identifier, _timestamp

RUN_HISTORY_LIMIT = 12
RUN_ID = re.compile(r"[a-zA-Z0-9_-]{8,100}")
DIGEST = re.compile(r"[a-f0-9]{64}")
_RUN_STATUSES = {"completed", "partial", "running", "interrupted", "reconciliation-required"}
_STEP_STATUSES = {"pending", "current", "completed", "blocked", "failed", "running", "reconciliation-required"}


def _restore_step(raw: Any, ids: dict[str, str]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    keys = ("nodeId", "sourceId", "operation", "kind", "status", "label", "detail", "inputHash", "configHash", "targetHash")
    if not all(isinstance(raw.get(key), str) for key in keys):
        return None
    if raw["status"] not in _STEP_STATUSES or raw["kind"] not in {"note", "chart", "artifact", "table", "model", "tool"}:
        return None
    step = {key: raw[key] for key in keys}
    for key in ("inputHash", "configHash", "targetHash"):
        if step[key] and not DIGEST.fullmatch(step[key]):
            return None
    if raw["kind"] == "model" and "endpointHash" in raw:
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
    for key in ("nodeId", "sourceId"):
        # Never leave an unmapped saved id pointing at an unrelated new n0/n1.
        step[key] = ids.get(step[key], "")
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
                        (step["status"] != "completed" or not step.get("outputHash")))):
                step.update(status="reconciliation-required", detail="External request receipt is inconsistent; do not retry.")
            elif attempt["phase"] != "completed":
                # A persisted 'dispatched' fence does not prove the remote call
                # finished, failed, or even left the host. Never auto-replay it.
                step.update(status="reconciliation-required", detail=RECONCILIATION_DETAIL)
    elif step["status"] in {"running", "reconciliation-required"}:
        # An in-flight remote step with no durable identity is unsafe to replay.
        step.update(status="reconciliation-required", detail="External request identity is missing; do not retry.")
    if step["status"] == "pending" and not (step["sourceId"] and step["nodeId"]):
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
