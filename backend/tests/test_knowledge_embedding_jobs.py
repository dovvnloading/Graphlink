"""Production knowledge embedding jobs: batches, cancellation and real intent wiring.

The providers are deterministic in-memory fakes. No test contacts Ollama or a
cloud endpoint, and every knowledge database is created under tmp_path.
"""

from __future__ import annotations

import asyncio
import threading

from backend import knowledge_embedding_config as config, knowledge_store, native_dialogs
from backend.api import intents_knowledge
from backend.chat_library import (
    _schedule_embeddings_after_graph_save, load_chat_row, save_chat_atomically_row,
)
from backend.domain.graph import SceneDocument
from backend.events import SessionBus
from backend.knowledge_embedding_jobs import KnowledgeEmbeddingJobManager
from backend.knowledge_ingest import ingest_text
from backend.providers.base import ProviderCapabilities
from backend.settings import register_settings
from graphlink_settings_store import SettingsManager


class FakeProvider:
    capabilities = ProviderCapabilities(embedding=True)

    def __init__(self, *, block_call: int = 0, fail: bool = False):
        self.calls: list[tuple[list[str], int]] = []
        self.block_call = block_call
        self.fail = fail
        self.started = threading.Event()
        self.release = threading.Event()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append((list(texts), threading.get_ident()))
        if self.fail:
            raise RuntimeError("private-api-key and document contents must never reach the event stream")
        if len(self.calls) == self.block_call:
            self.started.set()
            if not self.release.wait(5):
                raise RuntimeError("test provider barrier timed out")
        return [[1.0, float(len(text))] for text in texts]


def _prepare(tmp_path, monkeypatch, *, model: str = "model-a"):
    db = tmp_path / "knowledge.db"
    settings = SettingsManager(tmp_path / "session.dat")
    settings.set_knowledge_embedding_settings("ollama", model)
    monkeypatch.setattr(config.api_provider, "ollama_supports_embedding", lambda _model: True)
    return db, settings


def _add(db, workspace: int, count: int, *, prefix: str = "document"):
    collection = knowledge_store.get_or_create_workspace_collection(db, workspace)
    for n in range(count):
        ingest_text(
            f"{prefix} number {n} is useful knowledge.",
            source_uri=f"{prefix}:{workspace}:{n}", title=f"{prefix}-{n}",
            collection_id=collection, db_path=db,
        )
    return collection


async def _terminal(jobs: KnowledgeEmbeddingJobManager, job_id: str) -> dict:
    for _ in range(500):
        row = next(row for row in jobs.snapshot()["jobs"] if row["jobId"] == job_id)
        if row["status"] in {"completed", "cancelled", "failed"}:
            return row
        await asyncio.sleep(0.01)
    raise AssertionError(f"Embedding job {job_id} never completed")


def test_background_batches_32_and_emits_progress_without_cross_workspace_leaks(tmp_path, monkeypatch):
    db, settings = _prepare(tmp_path, monkeypatch)
    collection = _add(db, 7, 35)
    other = _add(db, 8, 1, prefix="other")
    provider = FakeProvider(block_call=2)
    monkeypatch.setattr(config, "OllamaProvider", lambda **_kwargs: provider)

    async def scenario():
        bus = SessionBus("batch-embeddings")
        jobs = KnowledgeEmbeddingJobManager(bus, db, settings)
        bus.register_topic("knowledge-embedding", jobs.snapshot)
        messages: list[dict] = []

        class Recorder:
            async def send_json(self, message):
                messages.append(message)

        bus.attach(Recorder())
        loop_thread = threading.get_ident()
        start = jobs.schedule(7)
        try:
            # Ensure the progress event for the committed 32-row batch has
            # time to publish before releasing the second batch.
            assert await asyncio.to_thread(provider.started.wait, 5)
            await asyncio.sleep(0.04)
        finally:
            provider.release.set()
        finished = await _terminal(jobs, start["jobId"])
        await asyncio.sleep(0.04)
        return finished, messages, loop_thread

    finished, messages, loop_thread = asyncio.run(scenario())
    assert finished["status"] == "completed"
    assert finished["chunksTotal"] == 35
    assert finished["chunksEmbedded"] == 35
    assert finished["chunksRemaining"] == 0
    assert [len(texts) for texts, _ in provider.calls] == [32, 3]
    assert all(thread != loop_thread for _, thread in provider.calls)
    assert len(knowledge_store.list_embeddings_for_search(db, "ollama:model-a", collection_id=collection)) == 35
    assert knowledge_store.list_embeddings_for_search(db, "ollama:model-a", collection_id=other) == []
    states = [
        row
        for message in messages if message.get("topic") == "knowledge-embedding"
        for row in message["payload"]["jobs"]
    ]
    assert any(row["status"] == "completed" and row["chunksEmbedded"] == 35 for row in states)
    assert any(row["chunksEmbedded"] == 32 for row in states)


