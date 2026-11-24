"""Durable execution journal in chats.db.

The graph JSON retains the approved plan and its compatibility snapshot. Run
and step receipts and the append-only event sequence are committed on the
same connection as graph output, notes and pins. No prompt, result body,
credentials, approval arguments or free-form error text belongs here.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from backend.domain.graph_run_external import restore_external_attempt
from backend.domain.graph_run_history import RUN_ID
from backend.domain.graph_run_inspector import _digest, _duration_ms, _identifier, _node_id, _timestamp


_RUN_STATUSES = {"running", "interrupted", "completed", "partial", "cancelled", "reconciliation-required"}
_STEP_STATUSES = {"pending", "running", "current", "completed", "blocked", "failed", "cancelled",
                  "reconciliation-required"}
_KINDS = {"note", "chart", "artifact", "table", "model", "tool", "scientific", "workflow"}
_APPROVALS = {"pending", "reviewed", "auto", "cached", "approved", "denied", "expired", "cancelled"}
_APPROVAL_ID = re.compile(r"mcp-[a-f0-9]{32}")


def migrate(conn: sqlite3.Connection) -> None:
    """Migration 6. The migration runner owns the enclosing transaction."""
    # Do not use executescript: sqlite3 would implicitly commit the migration.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_runs (
            graph_id INTEGER NOT NULL REFERENCES graphs(id) ON DELETE CASCADE,
            run_id TEXT NOT NULL,
            preview_hash TEXT NOT NULL,
            selection_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_ms INTEGER,
            max_concurrency INTEGER NOT NULL,
            PRIMARY KEY (graph_id, run_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_steps (
            graph_id INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            kind TEXT NOT NULL,
            operation TEXT NOT NULL,
            input_ref TEXT NOT NULL,
            output_ref TEXT,
            status TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            duration_ms INTEGER,
            provider TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            total_tokens INTEGER,
            cost_usd REAL,
            approval TEXT,
            approval_id TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            request_id TEXT,
            attempt_phase TEXT,
            PRIMARY KEY (graph_id, run_id, step_index),
            FOREIGN KEY (graph_id, run_id) REFERENCES workflow_runs(graph_id, run_id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            graph_id INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            step_index INTEGER,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            request_id TEXT,
            attempt_phase TEXT,
            approval TEXT,
            approval_id TEXT,
            retry_count INTEGER,
            FOREIGN KEY (graph_id, run_id) REFERENCES workflow_runs(graph_id, run_id) ON DELETE CASCADE
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS workflow_events_run ON workflow_events(graph_id, run_id, event_id)")
    # Existing snapshots are already durable. Seed their latest known state,
    # not a fabricated historical timeline; malformed legacy plans are left
    # to the existing fail-closed graph restore validation.
    for graph_id, data in conn.execute("SELECT id, data FROM graphs"):
        try:
            payload = json.loads(data)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            checkpoint(conn, graph_id, payload.get("graph_runs"))


def _integer(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 2147483647 else None


def _cost(value: Any) -> float | None:
    if type(value) in (float, int) and 0 <= value <= 1_000_000 and math.isfinite(value):
        return float(value)
    return None


def _enum(value: Any, allowed: set[str]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _step_record(step: dict[str, Any]) -> dict[str, Any]:
    attempt = restore_external_attempt(step.get("externalAttempt")) or {}
    start, end = _timestamp(step.get("startedAt")), _timestamp(step.get("finishedAt"))
    source_ids = step.get("sourceIds")
    if not isinstance(source_ids, list):
        source_ids = [step.get("sourceId")]
    input_ref = {
        "nodeIds": [_node_id(source) for source in source_ids[:64]],
        "sha256": _digest(step.get("inputHash")),
        "configuration": _digest(step.get("configHash")),
    }
    output_hash = _digest(step.get("outputHash"))
    output_ref = {"nodeId": _node_id(step.get("nodeId")), "sha256": output_hash,
                  "version": _integer(step.get("outputVersion"))}
    input_tokens, output_tokens = _integer(step.get("inputTokens")), _integer(step.get("outputTokens"))
    total = input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None
    approval_id = step.get("approvalId")
    return {
        "kind": step["kind"], "operation": _identifier(step.get("operation")) or "unknown",
        "input_ref": _json(input_ref), "output_ref": _json(output_ref) if output_hash else None,
        "status": step["status"], "started_at": start, "finished_at": end,
        "duration_ms": _integer(step.get("elapsedMs")) if _integer(step.get("elapsedMs")) is not None
        else _duration_ms(start, end),
        "provider": _identifier(attempt.get("provider")) or _identifier(step.get("provider")),
        "model": _identifier(attempt.get("model")) or _identifier(step.get("modelId")),
        "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total,
        "cost_usd": _cost(step.get("costUsd")),
        "approval": _enum(step.get("approval"), _APPROVALS),
        "approval_id": approval_id if isinstance(approval_id, str) and _APPROVAL_ID.fullmatch(approval_id) else None,
        "retry_count": _integer(step.get("retryCount")) or 0,
        "request_id": attempt.get("requestId"), "attempt_phase": attempt.get("phase"),
    }


def _valid_run(run: Any) -> bool:
    return (
        isinstance(run, dict) and isinstance(run.get("runId"), str) and bool(RUN_ID.fullmatch(run["runId"]))
        and _digest(run.get("previewHash", run.get("previewToken"))) is not None
        and _digest(run.get("selectionHash")) is not None and _timestamp(run.get("startedAt")) is not None
        and _enum(run.get("status"), _RUN_STATUSES) is not None and isinstance(run.get("steps"), list)
        and all(isinstance(s, dict) and _enum(s.get("kind"), _KINDS) is not None
                and _enum(s.get("status"), _STEP_STATUSES) is not None for s in run["steps"])
    )


def _event(conn: sqlite3.Connection, graph_id: int, run_id: str, index: int | None,
           event_type: str, record: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO workflow_events (graph_id, run_id, step_index, event_type, status, recorded_at, "
        "request_id, attempt_phase, approval, approval_id, retry_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (graph_id, run_id, index, event_type, record["status"], datetime.now(timezone.utc).isoformat(),
         record.get("request_id"), record.get("attempt_phase"), record.get("approval"),
         record.get("approval_id"), record.get("retry_count")),
    )


def _write(conn: sqlite3.Connection, table: str, key: dict[str, Any], record: dict[str, Any]) -> bool:
    # All identifiers are module-owned literals, never input. Values are bound.
    columns = list(record)
    where = " AND ".join(f"{name} = ?" for name in key)
    previous = conn.execute(
        f"SELECT {', '.join(columns)} FROM {table} WHERE {where}", tuple(key.values()),
    ).fetchone()
    if previous is not None:
        old = dict(zip(columns, previous))
        if table == "workflow_runs" and any(old[k] != record[k] for k in
                                            ("preview_hash", "selection_hash", "started_at")):
            raise ValueError("This operation ID already belongs to a durable run.")
        if table == "workflow_steps":
            if old["kind"] != record["kind"] or old["operation"] != record["operation"]:
                raise ValueError("A durable step cannot be replaced with another operation.")
            if old["status"] in {"completed", "current", "failed", "cancelled", "reconciliation-required"} and record["status"] in {"pending", "running"}:
                raise ValueError("A finished or uncertain workflow step cannot be restarted.")
            if old["request_id"] and (old["request_id"] != record["request_id"]
                    or (old["attempt_phase"] in {"completed", "reconciled"}
                        and old["attempt_phase"] != record["attempt_phase"])
                    or (old["attempt_phase"] == "reconciliation-required" and record["attempt_phase"] == "dispatched")):
                raise ValueError("An existing external request fence cannot be replaced or replayed.")
        if old == record:
            return False
        # A reminted node ID changes a reference, not execution history.
        # Keep the current reference but do not append a duplicate state event.
        event_changed = table != "workflow_steps" or any(
            old[name] != record[name] for name in columns if name not in {"input_ref", "output_ref"}
        )
        conn.execute(
            f"UPDATE {table} SET {', '.join(name + ' = ?' for name in columns)} WHERE {where}",
            (*record.values(), *key.values()),
        )
        return event_changed
    else:
        values = {**key, **record}
        conn.execute(
            f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
            tuple(values.values()),
        )
    return True


def checkpoint(conn: sqlite3.Connection, graph_id: int, runs: Any) -> None:
    """Write changed receipts and ordered events within the caller's transaction.

    Never delete durable history when the canvas's bounded plan list prunes
    an entry. The graph FK removes it only when its owning graph is deleted.
    """
    if not isinstance(runs, list):
        runs = []
    seen = set()
    for run in runs:
        if not _valid_run(run) or run["runId"] in seen:
            continue
        run_id = run["runId"]
        seen.add(run_id)
        key = {"graph_id": graph_id, "run_id": run_id}
        start, end = run["startedAt"], _timestamp(run.get("finishedAt"))
        concurrency = run.get("maxConcurrency", 3)
        record = {
            "preview_hash": run.get("previewHash", run.get("previewToken")),
            "selection_hash": run["selectionHash"], "status": run["status"],
            "started_at": start, "finished_at": end, "duration_ms": _duration_ms(start, end),
            "max_concurrency": concurrency if type(concurrency) is int and 1 <= concurrency <= 8 else 3,
        }
        previous_count = conn.execute(
            "SELECT count(*) FROM workflow_steps WHERE graph_id = ? AND run_id = ?", (graph_id, run_id),
        ).fetchone()[0]
        if previous_count and previous_count != len(run["steps"]):
            raise ValueError("A durable run cannot change its approved step count.")
        changed = _write(conn, "workflow_runs", key, record)
        # Initial admission precedes step events; terminal run status follows
        # its receipts. The sequence reflects commits, not wall-clock order.
        if changed and record["status"] == "running":
            _event(conn, graph_id, run_id, None, "run_state", record)
        for index, step in enumerate(run["steps"]):
            step_record = _step_record(step)
            if _write(conn, "workflow_steps", {**key, "step_index": index}, step_record):
                _event(conn, graph_id, run_id, index, "step_state", step_record)
            if step["kind"] == "workflow":
                from backend.workflow.child_runs import validate_child
                child = validate_child(step.get("childRun"))
                if child is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO workflow_child_runs VALUES (?, ?, ?, ?, ?)",
                        (graph_id, run_id, index, child["runId"], _json(child)),
                    )
        if changed and record["status"] != "running":
            _event(conn, graph_id, run_id, None, "run_state", record)
    # A stale or incomplete graph write must not make an unresolved fence
    # disappear from the plan consumed by the existing resume guards.
    unresolved = conn.execute(
        "SELECT DISTINCT run_id FROM workflow_steps WHERE graph_id = ? AND "
        "(status = 'reconciliation-required' OR attempt_phase IN ('dispatched', 'reconciliation-required'))",
        (graph_id,),
    )
    if any(run_id not in seen for (run_id,) in unresolved):
        raise ValueError("Reconcile the unresolved workflow request before removing its plan.")


def overlay_receipts(conn: sqlite3.Connection, graph_id: int, runs: Any) -> None:
    """Overlay committed execution state on matching saved plan snapshots.

    Node IDs still come from the graph plan (session restore remints them).
    Missing legacy rows fall back to the existing snapshot. A mismatched
    request identity is uncertain, never permission to issue another call.
    The regular restore validator handles dispatched fences and interrupted
    pure calculations after this overlay.
    """
    if not isinstance(runs, list):
        return
    for run in runs:
        if not _valid_run(run):
            continue
        key = (graph_id, run["runId"])
        receipt = conn.execute(
            "SELECT preview_hash, selection_hash, started_at, status, finished_at "
            "FROM workflow_runs WHERE graph_id = ? AND run_id = ?", key,
        ).fetchone()
        if receipt is None:
            continue
        if receipt[:3] != (run.get("previewHash", run.get("previewToken")), run["selectionHash"], run["startedAt"]):
            # Do not let a reused/mismatched operation ID authorize execution.
            run["status"] = "reconciliation-required"
            for step in run["steps"]:
                if step["status"] in {"pending", "running"}:
                    step["status"] = "reconciliation-required"
            continue
        run["status"] = receipt[3]
        if receipt[4] is not None:
            run["finishedAt"] = receipt[4]
        else:
            run.pop("finishedAt", None)
        rows = conn.execute(
            "SELECT step_index, status, started_at, finished_at, input_ref, output_ref, "
            "request_id, attempt_phase, approval, approval_id, retry_count, input_tokens, output_tokens, cost_usd, kind, operation "
            "FROM workflow_steps WHERE graph_id = ? AND run_id = ? ORDER BY step_index", key,
        ).fetchall()
        if len(rows) != len(run["steps"]) or any(
            row[0] != index or row[-2:] != (step["kind"], _identifier(step.get("operation")) or "unknown")
            for index, (row, step) in enumerate(zip(rows, run["steps"]))
        ):
            run["status"] = "reconciliation-required"
            for step in run["steps"]:
                if step["status"] in {"pending", "running"}:
                    step["status"] = "reconciliation-required"
            continue
        for row in rows:
            (index, status, start, end, input_ref, output_ref, request_id, phase, approval, approval_id,
             retries, in_tokens, out_tokens, cost, _kind, _operation) = row
            step = run["steps"][index]
            attempt = restore_external_attempt(step.get("externalAttempt"))
            if request_id and (attempt is None or attempt["requestId"] != request_id
                               or attempt["inputHash"] != json.loads(input_ref)["sha256"]):
                step["status"] = "reconciliation-required"
                continue
            step["status"] = status
            if request_id and attempt is not None:
                attempt["phase"] = phase
                step["externalAttempt"] = attempt
            fingerprint = json.loads(input_ref)["sha256"]
            if fingerprint:
                step["inputHash"] = fingerprint
            if output_ref:
                ref = json.loads(output_ref)
                step["outputHash"] = ref["sha256"]
                if ref["version"] is not None:
                    step["outputVersion"] = ref["version"]
            for name, value in (("startedAt", start), ("finishedAt", end), ("approval", approval),
                                ("approvalId", approval_id), ("inputTokens", in_tokens),
                                ("outputTokens", out_tokens), ("costUsd", cost)):
                if value is not None:
                    step[name] = value
                else:
                    step.pop(name, None)
            if retries:
                step["retryCount"] = retries


def read_run(conn: sqlite3.Connection, graph_id: int, run_id: str) -> dict[str, Any] | None:
    """Read a durable run, including pruned plans' receipts, in commit order."""
    def rows(sql: str) -> list[dict[str, Any]]:
        cursor = conn.execute(sql, (graph_id, run_id))
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row)) for row in cursor]

    runs = rows("SELECT * FROM workflow_runs WHERE graph_id = ? AND run_id = ?")
    if not runs:
        return None
    result = runs[0]
    result["steps"] = rows("SELECT * FROM workflow_steps WHERE graph_id = ? AND run_id = ? ORDER BY step_index")
    result["events"] = rows("SELECT * FROM workflow_events WHERE graph_id = ? AND run_id = ? ORDER BY event_id")
    return result
