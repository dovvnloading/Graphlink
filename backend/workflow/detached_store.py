"""Workspace-scoped typed run journal in the existing chats database.

Legacy Graph Runner receipts deliberately remain untouched. The new journal
has no graph FK: a detached run is not an autosaved graph.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .artifacts import canonical

TABLE = "detached_workflow_runs"
MAX_RUNS = 50
MAX_AGE_DAYS = 90


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS detached_workflow_runs (
            run_id TEXT PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS detached_workflow_workspace
        ON detached_workflow_runs(workspace_id, created_at DESC, run_id DESC)
    """)


def prune(conn: sqlite3.Connection, *, now: datetime | None = None) -> None:
    # Active runs are never evicted. Run on admission/finish and offline GC.
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=MAX_AGE_DAYS)).isoformat()
    conn.execute("""
        DELETE FROM detached_workflow_runs
        WHERE status NOT IN ('pending', 'running') AND (
            created_at < ? OR run_id IN (
                SELECT run_id FROM (
                    SELECT run_id, ROW_NUMBER() OVER (
                        PARTITION BY workspace_id ORDER BY created_at DESC, run_id DESC
                    ) AS position FROM detached_workflow_runs
                ) WHERE position > ?
            )
        )
    """, (cutoff, MAX_RUNS))


class DetachedRunStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def _connection(self):
        # Import lazily: chat_library owns the schema migration chain.
        from backend.chat_library import _connect
        with closing(_connect(self.db_path)) as conn, conn:
            yield conn

    def create(self, workspace_id: int, record: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO detached_workflow_runs(run_id, workspace_id, created_at, status, payload)
                VALUES (?, ?, ?, ?, ?)
            """, (record["run_id"], workspace_id, record["created_at"], record["status"], canonical(record)))
            prune(conn)

    def checkpoint(self, workspace_id: int, record: dict[str, Any]) -> None:
        with self._connection() as conn:
            result = conn.execute("""
                UPDATE detached_workflow_runs SET status = ?, payload = ?
                WHERE workspace_id = ? AND run_id = ? AND status IN ('pending', 'running')
            """, (record["status"], canonical(record), workspace_id, record["run_id"]))
            if result.rowcount != 1:
                raise RuntimeError("Workflow run is missing or already finished.")
            if record["status"] not in ("pending", "running"):
                prune(conn)

    def read(self, workspace_id: int, run_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            prune(conn)
            row = conn.execute("""
                SELECT payload FROM detached_workflow_runs WHERE workspace_id = ? AND run_id = ?
            """, (workspace_id, run_id)).fetchone()
            return json.loads(row[0]) if row else None

    def list(self, workspace_id: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            prune(conn)
            return [json.loads(payload) for (payload,) in conn.execute("""
                SELECT payload FROM detached_workflow_runs WHERE workspace_id = ?
                ORDER BY created_at DESC, run_id DESC
            """, (workspace_id,))]

    def interrupt_unfinished(self) -> None:
        """Call once at backend startup, before accepting any runs; never replay effects."""
        with self._connection() as conn:
            for (payload,) in conn.execute("""
                SELECT payload FROM detached_workflow_runs WHERE status IN ('pending', 'running')
            """).fetchall():
                run = json.loads(payload)
                now = utc_now()
                reason = "The backend stopped before the workflow finished."
                for step in run["steps"].values():
                    if step["status"] in ("pending", "running"):
                        step.update(status="interrupted", finished_at=now, error=reason)
                        if step.get("approval", {}).get("status") == "pending":
                            step["approval"]["status"] = "interrupted"
                        for child in step.get("map_items", {}).values():
                            if child["status"] in ("pending", "running"):
                                child.update(status="interrupted", error=reason)
                                for nested in child["steps"].values():
                                    if nested["status"] in ("pending", "running"):
                                        nested.update(status="interrupted", finished_at=now, error=reason)
                        run["events"].append({
                            "schema_version": 1, "run_id": run["run_id"], "sequence": len(run["events"]),
                            "timestamp": now, "kind": "step-interrupted", "step_id": step["step_id"],
                            "attempt": 1, "message": reason, "data": None,
                        })
                run.update(status="interrupted", finished_at=now, error=reason)
                run["events"].append({
                    "schema_version": 1, "run_id": run["run_id"], "sequence": len(run["events"]),
                    "timestamp": now, "kind": "run-interrupted", "step_id": None,
                    "attempt": None, "message": reason, "data": None,
                })
                conn.execute("UPDATE detached_workflow_runs SET status = ?, payload = ? WHERE run_id = ?",
                             ("interrupted", canonical(run), run["run_id"]))
            prune(conn)
