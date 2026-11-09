"""Offline, conservative maintenance of unreferenced Graphlink binary assets.

Issue #535: content-addressed images outlive deleted graphs and failed saves.
Never delete on normal graph mutation: old backups, shared refs, in-memory
undo and unfinished saves all make immediate unlink unsafe. This maintenance
command exclusively locks the data directory (refusing a running backend),
marks references in both the live graphs DB and *every* retained backup, then
deletes only old, unreferenced, correctly-shaped blob files when --apply is
explicitly requested. Dry-run is the default.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from backend.asset_store import _CONTENT_REF_RE, assets_dir_for
from backend.db_backup import _valid_backup, list_backups
from backend.instance_lock import exclusive_data_directory
from backend.workspace_archive import _collect_asset_refs

logger = logging.getLogger(__name__)

DEFAULT_MIN_AGE_DAYS = 7.0
_CHAT_TABLES = ("graphs", "chats")


@dataclass(frozen=True)
class AssetGcReport:
    referenced_assets: int
    scanned_assets: int
    candidate_assets: int
    candidate_bytes: int
    deleted_assets: int
    deleted_bytes: int
    dry_run: bool


def _read_references(database: Path, *, immutable: bool) -> set[str]:
    """Read all graph payloads or fail closed; unknown/corrupt rows are unsafe."""
    uri = database.resolve().as_uri() + ("?mode=ro&immutable=1" if immutable else "?mode=ro")
    refs: set[str] = set()
    with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=10)) as conn:
        if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError(f"Graphlink database has failed its integrity check: {database}")
        tables = {str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        table = next((name for name in _CHAT_TABLES if name in tables), None)
        if table is None:
            raise RuntimeError(f"Graphlink database has no recognized chat table: {database}")
        for (payload,) in conn.execute(f"SELECT data FROM {table}"):
            try:
                parsed = json.loads(payload)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Graphlink database has an unreadable graph payload: {database}"
                ) from exc
            _collect_asset_refs(parsed, refs)
    return refs


def _candidate_files(root: Path, live_refs: set[str], cutoff: float) -> list[tuple[Path, os.stat_result]]:
    """Return old unreferenced regular SHA blobs, never symlinks or unknown files."""
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("Graphlink asset directory is not a safe regular directory.")
    candidates: list[tuple[Path, os.stat_result]] = []
    for shard in root.iterdir():
        if shard.is_symlink():
            raise RuntimeError("Graphlink asset directory contains a symlinked shard.")
        if not shard.is_dir():
            continue
        for target in shard.iterdir():
            if target.is_symlink():
                continue
            if not _CONTENT_REF_RE.fullmatch(target.name) or shard.name != target.name[:2]:
                continue
            try:
                info = target.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if target.name not in live_refs and info.st_mtime < cutoff:
                candidates.append((target, info))
    return candidates


def collect_unreferenced_assets(
    db_path: Path, *, apply: bool = False, min_age_days: float = DEFAULT_MIN_AGE_DAYS,
) -> AssetGcReport:
    """Report or remove old orphans only with exclusive database-directory ownership.

    This is intentionally offline; running backend processes own in-memory
    assets and can save references after a DB scan. The existing instance
    lock prevents this maintenance operation from racing such a process.
    """
    db_path = Path(db_path).expanduser().resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f"Graphlink chat database is missing: {db_path}")
    if not 0 <= min_age_days <= 36500:
        raise ValueError("min_age_days must be between zero and 36500")
    with exclusive_data_directory(db_path):
        refs = _read_references(db_path, immutable=False)
        for backup in list_backups(db_path):
            if not _valid_backup(backup, required_table=_CHAT_TABLES):
                raise RuntimeError(
                    f"Cannot safely clean assets: a retained backup is invalid ({backup}). "
                    "Repair or quarantine backups before attempting deletion."
                )
            refs.update(_read_references(backup, immutable=True))

        root = assets_dir_for(db_path)
        cutoff = time.time() - (min_age_days * 86400)
        candidates = _candidate_files(root, refs, cutoff)
        deleted_assets = 0
        deleted_bytes = 0
        for target, previous in candidates:
            if not apply:
                continue
            # Recheck just before unlink. Never follow an unexpected symlink
            # or delete a file swapped in after candidate enumeration.
            try:
                current = target.lstat()
            except FileNotFoundError:
                continue
            if (current.st_ino != previous.st_ino
                    or current.st_size != previous.st_size
                    or current.st_mtime_ns != previous.st_mtime_ns
                    or not stat.S_ISREG(current.st_mode)
                    or current.st_mtime >= cutoff):
                continue
            target.unlink()
            deleted_assets += 1
            deleted_bytes += current.st_size

        scanned_assets = len(refs)
        if root.exists():
            scanned_assets += sum(
                1 for _shard in root.iterdir() if _shard.is_dir() and not _shard.is_symlink()
                for item in _shard.iterdir()
                if item.is_file() and not item.is_symlink()
                and _CONTENT_REF_RE.fullmatch(item.name) and _shard.name == item.name[:2]
                and item.name not in refs
            )
        return AssetGcReport(
            referenced_assets=len(refs),
            scanned_assets=scanned_assets,
            candidate_assets=len(candidates),
            candidate_bytes=sum(info.st_size for _, info in candidates),
            deleted_assets=deleted_assets,
            deleted_bytes=deleted_bytes,
            dry_run=not apply,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline Graphlink asset maintenance (default: dry run). Close Graphlink first."
    )
    parser.add_argument("--db", type=Path, default=Path.home() / ".graphlink" / "chats.db")
    parser.add_argument("--apply", action="store_true", help="Delete eligible old orphans after safety checks")
    parser.add_argument(
        "--min-age-days", type=float, default=DEFAULT_MIN_AGE_DAYS,
        help="Only consider assets this many days old (default: 7)",
    )
    args = parser.parse_args(argv)
    try:
        report = collect_unreferenced_assets(
            args.db, apply=args.apply, min_age_days=args.min_age_days,
        )
    except (OSError, sqlite3.DatabaseError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"Asset cleanup refused: {exc}\n")
    print(
        f"Referenced refs: {report.referenced_assets}; scanned blobs: {report.scanned_assets}; "
        f"eligible orphans: {report.candidate_assets} ({report.candidate_bytes} bytes); "
        f"deleted: {report.deleted_assets} ({report.deleted_bytes} bytes). "
        + ("Dry run; pass --apply to remove." if report.dry_run else "Cleanup completed.")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
