"""App-lifetime, opt-in workspace folder synchronization.

The watcher uses the same KnowledgeIngestManager jobs as manual imports.
No filesystem monitor, persisted path, or WebSocket client can enable a watch:
only an explicit native folder-picker selection can create its DB row.
An app-lifetime task starts a sync on launch and every ten minutes; manual
intents may start one sooner. Each folder has one active job across sessions.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from backend.events import SessionBus
from backend.knowledge_ingest import IngestError
from backend.knowledge_jobs import KnowledgeIngestManager
from backend.knowledge_store import (
    add_watched_folder, list_watched_folders, remove_watched_folder,
)

logger = logging.getLogger(__name__)

WATCH_SYNC_INTERVAL_SECONDS = 10 * 60
_FINAL = {"completed", "cancelled", "failed"}


class WatchedFolderService:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._managers: dict[int, KnowledgeIngestManager] = {}
        self._periodic_task: asyncio.Task | None = None

    def start(self) -> None:
        """Called from the ASGI lifespan, not at module import/app construction."""
        if self._periodic_task is None or self._periodic_task.done():
            self._periodic_task = asyncio.create_task(self._periodic(), name="knowledge-folder-sync")

    async def stop(self) -> None:
        task = self._periodic_task
        self._periodic_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        running = []
        for manager in self._managers.values():
            for job in manager.snapshot()["jobs"]:
                if job["status"] not in _FINAL:
                    manager.cancel(job["jobId"])
            running.extend(manager._tasks.values())
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    async def _periodic(self) -> None:
        while True:
            try:
                await self.sync_all()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("automatic knowledge folder sync could not be scheduled")
            await asyncio.sleep(WATCH_SYNC_INTERVAL_SECONDS)

    def _start_folder(self, folder: dict) -> dict:
        folder_id = folder["id"]
        manager = self._managers.get(folder_id)
        if manager is None:
            bus = SessionBus(f"watched-knowledge-{folder_id}")
            manager = KnowledgeIngestManager(bus, self.db_path)
            bus.register_topic("knowledge-ingest", manager.snapshot)
            self._managers[folder_id] = manager
        jobs = manager.snapshot()["jobs"]
        if jobs and (
            jobs[-1]["status"] not in _FINAL
            or (jobs[-1]["jobId"] in manager._tasks and not manager._tasks[jobs[-1]["jobId"]].done())
        ):
            return {
                "folderId": folder_id, "jobId": jobs[-1]["jobId"],
                "status": jobs[-1]["status"],
            }
        result = manager.start(
            folder["path"], folder=True, workspace_id=folder["workspaceId"],
            watched_folder_id=folder_id,
        )
        return {"folderId": folder_id, **result}

    async def sync_all(self) -> dict:
        """Schedule every configured watch at startup/on the timer, without blocking on ingestion."""
        folders = await asyncio.to_thread(list_watched_folders, self.db_path)
        return {"jobs": [self._start_folder(folder) for folder in folders]}

    async def list(self, workspace_id: int) -> dict:
        folders = await asyncio.to_thread(list_watched_folders, self.db_path, workspace_id)
        for folder in folders:
            manager = self._managers.get(folder["id"])
            jobs = manager.snapshot()["jobs"] if manager is not None else []
            folder["jobId"] = jobs[-1]["jobId"] if jobs else None
            folder["syncStatus"] = jobs[-1]["status"] if jobs else "idle"
        return {"folders": folders}

    async def add(self, workspace_id: int, path: str) -> dict:
        folder = await asyncio.to_thread(add_watched_folder, self.db_path, workspace_id, path)
        return {"folder": folder, "job": self._start_folder(folder)}

    async def remove(self, workspace_id: int, folder_id: int) -> dict:
        folders = await asyncio.to_thread(list_watched_folders, self.db_path, workspace_id)
        if not any(folder["id"] == folder_id for folder in folders):
            raise IngestError("Unknown watched folder in this workspace.")
        manager = self._managers.pop(folder_id, None)
        if manager is not None:
            for job in manager.snapshot()["jobs"]:
                if job["status"] not in _FINAL:
                    manager.cancel(job["jobId"])
        removed = await asyncio.to_thread(remove_watched_folder, self.db_path, workspace_id, folder_id)
        if not removed:
            raise IngestError("Unknown watched folder in this workspace.")
        return {"removed": True, "folderId": folder_id}

    async def sync(self, workspace_id: int, folder_id: int | None = None) -> dict:
        folders = await asyncio.to_thread(list_watched_folders, self.db_path, workspace_id)
        if folder_id is not None:
            folders = [folder for folder in folders if folder["id"] == folder_id]
            if not folders:
                raise IngestError("Unknown watched folder in this workspace.")
        return {"jobs": [self._start_folder(folder) for folder in folders]}
