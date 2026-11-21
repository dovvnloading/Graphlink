"""Knowledge Library workspace boundaries, document actions and browser drops."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from backend import knowledge_store
from backend.api import intents_knowledge
from backend.knowledge_chunking import chunk_text
from backend.knowledge_ingest import IngestError
from backend.tests.test_canvas import make_bus


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "knowledge.db"
    monkeypatch.setattr(intents_knowledge, "DEFAULT_DB_PATH", path)
    return path


def _stored_file(db: Path, path: Path, workspace_id: int):
    collection_id = knowledge_store.get_or_create_workspace_collection(db, workspace_id)
    value = path.read_text(encoding="utf-8")
    return knowledge_store.upsert_file_with_chunks(
        db, source_uri=str(path.resolve()), title=path.name,
        mime="text/markdown", text=value, chunks=chunk_text(value),
        collection_id=collection_id, size_bytes=path.stat().st_size,
    )


async def _finish(bus, job_id):
    for _ in range(500):
        jobs = await bus.dispatch_intent("knowledge", "getIngestJobs", [])
        job = next(item for item in jobs["jobs"] if item["jobId"] == job_id)
        if job["status"] in {"completed", "cancelled", "failed"}:
            return job
        await asyncio.sleep(0.02)
    pytest.fail("Knowledge import did not finish")


def test_list_has_real_metadata_and_respects_workspace(db, tmp_path):
    first = tmp_path / "notes.md"
    first.write_text("knowledge about blue flowers", encoding="utf-8")
    second = tmp_path / "private.md"
    second.write_text("knowledge about red flowers", encoding="utf-8")
    a = _stored_file(db, first, 13)
    b = _stored_file(db, second, 14)
    assert a.document_id != b.document_id

    bus, document, _ = make_bus()
    document.current_workspace_id = 13
    response = asyncio.run(bus.dispatch_intent("knowledge", "listDocuments", []))
    [row] = response["documents"]
    assert row["documentId"] == a.document_id
    assert row["title"] == "notes.md" and row["type"] == "text/markdown"
    assert row["sizeBytes"] == first.stat().st_size
    assert row["chunks"] == 1
    assert row["addedAt"] and row["status"] == "indexed"
    assert row["canReindex"] is True


def test_open_and_remove_cannot_cross_workspace(db, tmp_path):
    alpha = tmp_path / "alpha.md"
    alpha.write_text("secret alpha content", encoding="utf-8")
    beta = tmp_path / "beta.md"
    beta.write_text("secret beta content", encoding="utf-8")
    a = _stored_file(db, alpha, 13)
    b = _stored_file(db, beta, 14)

    async def scenario():
        bus, document, recorder = make_bus()
        document.current_workspace_id = 13
        with pytest.raises(IngestError, match="current workspace"):
            await bus.dispatch_intent("knowledge", "openDocument", [b.document_id])
        assert (await bus.dispatch_intent("knowledge", "removeDocument", [b.document_id])) == {"removed": False}
        assert recorder.topics_seen().count("knowledge-library") == 0
        opened = await bus.dispatch_intent("knowledge", "openDocument", [a.document_id])
        assert opened["title"] == "alpha.md"
        assert opened["content"] == "secret alpha content"
        assert (await bus.dispatch_intent("knowledge", "removeDocument", [a.document_id])) == {"removed": True}
        assert recorder.topics_seen().count("knowledge-library") == 1

    asyncio.run(scenario())
    assert knowledge_store.get_document(db, a.document_id) is None
    assert knowledge_store.get_document(db, b.document_id) is not None


def test_reindex_rereads_an_existing_mapped_source(db, tmp_path):
    path = tmp_path / "reindex.md"
    path.write_text("original knowledge", encoding="utf-8")
    _stored_file(db, path, 7)
    path.write_text("revised knowledge", encoding="utf-8")

    async def scenario():
        bus, document, recorder = make_bus()
        document.current_workspace_id = 7
        rows = (await bus.dispatch_intent("knowledge", "listDocuments", []))["documents"]
        result = await bus.dispatch_intent("knowledge", "reindexDocument", [rows[0]["documentId"]])
        final = await _finish(bus, result["jobId"])
        assert final["status"] == "completed" and final["updated"] == 1
        [new] = (await bus.dispatch_intent("knowledge", "listDocuments", []))["documents"]
        assert new["sizeBytes"] == path.stat().st_size
        opened = await bus.dispatch_intent("knowledge", "openDocument", [new["documentId"]])
        assert "revised knowledge" in opened["content"]
        # The import runs off-loop; the completed-job notification is
        # scheduled back onto the loop after the database write commits.
        for _ in range(100):
            if "knowledge-library" in recorder.topics_seen():
                break
            await asyncio.sleep(0.01)
        assert recorder.topics_seen().count("knowledge-library") == 1

    asyncio.run(scenario())


def test_chunked_drag_drop_upload_can_index_reindex_and_clean_private_copy(db):
    body = b"safe drag and drop source"

    async def scenario():
        bus, document, _ = make_bus()
        document.current_workspace_id = 33
        response = await bus.dispatch_intent("knowledge", "beginUpload", [[
            {"name": "dropped.md", "size": len(body)},
        ]])
        upload_id = response["uploadId"]
        # Even if the user switches workspaces while bytes are transferring,
        # the upload keeps the originally selected collection.
        document.current_workspace_id = 34
        half = len(body) // 2
        for part in (body[:half], body[half:]):
            ack = await bus.dispatch_intent("knowledge", "appendUpload", [
                upload_id, 0, base64.b64encode(part).decode("ascii"),
            ])
        assert ack["bytesReceived"] == len(body)
        started = await bus.dispatch_intent("knowledge", "finishUpload", [upload_id])
        assert (await _finish(bus, started["jobId"]))["status"] == "completed"
        document.current_workspace_id = 33
        [row] = (await bus.dispatch_intent("knowledge", "listDocuments", []))["documents"]
        assert row["title"] == "dropped.md"
        assert row["sizeBytes"] == len(body)
        collection = knowledge_store.get_or_create_workspace_collection(db, 33)
        [source] = knowledge_store.file_sources_for_document(
            db, collection_id=collection, document_id=row["documentId"],
        )
        assert Path(source).is_file()
        removed = await bus.dispatch_intent("knowledge", "removeDocument", [row["documentId"]])
        assert removed["removed"] is True
        assert not Path(source).exists()
        document.current_workspace_id = 34
        assert (await bus.dispatch_intent("knowledge", "listDocuments", []))["documents"] == []

    asyncio.run(scenario())


def test_upload_rejects_untrusted_paths_oversize_and_partial_transfer(db):
    async def scenario():
        bus, document, _ = make_bus()
        for name in ("../outside.md", "subdir/file.md", ".env", "script.exe"):
            with pytest.raises(IngestError):
                await bus.dispatch_intent("knowledge", "beginUpload", [[{"name": name, "size": 2}]])
        with pytest.raises(IngestError, match="25 MiB"):
            await bus.dispatch_intent("knowledge", "beginUpload", [[{
                "name": "large.md", "size": 25 * 1024 * 1024 + 1,
            }]])
        response = await bus.dispatch_intent("knowledge", "beginUpload", [[
            {"name": "safe.md", "size": 12},
        ]])
        with pytest.raises(IngestError, match="incomplete"):
            await bus.dispatch_intent("knowledge", "finishUpload", [response["uploadId"]])
        assert (await bus.dispatch_intent("knowledge", "cancelUpload", [response["uploadId"]]))["cancelled"]
        assert not (db.parent / "upload-staging" / response["uploadId"]).exists()

    asyncio.run(scenario())
