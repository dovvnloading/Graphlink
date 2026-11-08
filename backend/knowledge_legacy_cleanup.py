"""Explicit, preview-first cleanup of pre-#503 ambiguous branch knowledge rows.

Earlier versions indexed branches under source_uri="branch:n1" etc. Node ids
repeat across saved graphs, so the old rows cannot be reliably attributed
to any particular graph. DO NOT automatically migrate or remove them.
Instead, users can preview the strictly matched legacy records and select
specific document ids for a backed-up, transactional deletion.

Run: python -m backend.knowledge_legacy_cleanup [--db PATH]
     python -m backend.knowledge_legacy_cleanup [--db PATH] --delete-id ID --confirm-delete
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.knowledge_store import DEFAULT_DB_PATH, _connect

_LEGACY_BRANCH_URI = re.compile(r"^branch:(n[0-9]+)$")


def _is_legacy_branch(row: dict[str, Any]) -> bool:
    """Require the exact old URI, title AND mime; never match newer UUIDs."""
    match = _LEGACY_BRANCH_URI.fullmatch(str(row.get("source_uri", "")))
    return bool(
        match
        and row.get("title") == f"Branch (node {match.group(1)})"
        and row.get("mime") == "text/plain"
    )


def _rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, collection_id, source_uri, title, mime, content_hash, added_at "
        "FROM documents ORDER BY id"
    ).fetchall()
    return [
        dict(zip(("id", "collection_id", "source_uri", "title", "mime", "content_hash", "added_at"), row))
        for row in rows
    ]


def preview_legacy_branch_documents(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Read-only preview; returns metadata, never raw snippets or private text."""
    if not db_path.is_file():
        return []
    with contextlib.closing(_connect(db_path)) as conn:
        return [
            {
                "id": row["id"],
                "source_uri": row["source_uri"],
                "title": row["title"],
                "collection_id": row["collection_id"],
                "added_at": row["added_at"],
            }
            for row in _rows(conn)
            if _is_legacy_branch(row)
        ]


def delete_selected_legacy_branches(
    db_path: Path, document_ids: list[int], *, confirm: bool = False,
) -> dict[str, Any]:
    """Back up the full SQLite DB, then transactionally delete ONLY chosen IDs.

    No --all shortcut. The selected row identities are verified before AND
    after the backup, under BEGIN IMMEDIATE for the final deletion. The
    backup is a SQLite online backup (not a WAL-unsafe byte copy), created
    with private POSIX permissions before SQLite opens the destination.
    """
    if not confirm:
        raise ValueError("Deletion requires explicit --confirm-delete.")
    if not document_ids or any(type(i) is not int or i < 1 for i in document_ids):
        raise ValueError("Provide one or more positive integer --delete-id values.")
    if not db_path.is_file():
        raise FileNotFoundError(f"No knowledge database exists at {db_path}.")
    selected = sorted(set(document_ids))
    selected_ids = set(selected)
    backup_path: Path | None = None

    with contextlib.closing(_connect(db_path)) as conn:
        before = {row["id"]: row for row in _rows(conn) if row["id"] in selected_ids}
        if set(before) != selected_ids or not all(_is_legacy_branch(row) for row in before.values()):
            raise ValueError("Every selected document must be a verified legacy branch row.")

        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = db_path.with_name(
            f"{db_path.name}.legacy-branch-backup-{suffix}-{secrets.token_hex(6)}.db"
        )
        # Protect the user's ingested text in the backup from first creation.
        fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            with contextlib.closing(sqlite3.connect(backup_path)) as destination:
                conn.backup(destination)
        except BaseException:
            backup_path.unlink(missing_ok=True)
            raise

        conn.execute("BEGIN IMMEDIATE")
        try:
            after = {row["id"]: row for row in _rows(conn) if row["id"] in selected_ids}
            if before != after or set(after) != selected_ids:
                raise ValueError("Legacy branch records changed during backup; nothing was deleted.")
            if not all(_is_legacy_branch(row) for row in after.values()):
                raise ValueError("One or more selected records are no longer legacy branches.")
            conn.executemany("DELETE FROM documents WHERE id = ?", [(i,) for i in selected])
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return {"deleted_ids": selected, "backup_path": str(backup_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview or safely remove selected pre-#503 ambiguous branch knowledge rows."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Path to knowledge.db")
    parser.add_argument("--delete-id", type=int, action="append", default=[],
                        help="Explicit document ID to delete; repeat for multiple rows")
    parser.add_argument("--confirm-delete", action="store_true",
                        help="Acknowledge deletion of the selected ids after a database backup")
    args = parser.parse_args(argv)
    try:
        if args.delete_id:
            outcome = delete_selected_legacy_branches(args.db, args.delete_id, confirm=args.confirm_delete)
            sys.stdout.write(json.dumps(outcome, indent=2) + "\n")
        else:
            if args.confirm_delete:
                parser.error("--confirm-delete also requires at least one --delete-id")
            sys.stdout.write(json.dumps({"legacy_documents": preview_legacy_branch_documents(args.db)}, indent=2) + "\n")
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(2, f"Legacy knowledge cleanup refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
