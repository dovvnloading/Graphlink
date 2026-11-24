"""Global, versioned Workflow Library in chats.db.

All writes use the caller's transaction and the library's own immutable
revision IDs. Deletion is archival so pinned versions remain resolvable.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from backend.secret_scrub import scrub
from backend.workflow.library import canonical, identifier, validate_document


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_library (
            workflow_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0, 1))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_library_versions (
            workflow_id TEXT NOT NULL REFERENCES workflow_library(workflow_id),
            revision TEXT NOT NULL,
            version INTEGER NOT NULL CHECK(version > 0),
            created_at TEXT NOT NULL,
            document_json TEXT NOT NULL,
            PRIMARY KEY (workflow_id, revision),
            UNIQUE (workflow_id, version)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_child_runs (
            graph_id INTEGER NOT NULL,
            run_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            child_run_id TEXT NOT NULL,
            receipt_json TEXT NOT NULL,
            PRIMARY KEY (graph_id, run_id, step_index, child_run_id),
            FOREIGN KEY (graph_id, run_id, step_index)
                REFERENCES workflow_steps(graph_id, run_id, step_index) ON DELETE CASCADE
        )
    """)


def _name(name):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200 or scrub(name) != name:
        raise ValueError("Choose a nonempty workflow name without credentials or private paths (maximum 200 characters).")
    return name.strip()


def list_library(conn, query=""):
    rows = conn.execute("""
        SELECT l.workflow_id, l.name, v.revision, v.version, v.created_at
        FROM workflow_library l JOIN workflow_library_versions v USING(workflow_id)
        WHERE l.deleted = 0 AND v.version = (
            SELECT MAX(version) FROM workflow_library_versions WHERE workflow_id = l.workflow_id
        )
        ORDER BY l.name COLLATE NOCASE, l.workflow_id
    """)
    return [{"workflow_id": wid, "name": name, "revision": rev, "version": version, "created_at": created}
            for wid, name, rev, version, created in rows if query.casefold() in name.casefold()]


def history(conn, workflow_id):
    return [{"revision": rev, "version": version, "created_at": created}
            for rev, version, created in conn.execute(
                "SELECT revision, version, created_at FROM workflow_library_versions WHERE workflow_id = ? ORDER BY version DESC",
                (workflow_id,),
            )]


def get(conn, workflow_id, revision):
    row = conn.execute("SELECT document_json FROM workflow_library_versions WHERE workflow_id = ? AND revision = ?",
                       (workflow_id, revision)).fetchone()
    if row is None:
        raise ValueError("This pinned workflow version is not in the library.")
    return validate_document(json.loads(row[0]))


def save(conn, document, *, name, workflow_id=None, expected_revision=None):
    """New library entry or compare-and-append version. Never overwrite a spec."""
    name = _name(name)
    document = validate_document(document)
    if workflow_id is None:
        workflow_id = identifier()
        version = 1
        conn.execute("INSERT INTO workflow_library (workflow_id, name) VALUES (?, ?)", (workflow_id, name))
    else:
        # Take the writer lock before checking the current revision. A stale
        # editor must not silently append on top of another editor's changes.
        result = conn.execute("UPDATE workflow_library SET name = name WHERE workflow_id = ? AND deleted = 0",
                              (workflow_id,))
        if result.rowcount != 1:
            raise ValueError("Workflow no longer exists in the library.")
        previous = history(conn, workflow_id)[0]
        if expected_revision != previous["revision"]:
            raise ValueError("A newer workflow version exists. Refresh before saving a new version.")
        version = previous["version"] + 1
        conn.execute("UPDATE workflow_library SET name = ? WHERE workflow_id = ?", (name, workflow_id))
    revision = identifier()
    document["spec"].update(workflow_id=workflow_id, revision=revision, name=name)
    document = validate_document(document)
    conn.execute("INSERT INTO workflow_library_versions VALUES (?, ?, ?, ?, ?)",
                 (workflow_id, revision, version, datetime.now(timezone.utc).isoformat(), canonical(document)))
    return {"workflow_id": workflow_id, "revision": revision, "version": version}


def rename(conn, workflow_id, name):
    result = conn.execute("UPDATE workflow_library SET name = ? WHERE workflow_id = ? AND deleted = 0",
                          (_name(name), workflow_id))
    if result.rowcount != 1:
        raise ValueError("Workflow no longer exists in the library.")


def duplicate(conn, workflow_id, revision, name):
    return save(conn, get(conn, workflow_id, revision), name=name)


def delete(conn, workflow_id):
    conn.execute("UPDATE workflow_library SET deleted = 1 WHERE workflow_id = ?", (workflow_id,))