def test_cancellation_at_batch_boundary_and_resume_only_pending_chunks(tmp_path, monkeypatch):
    db, settings = _prepare(tmp_path, monkeypatch)
    _add(db, 5, 35)
    provider = FakeProvider(block_call=2)
    monkeypatch.setattr(config, "OllamaProvider", lambda **_kwargs: provider)

    async def scenario():
        bus = SessionBus("cancel-embeddings")
        jobs = KnowledgeEmbeddingJobManager(bus, db, settings)
        bus.register_topic("knowledge-embedding", jobs.snapshot)
        first = jobs.schedule(5)
        try:
            assert await asyncio.to_thread(provider.started.wait, 5)
            cancelling = jobs.cancel(first["jobId"])
            assert cancelling["cancelRequested"]
        finally:
            provider.release.set()
        stopped = await _terminal(jobs, first["jobId"])
        resumed = jobs.resume(5)
        finished = await _terminal(jobs, resumed["jobId"])
        return stopped, finished

    stopped, finished = asyncio.run(scenario())
    assert stopped["status"] == "cancelled"
    assert stopped["chunksEmbedded"] == 32
    assert finished["status"] == "completed"
    assert finished["chunksTotal"] == finished["chunksEmbedded"] == 3
    assert [len(texts) for texts, _ in provider.calls] == [32, 3, 3]
    assert len(knowledge_store.list_embeddings_for_search(db, "ollama:model-a")) == 35


def test_model_change_cancels_old_provider_and_embeds_new_space(tmp_path, monkeypatch):
    db, settings = _prepare(tmp_path, monkeypatch)
    _add(db, 9, 1)
    first_provider = FakeProvider(block_call=1)
    next_provider = FakeProvider()
    monkeypatch.setattr(
        config, "OllamaProvider",
        lambda *, model: first_provider if model == "model-a" else next_provider,
    )

    async def scenario():
        bus = SessionBus("model-switch")
        jobs = KnowledgeEmbeddingJobManager(bus, db, settings)
        bus.register_topic("knowledge-embedding", jobs.snapshot)
        first = jobs.schedule(9)
        try:
            assert await asyncio.to_thread(first_provider.started.wait, 5)
            settings.set_knowledge_embedding_settings("ollama", "model-b")
            jobs.refresh(9)
        finally:
            first_provider.release.set()
        old = await _terminal(jobs, first["jobId"])
        for _ in range(500):
            all_rows = jobs.snapshot()["jobs"]
            if len(all_rows) >= 2 and all_rows[-1]["status"] == "completed":
                return old, all_rows[-1]
            await asyncio.sleep(0.01)
        raise AssertionError("New model never ran")

    stopped, finished = asyncio.run(scenario())
    assert stopped["status"] == "cancelled"
    assert stopped["chunksEmbedded"] == 0
    assert finished["modelId"] == "ollama:model-b"
    assert finished["chunksEmbedded"] == 1
    assert knowledge_store.list_embeddings_for_search(db, "ollama:model-a") == []
    assert len(knowledge_store.list_embeddings_for_search(db, "ollama:model-b")) == 1

    # Turning the source Off cannot make any provider call, or reuse stale
    # cloud consent from a previous choice.
    settings.set_knowledge_embedding_settings("off")
    async def off():
        bus = SessionBus("off-model")
        jobs = KnowledgeEmbeddingJobManager(bus, db, settings)
        bus.register_topic("knowledge-embedding", jobs.snapshot)
        assert jobs.refresh(9) == {"status": "off"}
        assert jobs.snapshot()["jobs"] == []
    asyncio.run(off())


def test_failure_is_safe_to_publish_and_retry_can_resume(tmp_path, monkeypatch):
    db, settings = _prepare(tmp_path, monkeypatch)
    _add(db, 12, 1)
    failed_provider = FakeProvider(fail=True)
    good_provider = FakeProvider()
    current = [failed_provider]
    monkeypatch.setattr(config, "OllamaProvider", lambda **_kwargs: current[0])

    async def scenario():
        bus = SessionBus("error-embeddings")
        jobs = KnowledgeEmbeddingJobManager(bus, db, settings)
        bus.register_topic("knowledge-embedding", jobs.snapshot)
        messages = []

        class Recorder:
            async def send_json(self, message):
                messages.append(message)

        bus.attach(Recorder())
        first = jobs.schedule(12)
        failed = await _terminal(jobs, first["jobId"])
        current[0] = good_provider
        second = jobs.resume(12)
        recovered = await _terminal(jobs, second["jobId"])
        await asyncio.sleep(0.04)
        return failed, recovered, messages

    failed, recovered, messages = asyncio.run(scenario())
    assert failed["status"] == "failed"
    assert failed["error"] and "private-api-key" not in failed["error"]
    assert "document contents" not in str(messages)
    assert recovered["status"] == "completed"
    assert recovered["chunksEmbedded"] == 1


