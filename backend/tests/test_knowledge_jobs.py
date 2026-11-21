"""Batch knowledge imports: boundaries, dedup/upsert, jobs and websocket intents."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from types import SimpleNamespace
from pathlib import Path
from zipfile import ZipFile

import pytest

from backend import knowledge_jobs, knowledge_store, native_dialogs
from backend.attachments import StagedAttachment
from backend.domain.graph import SceneDocument
from backend.harness.workspace import WorkspaceError
from backend.api import intents_knowledge
from backend.events import SessionBus
from backend.knowledge_chunking import chunk_text
from backend.knowledge_ingest import IngestError, extract_text
from backend.knowledge_jobs import KnowledgeIngestJob, KnowledgeIngestManager, _collect_files
from backend.tests.test_canvas import make_bus


def _import(db_path: Path, file: Path, collection_id: int = 0):
    text, mime = extract_text(file)
    return knowledge_store.upsert_file_with_chunks(
        db_path, source_uri=str(file.resolve()), title=file.name,
        mime=mime, text=text, chunks=chunk_text(text), collection_id=collection_id,
    )


async def _completed(manager: KnowledgeIngestManager, job_id: str) -> dict:
    for _ in range(500):
        row = next(row for row in manager.snapshot()["jobs"] if row["jobId"] == job_id)
        if row["status"] in {"completed", "cancelled", "failed"}:
            return row
        await asyncio.sleep(0.02)
    pytest.fail("knowledge import did not terminate")


def test_file_source_aliases_and_replacements_are_atomic(tmp_path):
    db = tmp_path / "knowledge.db"
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("shared oldorchidphrase", encoding="utf-8")
    second.write_text("shared oldorchidphrase", encoding="utf-8")
    a = _import(db, first)
    b = _import(db, second)
    assert (a.action, b.action) == ("added", "duplicate")
    assert a.document_id == b.document_id
    assert _import(db, first).action == "unchanged"
    assert len(knowledge_store.list_documents(db)) == 1

    first.write_text("changed newcactusphrase", encoding="utf-8")
    updated = _import(db, first)
    assert updated.action == "updated"
    assert len(knowledge_store.list_documents(db)) == 2
    old = knowledge_store.get_document(db, a.document_id)
    assert old is not None and old["source_uri"] == str(second)
    assert len(knowledge_store.search_chunks(db, "oldorchidphrase")) == 1
    assert len(knowledge_store.search_chunks(db, "newcactusphrase")) == 1

    second.write_text("changed newcactusphrase", encoding="utf-8")
    assert _import(db, second).action == "updated"
    assert len(knowledge_store.list_documents(db)) == 1
    assert knowledge_store.search_chunks(db, "oldorchidphrase") == []
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM file_sources").fetchone()[0] == 2


def test_changes_are_scoped_to_workspace_collection(tmp_path):
    db = tmp_path / "knowledge.db"
    file = tmp_path / "notes.md"
    file.write_text("workspace alpha", encoding="utf-8")
    a = _import(db, file, collection_id=4)
    b = _import(db, file, collection_id=5)
    assert a.document_id != b.document_id
    file.write_text("workspace beta", encoding="utf-8")
    assert _import(db, file, collection_id=4).action == "updated"
    assert len(knowledge_store.search_chunks(db, "alpha", collection_id=5)) == 1
    assert knowledge_store.search_chunks(db, "alpha", collection_id=4) == []


def test_xlsx_imports_shared_strings_inline_strings_and_numbers(tmp_path):
    file = tmp_path / "sheet.xlsx"
    namespace = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with ZipFile(file, "w") as archive:
        archive.writestr(
            "xl/sharedStrings.xml",
            f"<sst {namespace}><si><t>header</t></si><si><t>shared value</t></si></sst>",
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet {namespace}><sheetData><row r="1">'
            '<c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>inline</t></is></c>'
            '<c r="C1"><v>42</v></c></row><row r="2">'
            '<c r="A2" t="s"><v>1</v></c></row></sheetData></worksheet>',
        )
    text, mime = extract_text(file)
    assert "header\tinline\t42" in text
    assert "shared value" in text
    assert "spreadsheetml.sheet" in mime
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip file")
    with pytest.raises(IngestError, match="invalid XLSX"):
        extract_text(bad)


def test_folder_scan_skips_unwanted_dirs_and_escaping_symlinks(tmp_path):
    root = tmp_path / "chosen"
    root.mkdir()
    (root / "visible.md").write_text("real content")
    for name in (".hidden", ".git", "node_modules", "venv", ".venv", "virtualenv", "__pycache__"):
        nested = root / name
        nested.mkdir()
        (nested / "secret.txt").write_text("do not index")
    (root / "ignore.bin").write_bytes(b"\x00\x01")
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    try:
        (root / "external.md").symlink_to(outside)
        (root / "hidden_link").symlink_to(root / ".git", target_is_directory=True)
    except (OSError, NotImplementedError):
        pass  # Windows CI may lack unprivileged symlink creation.
    job = KnowledgeIngestJob("id", "folder", 11)
    files = _collect_files(root, True, job)
    assert [file.name for file, _ in files] == ["visible.md"]
    assert job.snapshot()["filesTotal"] == 1


def test_limits_preflight_before_any_database_write(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "first.md").write_text("12345678")
    (root / "second.txt").write_text("different")
    job = KnowledgeIngestJob("id", "folder", None)
    monkeypatch.setattr(knowledge_jobs, "MAX_JOB_FILES", 1)
    with pytest.raises(IngestError, match="10,000-file"):
        _collect_files(root, True, job)
    monkeypatch.setattr(knowledge_jobs, "MAX_JOB_FILES", 10_000)
    monkeypatch.setattr(knowledge_jobs, "MAX_JOB_BYTES", 4)
    with pytest.raises(IngestError, match="500 MiB"):
        _collect_files(root, True, job)

    big = root / "huge.md"
    with big.open("wb") as handle:
        handle.truncate(knowledge_jobs.MAX_FILE_BYTES + 1)
    monkeypatch.setattr(knowledge_jobs, "MAX_JOB_BYTES", knowledge_jobs.MAX_JOB_BYTES * 1024)
    one = _collect_files(big, False, KnowledgeIngestJob("oversize", "file", None))
    assert one == []


def test_background_job_emits_progress_and_writes_only_to_knowledge_db(tmp_path):
    file = tmp_path / "source.txt"
    file.write_text("safe standalone knowledge document")
    original_bytes = file.read_bytes()
    original_mtime = file.stat().st_mtime_ns
    db = tmp_path / "private" / "knowledge.db"

    async def scenario():
        bus = SessionBus("job-test")
        manager = KnowledgeIngestManager(bus, db)
        bus.register_topic("knowledge-ingest", manager.snapshot)
        messages = []

        class Recorder:
            async def send_json(self, message):
                messages.append(message)

        bus.attach(Recorder())
        started = manager.start(str(file), folder=False, workspace_id=42)
        final = await _completed(manager, started["jobId"])
        await asyncio.sleep(0.04)
        assert final["status"] == "completed"
        assert final["added"] == 1 and final["filesProcessed"] == 1
        assert any(message["topic"] == "knowledge-ingest" for message in messages)
        return final

    asyncio.run(scenario())
    collection = knowledge_store.get_or_create_workspace_collection(db, 42)
    assert len(knowledge_store.list_documents(db, collection_id=collection)) == 1
    assert file.read_bytes() == original_bytes
    assert file.stat().st_mtime_ns == original_mtime


def test_cancel_while_extracting_prevents_any_document_write(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    file = tmp_path / "slow.md"
    file.write_text("slow text")
    begun, release = threading.Event(), threading.Event()
    real_extract = knowledge_jobs.extract_text

    def blocked(path):
        begun.set()
        if not release.wait(5):
            raise RuntimeError("test extraction was never released")
        return real_extract(path)

    monkeypatch.setattr(knowledge_jobs, "extract_text", blocked)

    async def scenario():
        bus = SessionBus("cancel-job")
        manager = KnowledgeIngestManager(bus, db)
        bus.register_topic("knowledge-ingest", manager.snapshot)
        job_id = manager.start(str(file), folder=False, workspace_id=9)["jobId"]
        assert await asyncio.to_thread(begun.wait, 5)
        snapshot = manager.cancel(job_id)
        assert snapshot["cancelRequested"]
        release.set()
        return await _completed(manager, job_id)

    try:
        final = asyncio.run(scenario())
    finally:
        release.set()
    assert final["status"] == "cancelled"
    collection = knowledge_store.get_or_create_workspace_collection(db, 9)
    assert knowledge_store.list_documents(db, collection_id=collection) == []


def test_native_picker_intent_pins_workspace_before_dialog_returns(tmp_path, monkeypatch):
    db = tmp_path / "knowledge.db"
    monkeypatch.setattr(intents_knowledge, "DEFAULT_DB_PATH", db)
    file = tmp_path / "import.txt"
    file.write_text("pinned workspace text")

    async def scenario():
        bus, document, _ = make_bus()
        document.current_workspace_id = 15

        async def pick():
            document.current_workspace_id = 16
            return [str(file)]

        monkeypatch.setattr(native_dialogs, "pick_files", pick)
        response = await bus.dispatch_intent("knowledge", "addFiles", [])
        assert "jobId" in response
        for _ in range(500):
            jobs = await bus.dispatch_intent("knowledge", "getIngestJobs", [])
            status = jobs["jobs"][-1]["status"]
            if status in {"completed", "failed", "cancelled"}:
                assert status == "completed"
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("intent-launched import did not complete")
        return document

    asyncio.run(scenario())
    first = knowledge_store.get_or_create_workspace_collection(db, 15)
    second = knowledge_store.get_or_create_workspace_collection(db, 16)
    assert len(knowledge_store.list_documents(db, collection_id=first)) == 1
    assert knowledge_store.list_documents(db, collection_id=second) == []


def test_staged_chat_document_import_is_workspace_scoped_and_rejects_stale_ids(tmp_path, monkeypatch):
    """An opaque composer attachment ID, never a browser-supplied path, grants the import."""
    db = tmp_path / "knowledge.db"
    monkeypatch.setattr(intents_knowledge, "DEFAULT_DB_PATH", db)
    file = tmp_path / "chat-notes.md"
    file.write_text("A note attached to the current conversation")
    staged = StagedAttachment(id="staged-1", kind="document", name=file.name, path=str(file))
    composer = SimpleNamespace(staged_attachments=[staged])
    document = SceneDocument()
    document.current_workspace_id = 314
    bus = SessionBus("chat-import")
    intents_knowledge.register_knowledge_intents(bus, document, composer_document=composer)

    async def scenario():
        with pytest.raises(IngestError, match="no longer staged"):
            await bus.dispatch_intent("knowledge", "addChatAttachment", ["unknown-id"])
        composer.staged_attachments = [StagedAttachment(id="image", kind="image", path=str(file))]
        with pytest.raises(IngestError, match="Only supported"):
            await bus.dispatch_intent("knowledge", "addChatAttachment", ["image"])
        composer.staged_attachments = [staged]
        result = await bus.dispatch_intent("knowledge", "addChatAttachment", ["staged-1"])
        document.current_workspace_id = 315
        for _ in range(500):
            rows = (await bus.dispatch_intent("knowledge", "getIngestJobs", []))["jobs"]
            row = next(row for row in rows if row["jobId"] == result["jobId"])
            if row["status"] in {"completed", "cancelled", "failed"}:
                assert row["status"] == "completed"
                assert row["added"] == 1
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("staged attachment import did not finish")

    asyncio.run(scenario())
    current = knowledge_store.get_or_create_workspace_collection(db, 314)
    next_workspace = knowledge_store.get_or_create_workspace_collection(db, 315)
    assert len(knowledge_store.list_documents(db, collection_id=current)) == 1
    assert knowledge_store.list_documents(db, collection_id=next_workspace) == []


def test_workspace_file_import_checks_root_trust_and_path_before_scheduling(tmp_path, monkeypatch):
    """Files panel paths remain relative to the live grant, not an arbitrary host path."""
    db = tmp_path / "knowledge.db"
    monkeypatch.setattr(intents_knowledge, "DEFAULT_DB_PATH", db)
    project = tmp_path / "project"
    project.mkdir()
    file = project / "file.txt"
    file.write_text("Workspace file content added to knowledge")
    private = tmp_path / "private.txt"
    private.write_text("Never index this outside file")
    (project / "binary.png").write_bytes(b"not indexable")
    node_document = SceneDocument()
    node_document.current_workspace_id = 81
    agent = node_document.add_harness_node(0, 0, "Inspect project")
    agent.state.harness_workspace_path = str(project)
    trusted = [str(project.resolve())]
    settings = SimpleNamespace(get_harness_trusted_dirs=lambda: trusted)
    dispatcher = SimpleNamespace(_settings_manager=settings)
    bus = SessionBus("agent-import")
    intents_knowledge.register_knowledge_intents(
        bus, node_document, agent_dispatcher=dispatcher,
    )

    async def scenario():
        with pytest.raises(WorkspaceError, match="Workspace folder changed"):
            await bus.dispatch_intent("knowledge", "addWorkspaceFile", [agent.id, "file.txt", str(tmp_path)])
        with pytest.raises(WorkspaceError, match="outside"):
            await bus.dispatch_intent("knowledge", "addWorkspaceFile", [agent.id, "../private.txt", str(project)])
        with pytest.raises(IngestError, match="file type"):
            await bus.dispatch_intent("knowledge", "addWorkspaceFile", [agent.id, "binary.png", str(project)])
        with pytest.raises(WorkspaceError, match="no longer exists"):
            await bus.dispatch_intent("knowledge", "addWorkspaceFile", ["unknown-node", "file.txt", str(project)])

        response = await bus.dispatch_intent("knowledge", "addWorkspaceFile", [agent.id, "file.txt", str(project)])
        for _ in range(500):
            jobs = (await bus.dispatch_intent("knowledge", "getIngestJobs", []))["jobs"]
            row = next(row for row in jobs if row["jobId"] == response["jobId"])
            if row["status"] in {"completed", "cancelled", "failed"}:
                assert row["status"] == "completed"
                assert row["added"] == 1
                break
            await asyncio.sleep(0.02)
        else:
            pytest.fail("workspace import did not finish")
        trusted.clear()
        # Revoking a grant can also fail closed at root pinning on Windows.
        with pytest.raises(WorkspaceError, match=r"Workspace (?:folder|root) changed"):
            await bus.dispatch_intent("knowledge", "addWorkspaceFile", [agent.id, "file.txt", str(project)])

    asyncio.run(scenario())
    collection = knowledge_store.get_or_create_workspace_collection(db, 81)
    records = knowledge_store.list_documents(db, collection_id=collection)
    assert len(records) == 1
    assert records[0]["title"] == "file.txt"


def test_direct_file_import_rejects_symlink_escape_from_authorized_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("Must never be imported")
    link = root / "link.md"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Filesystem does not support user symlinks")
    job = KnowledgeIngestJob("link-test", "file", 1)
    with pytest.raises(IngestError, match="authorized workspace"):
        _collect_files(link, folder=False, job=job, allowed_root=root)
