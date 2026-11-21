"""Workspace-scoped, cancellable knowledge embedding jobs.

Embedding calls, capability probes and SQLite work all run in a worker thread.
The per-chunk, per-model cache is the resume checkpoint: committed batches are
never sent to the provider again after cancellation, failure or a restart.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from backend.events import SessionBus
from backend.knowledge_embedding_config import embedding_provider_for_choice
from backend.knowledge_embeddings import DEFAULT_BATCH_SIZE, embed_pending_chunks
from backend.knowledge_store import chunks_pending_embedding, get_or_create_workspace_collection

logger = logging.getLogger(__name__)
_FINAL = frozenset({"completed", "cancelled", "failed"})
_SAFE_ERROR = "Knowledge embedding failed. Check the selected provider and model; see graphlink.log."


@dataclass
class KnowledgeEmbeddingJob:
    job_id: str
    workspace_id: int | None
    source: str
    model: str
    status: str = "queued"
    model_id: str = ""
    chunks_total: int = 0
    chunks_embedded: int = 0
    error: str = ""
    choice: dict = field(default_factory=dict, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def change(self, **updates: object) -> None:
        with self.lock:
            for key, value in updates.items():
                setattr(self, key, value)

    def embedded(self, count: int) -> None:
        with self.lock:
            self.chunks_embedded += count

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "jobId": self.job_id,
                "workspaceId": self.workspace_id,
                "source": self.source,
                "model": self.model,
                "modelId": self.model_id,
                "status": self.status,
                "chunksTotal": self.chunks_total,
                "chunksEmbedded": self.chunks_embedded,
                "chunksRemaining": max(0, self.chunks_total - self.chunks_embedded),
                "cancelRequested": self.cancel_event.is_set(),
                "error": self.error,
            }


class KnowledgeEmbeddingJobManager:
    """Serializes embedding runs and coalesces ingestion during an active run.

    Each request pins a workspace, including the legacy collection-0 case.
    A settings change cancels the old choice at the next batch boundary
    before starting the newly selected model. The worker takes a settings
    snapshot, and checks that consent/choice still matches before each call.
    """

    def __init__(self, bus: SessionBus, db_path: Path, settings_manager) -> None:
        self._bus = bus
        self._db_path = db_path
        self._settings_manager = settings_manager
        self._jobs: dict[str, KnowledgeEmbeddingJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._queued: dict[int | None, KnowledgeEmbeddingJob] = {}
        self._active: KnowledgeEmbeddingJob | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_event = 0.0

    def snapshot(self) -> dict:
        return {"jobs": [job.snapshot() for job in self._jobs.values()]}

    def _enqueue(self, workspace_id: int | None) -> KnowledgeEmbeddingJob | None:
        choice = (
            dict(self._settings_manager.get_knowledge_embedding_settings())
            if self._settings_manager is not None else {"source": "off"}
        )
        if choice["source"] == "off":
            return None
        waiting = self._queued.get(workspace_id)
        if waiting is not None:
            if waiting.choice == choice:
                return waiting
            waiting.cancel_event.set()
            waiting.change(status="cancelled")
        job = KnowledgeEmbeddingJob(
            uuid4().hex, workspace_id, choice["source"], choice["model"], choice=choice,
        )
        self._queued[workspace_id] = job
        self._jobs[job.job_id] = job
        # Never evict an active or queued job merely to keep a compact
        # recent-history snapshot.
        for old_id, old in list(self._jobs.items()):
            if len(self._jobs) <= 10:
                break
            if old.snapshot()["status"] in _FINAL:
                del self._jobs[old_id]
        return job

    def schedule(self, workspace_id: int | None) -> dict:
        """Queue a workspace after ingest, coalescing repeated requests."""
        self._loop = asyncio.get_running_loop()
        job = self._enqueue(workspace_id)
        if job is None:
            return {"status": "off"}
        self._pump()
        self._emit(force=True)
        return self._response(workspace_id)

    def refresh(self, workspace_id: int | None) -> dict:
        """Handle a saved embedding choice, including an explicit Off/opt-out."""
        self._loop = asyncio.get_running_loop()
        for waiting in self._queued.values():
            waiting.cancel_event.set()
            waiting.change(status="cancelled")
        self._queued.clear()
        if self._active is not None:
            self._active.cancel_event.set()
        job = self._enqueue(workspace_id)
        if job is None:
            self._emit(force=True)
            return {"status": "off"}
        self._pump()
        self._emit(force=True)
        return self._response(workspace_id)

    def resume(self, workspace_id: int | None) -> dict:
        """Re-query unembedded chunks, even after a failure or a restart."""
        if (
            self._active is not None and self._active.workspace_id == workspace_id
            and not self._active.cancel_event.is_set()
            and self._active.snapshot()["status"] not in _FINAL
        ):
            return self._active.snapshot()
        return self.schedule(workspace_id)

    def cancel(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError("Unknown knowledge embedding job.")
        if job.snapshot()["status"] not in _FINAL:
            if self._active is job:
                job.cancel_event.set()
                waiting = self._queued.pop(job.workspace_id, None)
                if waiting is not None:
                    waiting.cancel_event.set()
                    waiting.change(status="cancelled")
            elif self._queued.get(job.workspace_id) is job:
                del self._queued[job.workspace_id]
                job.cancel_event.set()
                job.change(status="cancelled")
            self._emit(force=True)
        return job.snapshot()

    def cancel_active(self) -> None:
        """Stop future batches if a session is evicted."""
        for waiting in self._queued.values():
            waiting.cancel_event.set()
            waiting.change(status="cancelled")
        self._queued.clear()
        if self._active is not None:
            self._active.cancel_event.set()
        self._emit(force=True)

    def _response(self, workspace_id: int | None) -> dict:
        if (
            self._active is not None and self._active.workspace_id == workspace_id
            and not self._active.cancel_event.is_set()
            and self._active.snapshot()["status"] not in _FINAL
        ):
            return self._active.snapshot()
        waiting = self._queued.get(workspace_id)
        return waiting.snapshot() if waiting is not None else {"status": "off"}

    def _pump(self) -> None:
        if self._active is not None or not self._queued:
            return
        workspace_id = next(iter(self._queued))
        job = self._queued.pop(workspace_id)
        # A settings change in another thread can happen before the queued
        # job begins. Never start a provider with an outdated consent/model.
        if job.cancel_event.is_set() or job.choice != self._settings_manager.get_knowledge_embedding_settings():
            job.change(status="cancelled")
            self._emit(force=True)
            self._pump()
            return
        self._active = job
        task = asyncio.create_task(asyncio.to_thread(self._run, job, job.choice))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda completed, current=job: self._done(current, completed))
        self._emit(force=True)

    def _done(self, job: KnowledgeEmbeddingJob, task: asyncio.Task) -> None:
        self._tasks.pop(job.job_id, None)
        if task.cancelled():
            job.change(status="cancelled")
        elif task.exception() is not None:
            logger.error("knowledge embedding task failed: %s", task.exception())
            job.change(status="failed", error=_SAFE_ERROR)
        if self._active is job:
            self._active = None
        self._emit(force=True)
        self._pump()

    def _emit(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_event < 0.15:
            return
        self._last_event = now
        if self._loop is None:
            return

        def publish() -> None:
            asyncio.create_task(self._bus.publish("knowledge-embedding"))

        try:
            self._loop.call_soon_threadsafe(publish)
        except RuntimeError:
            pass  # session/event loop has already closed

    def _run(self, job: KnowledgeEmbeddingJob, choice: dict) -> None:
        def cancelled() -> bool:
            return (
                job.cancel_event.is_set()
                or self._settings_manager.get_knowledge_embedding_settings() != choice
            )

        try:
            if cancelled():
                job.change(status="cancelled")
                return
            job.change(status="running")
            self._emit(force=True)
            collection_id = (
                get_or_create_workspace_collection(self._db_path, job.workspace_id)
                if job.workspace_id is not None else 0
            )
            with embedding_provider_for_choice(self._settings_manager, choice=choice) as (provider, model_id):
                if provider is None or model_id is None or cancelled():
                    job.change(status="cancelled")
                    return
                # Pin the pending set, so concurrent ingests cannot make the
                # reported total disagree with the batches in this run.
                pending = chunks_pending_embedding(self._db_path, model_id, collection_id=collection_id)
                job.change(model_id=model_id, chunks_total=len(pending))
                self._emit(force=True)

                def on_batch(count: int) -> None:
                    job.embedded(count)
                    # One event per committed batch (32 chunks), even when
                    # the provider is fast enough to outrun throttling.
                    self._emit(force=True)

                embed_pending_chunks(
                    self._db_path, provider, model_id, collection_id=collection_id,
                    batch_size=DEFAULT_BATCH_SIZE, pending_rows=pending,
                    should_cancel=cancelled, on_batch=on_batch,
                )
                job.change(status="cancelled" if cancelled() else "completed")
        except Exception:
            if cancelled():
                job.change(status="cancelled")
            else:
                # SDK exceptions may include API keys, paths or raw document
                # text; expose only a fixed safe message on the WS topic.
                logger.exception("knowledge embedding job %s failed", job.job_id)
                job.change(status="failed", error=_SAFE_ERROR)
        finally:
            self._emit(force=True)
