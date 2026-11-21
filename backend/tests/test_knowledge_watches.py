"""Workspace watched folders: persistence, sync lifecycle, safety and intent scoping."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from backend import knowledge_jobs, knowledge_store, knowledge_watches, native_dialogs
from backend.api import intents_knowledge
from backend.knowledge_ingest import IngestError
from backend.knowledge_watches import WatchedFolderService
from backend.tests.test_canvas import make_bus


async def _finished(service: WatchedFolderService, workspace_id: int, folder_id: int, job_id: str) -> dict:
    for _ in range(400):
        rows = (await service.list(workspace_id))["folders"]
        row = next(row for row in rows if row["id"] == folder_id)
        if row["jobId"] == job_id and row["syncStatus"] in {"completed", "failed", "cancelled"}:
            manager = service._managers.get(folder_id)
            task = manager._tasks.get(job_id) if manager else None
            # A job publishes final status immediately before its finally
            # writes lastSyncAt/lastError. Wait for that write, too.
            if task is None or task.done():
                return (await service.list(workspace_id))["folders"][
                    next(i for i, item in enumerate(rows) if item["id"] == folder_id)
                ]
        await asyncio.sleep(0.02)
    pytest.fail("watched folder ingestion did not settle")


def test_watched_folders_are_opt_in_persisted_and_workspace_scoped(tmp_path):
    db = tmp_path / "knowledge.db"
    folder = tmp_path / "notes"
    folder.mkdir()
    first = knowledge_store.add_watched_folder(db, 17, str(folder))
    assert first["path"] == str(folder.resolve())
    assert first["lastSyncAt"] is None and first["lastError"] == ""
    assert knowledge_store.add_watched_folder(db, 17, str(folder))["id"] == first["id"]
    other = knowledge_store.add_watched_folder(db, 18, str(folder))
    assert first["id"] != other["id"]
    assert [row["id"] for row in knowledge_store.list_watched_folders(db, 17)] == [first["id"]]
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == knowledge_store.KNOWLEDGE_DB_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM watched_folders").fetchone()[0] == 2
    assert not knowledge_store.remove_watched_folder(db, 18, first["id"])
    assert knowledge_store.remove_watched_folder(db, 17, first["id"])
    assert knowledge_store.list_watched_folders(db, 17) == []
    with pytest.raises((FileNotFoundError, OSError)):
        knowledge_store.add_watched_folder(db, 17, str(folder / "missing"))


def test_watch_sync_add_update_remove_and_errors_preserve_the_old_index(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    folder = tmp_path / "notes"
    folder.mkdir()
    original = folder / "original.md"
    original.write_text("orchidoldphrase", encoding="utf-8")

    async def scenario():
        service = WatchedFolderService(db)
        first = await service.add(11, str(folder))
        folder_id = first["folder"]["id"]
        job_id = first["job"]["jobId"]
        try:
            row = await _finished(service, 11, folder_id, job_id)
            assert row["syncStatus"] == "completed"
            assert row["lastSyncAt"] and not row["lastError"]
            collection = knowledge_store.get_or_create_workspace_collection(db, 11)
            assert len(knowledge_store.search_chunks(db, "orchidoldphrase", collection_id=collection)) == 1

            # A different workspace has an independent corpus and subscription.
            assert (await service.list(12))["folders"] == []
            incoming = folder / "new.txt"
            incoming.write_text("newrosephrase", encoding="utf-8")
            original.write_text("violetupdatedphrase", encoding="utf-8")
            job_id = (await service.sync(11, folder_id))["jobs"][0]["jobId"]
            await _finished(service, 11, folder_id, job_id)
            assert len(knowledge_store.search_chunks(db, "violetupdatedphrase", collection_id=collection)) == 1
            assert len(knowledge_store.search_chunks(db, "newrosephrase", collection_id=collection)) == 1
            assert knowledge_store.search_chunks(db, "orchidoldphrase", collection_id=collection) == []

            # Metadata-identical files need not be extracted or re-chunked.
            job_id = (await service.sync(11, folder_id))["jobs"][0]["jobId"]
            await _finished(service, 11, folder_id, job_id)
            stats = service._managers[folder_id].snapshot()["jobs"][-1]
            assert stats["unchanged"] == 2

            incoming.unlink()
            job_id = (await service.sync(11, folder_id))["jobs"][0]["jobId"]
            await _finished(service, 11, folder_id, job_id)
            assert service._managers[folder_id].snapshot()["jobs"][-1]["removed"] == 1
            assert knowledge_store.search_chunks(db, "newrosephrase", collection_id=collection) == []

            original.write_text("temporarilyunreadablephrase", encoding="utf-8")
            def cannot_read(path):
                raise IngestError("simulated extraction error")
            monkeypatch.setattr(knowledge_jobs, "extract_text", cannot_read)
            job_id = (await service.sync(11, folder_id))["jobs"][0]["jobId"]
            row = await _finished(service, 11, folder_id, job_id)
            assert "simulated extraction error" in row["lastError"]
            assert len(knowledge_store.search_chunks(db, "violetupdatedphrase", collection_id=collection)) == 1
            monkeypatch.undo()

            job_id = (await service.sync(11, folder_id))["jobs"][0]["jobId"]
            row = await _finished(service, 11, folder_id, job_id)
            assert row["lastError"] == ""
            assert len(knowledge_store.search_chunks(db, "temporarilyunreadablephrase", collection_id=collection)) == 1
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_overlapping_watches_never_delete_other_watcher_files(tmp_path):
    db = tmp_path / "knowledge.db"
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    file = nested / "only.txt"
    file.write_text("overlapcactusphrase")

    async def scenario():
        service = WatchedFolderService(db)
        try:
            outer = await service.add(3, str(root))
            inner = await service.add(3, str(nested))
            await _finished(service, 3, outer["folder"]["id"], outer["job"]["jobId"])
            await _finished(service, 3, inner["folder"]["id"], inner["job"]["jobId"])
            collection = knowledge_store.get_or_create_workspace_collection(db, 3)
            assert len(knowledge_store.search_chunks(db, "overlapcactusphrase", collection_id=collection)) == 1

            file.unlink()
            job = (await service.sync(3, outer["folder"]["id"]))["jobs"][0]
            await _finished(service, 3, outer["folder"]["id"], job["jobId"])
            assert len(knowledge_store.search_chunks(db, "overlapcactusphrase", collection_id=collection)) == 1

            job = (await service.sync(3, inner["folder"]["id"]))["jobs"][0]
            await _finished(service, 3, inner["folder"]["id"], job["jobId"])
            assert knowledge_store.search_chunks(db, "overlapcactusphrase", collection_id=collection) == []
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_missing_folder_does_not_prune_and_remove_only_disables_watch(tmp_path):
    db = tmp_path / "knowledge.db"
    folder = tmp_path / "watch"
    folder.mkdir()
    (folder / "note.md").write_text("persistedelmaphrase")

    async def scenario():
        service = WatchedFolderService(db)
        try:
            result = await service.add(7, str(folder))
            folder_id = result["folder"]["id"]
            row = await _finished(service, 7, folder_id, result["job"]["jobId"])
            last_sync = row["lastSyncAt"]
            collection = knowledge_store.get_or_create_workspace_collection(db, 7)
            moved = tmp_path / "moved"
            folder.rename(moved)
            job = (await service.sync(7, folder_id))["jobs"][0]
            row = await _finished(service, 7, folder_id, job["jobId"])
            assert row["syncStatus"] == "failed"
            assert row["lastSyncAt"] == last_sync and row["lastError"]
            assert len(knowledge_store.search_chunks(db, "persistedelmaphrase", collection_id=collection)) == 1
            with pytest.raises(IngestError, match="Unknown watched folder"):
                await service.remove(8, folder_id)
            assert (await service.remove(7, folder_id))["removed"]
            assert (await service.list(7))["folders"] == []
            assert len(knowledge_store.search_chunks(db, "persistedelmaphrase", collection_id=collection)) == 1
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_startup_and_periodic_reuse_jobs_without_user_initiation(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    folder = tmp_path / "watch"
    folder.mkdir()
    file = folder / "note.md"
    file.write_text("periodicfirstphrase")
    watch = knowledge_store.add_watched_folder(db, 4, str(folder))
    monkeypatch.setattr(knowledge_watches, "WATCH_SYNC_INTERVAL_SECONDS", 0.08)

    async def scenario():
        service = WatchedFolderService(db)
        service.start()  # ASGI lifespan startup: no manual sync call
        try:
            for _ in range(400):
                manager = service._managers.get(watch["id"])
                jobs = manager.snapshot()["jobs"] if manager else []
                if jobs and jobs[-1]["status"] == "completed":
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("startup did not schedule watched-folder job")
            collection = knowledge_store.get_or_create_workspace_collection(db, 4)
            assert len(knowledge_store.search_chunks(db, "periodicfirstphrase", collection_id=collection)) == 1
            file.write_text("periodicsecondphrase")
            for _ in range(400):
                jobs = service._managers[watch["id"]].snapshot()["jobs"]
                if (len(jobs) >= 2 and jobs[-1]["status"] == "completed"
                        and knowledge_store.search_chunks(db, "periodicsecondphrase", collection_id=collection)):
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("periodic interval did not re-index a changed file")
            assert knowledge_store.search_chunks(db, "periodicfirstphrase", collection_id=collection) == []
        finally:
            await service.stop()

    asyncio.run(scenario())


def test_watched_intents_require_a_workspace_and_a_native_picker(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    folder = tmp_path / "watch"
    folder.mkdir()
    (folder / "a.txt").write_text("intentwatchedphrase")
    monkeypatch.setattr(intents_knowledge, "DEFAULT_DB_PATH", db)

    async def scenario():
        bus, document, _ = make_bus()
        with pytest.raises(IngestError, match="Select a workspace"):
            await bus.dispatch_intent("knowledge", "listWatchedFolders", [])
        document.current_workspace_id = 91

        async def picker():
            document.current_workspace_id = 92
            return str(folder)

        monkeypatch.setattr(native_dialogs, "pick_folder", picker)
        added = await bus.dispatch_intent("knowledge", "addWatchedFolder", [])
        folder_id = added["folder"]["id"]
        document.current_workspace_id = 91
        for _ in range(400):
            folder_row = (await bus.dispatch_intent("knowledge", "listWatchedFolders", []))["folders"][0]
            if folder_row["syncStatus"] == "completed" and folder_row["lastSyncAt"]:
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("watched intent import did not finish")
        assert folder_row["workspaceId"] == 91
        assert (await bus.dispatch_intent("knowledge", "syncWatchedFolders", [folder_id]))["jobs"]
        document.current_workspace_id = 92
        assert (await bus.dispatch_intent("knowledge", "listWatchedFolders", []))["folders"] == []
        with pytest.raises(IngestError, match="Unknown watched folder"):
            await bus.dispatch_intent("knowledge", "removeWatchedFolder", [folder_id])
        document.current_workspace_id = 91
        assert (await bus.dispatch_intent("knowledge", "removeWatchedFolder", [folder_id]))["removed"]

    asyncio.run(scenario())
