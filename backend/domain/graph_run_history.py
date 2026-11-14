"""Saved Graph Runner records. Only the node references change on restore."""

from __future__ import annotations

import re
from typing import Any

RUN_HISTORY_LIMIT = 12
RUN_ID = re.compile(r"[a-zA-Z0-9_-]{8,100}")
DIGEST = re.compile(r"[a-f0-9]{64}")
_RUN_STATUSES = {"completed", "partial", "running", "interrupted"}
_STEP_STATUSES = {"pending", "current", "completed", "blocked", "failed"}


def _restore_step(raw: Any, ids: dict[str, str]) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    keys = ("nodeId", "sourceId", "operation", "kind", "status", "label", "detail", "inputHash", "configHash", "targetHash")
    if not all(isinstance(raw.get(key), str) for key in keys):
        return None
    if raw["status"] not in _STEP_STATUSES or raw["kind"] not in {"note", "chart", "artifact", "table"}:
        return None
    step = {key: raw[key] for key in keys}
    for key in ("inputHash", "configHash", "targetHash"):
        if step[key] and not DIGEST.fullmatch(step[key]):
            return None
    if raw.get("outputHash"):
        if not isinstance(raw["outputHash"], str) or not DIGEST.fullmatch(raw["outputHash"]):
            return None
        step["outputHash"] = raw["outputHash"]
    for key in ("nodeId", "sourceId"):
        # Never leave an unmapped saved id pointing at an unrelated new n0/n1.
        step[key] = ids.get(step[key], "")
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
        keys = ("runId", "previewToken", "selectionHash", "startedAt", "status")
        if not all(isinstance(record.get(key), str) for key in keys):
            continue
        run = {key: record[key] for key in keys}
        if (not RUN_ID.fullmatch(run["runId"]) or run["runId"] in seen or run["status"] not in _RUN_STATUSES
                or not DIGEST.fullmatch(run["previewToken"]) or not DIGEST.fullmatch(run["selectionHash"])
                or not isinstance(record.get("steps"), list)):
            continue
        steps = [_restore_step(step, ids) for step in record["steps"]]
        if any(step is None for step in steps):
            # Dropping a malformed prerequisite would change the approved plan.
            continue
        run["steps"] = steps
        if run["status"] == "running":
            run["status"] = "interrupted"
        seen.add(run["runId"])
        runs.append(run)
    return runs
