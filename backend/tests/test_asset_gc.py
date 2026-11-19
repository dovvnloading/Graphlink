"""Offline binary-asset cleanup regressions for issue #535 (tmp DBs only)."""

from __future__ import annotations

import json
import os
import sqlite3
import time

import pytest

from backend.asset_gc import collect_unreferenced_assets
from backend.asset_store import store_for
from backend.db_backup import backup_filename, backups_dir_for, take_backup
from backend.instance_lock import DataDirectoryInUse, exclusive_data_directory


def _db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE graphs (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
    return path


def _add_graph(db_path, ref):
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO graphs (data) VALUES (?)",
            (json.dumps({"nodes": [{"node_type": "image", "asset_ref": ref}]}),),
        )


def _age(path):
    old = time.time() - 20 * 86400
    os.utime(path, (old, old))


def test_shared_blob_is_retained_until_last_live_reference_is_deleted(tmp_path):
    db = _db(tmp_path / "chats.db")
    store = store_for(db)
    ref = store.put(b"test-image-content")
    target = store._path_for(ref)
    _age(target)
    _add_graph(db, ref)
    _add_graph(db, ref)

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM graphs WHERE id = 1")
    assert collect_unreferenced_assets(db, apply=True).deleted_assets == 0
    assert target.exists()

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM graphs")
    dry = collect_unreferenced_assets(db)
    assert dry.dry_run and dry.candidate_assets == 1 and dry.deleted_assets == 0
    assert target.exists()

    cleaned = collect_unreferenced_assets(db, apply=True)
    assert cleaned.deleted_assets == 1
    assert not target.exists()


def test_retained_backup_protects_reference_after_graph_deletion(tmp_path):
    db = _db(tmp_path / "chats.db")
    store = store_for(db)
    ref = store.put(b"recoverable-image")
    target = store._path_for(ref)
    _age(target)
    _add_graph(db, ref)
    take_backup(db)
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM graphs")

    report = collect_unreferenced_assets(db, apply=True, min_age_days=0)
    assert report.deleted_assets == 0
    assert target.exists()


def test_grace_period_protects_recent_orphans(tmp_path):
    db = _db(tmp_path / "chats.db")
    store = store_for(db)
    ref = store.put(b"recent-orphan")
    target = store._path_for(ref)

    assert collect_unreferenced_assets(db, apply=True).deleted_assets == 0
    assert target.exists()
    assert collect_unreferenced_assets(db, min_age_days=0).candidate_assets == 1


def test_invalid_retained_backup_blocks_sweep_entirely(tmp_path):
    db = _db(tmp_path / "chats.db")
    store = store_for(db)
    ref = store.put(b"orphan-after-bad-backup")
    target = store._path_for(ref)
    _age(target)
    backup_dir = backups_dir_for(db)
    backup_dir.mkdir(parents=True)
    (backup_dir / backup_filename("20250101T120000Z")).write_bytes(b"invalid database")

    with pytest.raises(RuntimeError, match="retained backup is invalid"):
        collect_unreferenced_assets(db, apply=True)
    assert target.exists()


def test_active_graphlink_instance_prevents_asset_cleanup(tmp_path):
    db = _db(tmp_path / "chats.db")
    with exclusive_data_directory(db):
        with pytest.raises(DataDirectoryInUse):
            collect_unreferenced_assets(db, apply=True)


def test_externalized_plugin_state_protects_nested_table_assets_from_gc(tmp_path):
    db = _db(tmp_path / "chats.db")
    store = store_for(db)
    table_ref = store.put(b"full typed table payload")
    state_ref = store.put(json.dumps({"latest": {"tableAsset": {
        "asset_ref": table_ref, "mime_type": "application/json",
    }}}).encode("utf-8"))
    for ref in (table_ref, state_ref):
        _age(store._path_for(ref))
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO graphs (data) VALUES (?)", (json.dumps({
            "nodes": [{"node_type": "science_statistics.workbench", "plugin_state_asset": {
                "asset_ref": state_ref, "mime_type": "application/json",
            }}],
        }),))
    report = collect_unreferenced_assets(db, apply=True, min_age_days=0)
    assert report.deleted_assets == 0
    assert report.referenced_assets == 2
    assert store.verify(table_ref) and store.verify(state_ref)

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM graphs")
    assert collect_unreferenced_assets(db, apply=True, min_age_days=0).deleted_assets == 2


def test_asset_gc_fails_closed_when_externalized_plugin_state_is_missing(tmp_path):
    db = _db(tmp_path / "chats.db")
    store = store_for(db)
    table_ref = store.put(b"dependent typed table")
    state_ref = store.put(json.dumps({"latest": {
        "tableAsset": {"asset_ref": table_ref},
    }}).encode("utf-8"))
    _age(store._path_for(table_ref))
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO graphs (data) VALUES (?)", (json.dumps({
            "nodes": [{"node_type": "science_statistics.workbench", "plugin_state_asset": {
                "asset_ref": state_ref, "mime_type": "application/json",
            }}],
        }),))
    store._path_for(state_ref).unlink()
    with pytest.raises(RuntimeError, match="plugin state is unavailable"):
        collect_unreferenced_assets(db, apply=True, min_age_days=0)
    assert store.verify(table_ref)