def test_settings_change_and_file_ingestion_start_real_intent_jobs(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    manager = SettingsManager(tmp_path / "session.dat")
    monkeypatch.setattr(config.api_provider, "ollama_supports_embedding", lambda _model: True)
    provider = FakeProvider()
    monkeypatch.setattr(config, "OllamaProvider", lambda **_kwargs: provider)
    monkeypatch.setattr(intents_knowledge, "DEFAULT_DB_PATH", db)

    first_file = tmp_path / "first.md"
    second_file = tmp_path / "second.md"
    first_file.write_text("First important research passage.")
    second_file.write_text("Second important research passage.")
    selected = [[str(first_file)], [str(second_file)]]

    async def picker():
        return selected.pop(0)

    monkeypatch.setattr(native_dialogs, "pick_files", picker)

    async def wait_for_ingest(bus, job_id):
        for _ in range(500):
            payload = await bus.dispatch_intent("knowledge", "getIngestJobs", [])
            row = next(r for r in payload["jobs"] if r["jobId"] == job_id)
            if row["status"] in {"completed", "cancelled", "failed"}:
                assert row["status"] == "completed"
                return
            await asyncio.sleep(0.01)
        raise AssertionError("File ingestion never finished")

    async def wait_for_job_count(bus, expected):
        for _ in range(500):
            payload = await bus.dispatch_intent("knowledge", "getEmbeddingJobs", [])
            rows = payload["jobs"]
            if len(rows) >= expected and rows[-1]["status"] in {"completed", "failed"}:
                return rows[-1]
            await asyncio.sleep(0.01)
        raise AssertionError("Automatic embedding was not scheduled")

    async def scenario():
        bus = SessionBus("wiring")
        document = SceneDocument()
        document.current_workspace_id = 22
        intents_knowledge.register_knowledge_intents(bus, document, settings_manager=manager)
        assert (await bus.dispatch_intent("knowledge", "getEmbeddingJobs", [])) == {
            "workspaceId": 22, "jobs": [],
        }
        assert (await bus.publish("knowledge-embedding"))["workspaceId"] == 22
        document.current_workspace_id = 23
        assert (await bus.dispatch_intent("knowledge", "getEmbeddingJobs", []))["workspaceId"] == 23
        document.current_workspace_id = 22
        register_settings(
            bus, manager,
            on_knowledge_embedding_changed=lambda: bus.dispatch_intent("knowledge", "refreshEmbeddings", []),
        )
        first = await bus.dispatch_intent("knowledge", "addFiles", [])
        await wait_for_ingest(bus, first["jobId"])
        assert (await bus.dispatch_intent("knowledge", "getEmbeddingJobs", []))["jobs"] == []

        await bus.dispatch_intent("app-settings", "setKnowledgeEmbedding", ["ollama", "model-a", "", False])
        initial = await wait_for_job_count(bus, 1)
        assert initial["status"] == "completed" and initial["chunksEmbedded"] == 1

        second = await bus.dispatch_intent("knowledge", "addFiles", [])
        await wait_for_ingest(bus, second["jobId"])
        automatic = await wait_for_job_count(bus, 2)
        assert automatic["status"] == "completed"
        assert automatic["chunksEmbedded"] == 1

        # Manual resume is idempotent and exposes the same cache checkpoint.
        resumed = await bus.dispatch_intent("knowledge", "resumeEmbedding", [])
        final = await wait_for_job_count(bus, 3)
        assert resumed["jobId"] == final["jobId"]
        assert final["chunksEmbedded"] == 0

    asyncio.run(scenario())
    collection = knowledge_store.get_or_create_workspace_collection(db, 22)
    assert len(knowledge_store.list_embeddings_for_search(db, "ollama:model-a", collection_id=collection)) == 2
    assert [len(texts) for texts, _ in provider.calls] == [1, 1]

def test_saved_graph_uses_persisted_default_workspace_not_global_pool(tmp_path, monkeypatch):
    db, settings = _prepare(tmp_path, monkeypatch)
    chats_db = tmp_path / "chats.db"
    chat_id, _ = save_chat_atomically_row(
        chats_db, None, "Graph", {"nodes": []}, [], [], knowledge_db_path=db,
    )
    workspace_id = load_chat_row(chats_db, chat_id)["workspace_id"]
    collection = _add(db, workspace_id, 1, prefix="graph")
    ingest_text(
        "A deliberately different global-pool note.",
        source_uri="unscoped.md", title="Unscoped",
        collection_id=0, db_path=db,
    )
    provider = FakeProvider()
    monkeypatch.setattr(config, "OllamaProvider", lambda **_kwargs: provider)

    async def scenario():
        bus = SessionBus("graph-save")
        jobs = KnowledgeEmbeddingJobManager(bus, db, settings)
        bus.register_topic("knowledge-embedding", jobs.snapshot)
        bus.knowledge_embedding_jobs = jobs
        # A new chat originally has no workspace hint; chats.db assigns the
        # Default workspace on INSERT. Scheduling must read the saved row.
        await _schedule_embeddings_after_graph_save(bus, chats_db, chat_id, None, settings)
        [job] = jobs.snapshot()["jobs"]
        return await _terminal(jobs, job["jobId"])

    finished = asyncio.run(scenario())
    assert finished["status"] == "completed"
    assert finished["workspaceId"] == workspace_id
    assert finished["chunksEmbedded"] == 1
    assert len(knowledge_store.list_embeddings_for_search(db, "ollama:model-a", collection_id=collection)) == 1
    assert len(knowledge_store.list_embeddings_for_search(db, "ollama:model-a", collection_id=0)) == 0
