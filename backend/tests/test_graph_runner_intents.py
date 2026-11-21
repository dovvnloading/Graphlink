"""Run checkpoints and replay through the actual intent bus and graph database."""

import asyncio
import threading

import pytest

from backend import chat_library
from backend.canvas import SceneDocument
from backend.domain.graph_run_history import restore_graph_runs
from backend.events import IntentValidationError, SessionBus


def open_session(path):
    doc = SceneDocument()
    bus = SessionBus("runner")
    bus.register_topic("scene", doc.scene_payload)
    chat_library.register_chat_library(bus, path, doc, autosave_interval_seconds=None)

    async def send(intent, *args):
        return await bus.dispatch_intent("scene", intent, list(args))

    return bus, doc, send


def setup_chain(doc):
    # A hole guarantees at least one remapped node id after a restart.
    for _ in range(7):
        doc.remove_nodes([doc.add_note(0, 0).id])
    raw = doc.add_table(0, 0, "Region,Qty\nWest,1")
    report = doc.create_table_output(raw.id, "artifact", "Region", "Qty")
    note = doc.add_note(0, 0)
    doc.bind_note_text_input(report.id, note.id)
    doc.set_table_source(raw.id, "Region,Qty\nWest,9", raw.content)
    return raw, report, note


def fail_write(monkeypatch, number):
    write = chat_library.save_chat_atomically_row
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == number:
            raise OSError("injected disk failure")
        return write(*args, **kwargs)

    monkeypatch.setattr(chat_library, "save_chat_atomically_row", fail_once)


def test_preview_is_read_only_stale_rejected_success_history_and_idempotent_retry(tmp_path):
    async def scenario():
        path = tmp_path / "graphs.db"
        bus, doc, send = open_session(path)
        source, report, note = setup_chain(doc)
        assert await send("getGraphRun") is None
        selected = [source.id]
        preview = await send("previewGraphRun", selected)
        assert not path.exists() and not doc.command_log
        doc.set_table_source(source.id, "Region,Qty\nWest,4", source.content)
        with pytest.raises(IntentValidationError, match="changed"):
            await send("executeGraphRun", selected, preview["previewToken"], "operation-one")
        preview = await send("previewGraphRun", selected)
        args = [selected, preview["previewToken"], "operation-one"]
        result = await send("executeGraphRun", *args)
        assert result["status"] == "completed"
        assert [s["status"] for s in result["steps"]] == ["completed", "completed"]
        assert result["steps"][0]["outputHash"] == result["steps"][1]["inputHash"]
        assert report.state.table_output.current.version == 2 and "4" in note.content
        assert all(c.run_id == "operation-one" for c in doc.command_log)
        assert (await send("getGraphRun"))["history"] == []
        assert await send("executeGraphRun", *args) == result
        assert await send("resumeGraphRun", "operation-one") == result
        with pytest.raises(IntentValidationError, match="another preview"):
            await send("executeGraphRun", [note.id], preview["previewToken"], "operation-one")
        assert len(doc.command_log) == 2
        # A new run records the existing result as current and edits only a
        # newly added target. Both runs survive a new process and row reload.
        extra = doc.add_note(0, 0)
        doc.bind_note_text_input(note.id, extra.id)
        new_preview = await send("previewGraphRun", [])
        second = await send("executeGraphRun", [], new_preview["previewToken"], "operation-two")
        assert [s["status"] for s in second["steps"]] == ["current", "current", "completed"]
        bus2, doc2, send2 = open_session(path)
        await bus2.dispatch_intent("app-chat-library", "loadChat", [doc.current_chat_id])
        latest = await send2("getGraphRun")
        assert latest["runId"] == "operation-two"
        assert [r["runId"] for r in latest["history"]] == ["operation-one"]
        assert len({s["nodeId"] for s in latest["steps"]}) == 3
        assert all(s["nodeId"] in doc2.nodes and s["sourceId"] in doc2.nodes for s in latest["steps"])
        # Replay the exact old transport message, including its original ids.
        assert (await send2("executeGraphRun", *args))["status"] == "completed"
        assert not doc2.command_log
        await bus2.dispatch_intent("app-chat-library", "newChat", [])
        assert await send2("getGraphRun") is None
        with pytest.raises(IntentValidationError, match="open graph"):
            await send2("resumeGraphRun", "operation-one")

    asyncio.run(scenario())


@pytest.mark.parametrize("edit_source", [False, True])
def test_restart_resumes_only_uncommitted_steps_and_honors_upstream_drift(tmp_path, monkeypatch, edit_source):
    async def scenario():
        path = tmp_path / "graphs.db"
        _, doc, send = open_session(path)
        raw, report, note = setup_chain(doc)
        preview = await send("previewGraphRun", [])
        # Successful admission and report checkpoint, then crash while
        # saving the note. The SQLite graph and journal must agree.
        fail_write(monkeypatch, 3)
        with pytest.raises(IntentValidationError, match="checkpoint"):
            await send("executeGraphRun", [], preview["previewToken"], "recover-chain")
        chat_id = doc.current_chat_id
        assert chat_id and (await send("getGraphRun"))["status"] == "interrupted"

        fresh, reopened, send2 = open_session(path)
        await fresh.dispatch_intent("app-chat-library", "loadChat", [chat_id])
        paused = await send2("getGraphRun")
        assert paused["status"] == "interrupted"
        first, second = paused["steps"]
        assert (first["status"], second["status"]) == ("completed", "pending")
        assert (first["nodeId"], second["nodeId"]) != (report.id, note.id)
        report2 = reopened.nodes[first["nodeId"]]
        note2 = reopened.nodes[second["nodeId"]]
        initial_note_content = note2.content
        assert report2.state.table_output.current.version == 2
        assert note2.content != report2.state.artifact_content
        if edit_source:
            source = reopened.nodes[first["sourceId"]]
            reopened.set_table_source(source.id, "Region,Qty\nWest,72", source.content)
        result = await send2("resumeGraphRun", "recover-chain")
        assert result["status"] == ("partial" if edit_source else "completed")
        if edit_source:
            assert "upstream" in result["steps"][1]["detail"]
            assert not reopened.command_log
            assert note2.content == initial_note_content
        else:
            assert len(reopened.command_log) == 1
            assert note2.content == report2.state.artifact_content
        assert report2.state.table_output.current.version == 2

    asyncio.run(scenario())


def test_final_checkpoint_failure_retries_without_repeating_outputs(tmp_path, monkeypatch):
    async def scenario():
        path = tmp_path / "graphs.db"
        _, doc, send = open_session(path)
        raw, report, note = setup_chain(doc)
        token = (await send("previewGraphRun", []))["previewToken"]
        fail_write(monkeypatch, 4)
        with pytest.raises(IntentValidationError, match="checkpoint"):
            await send("executeGraphRun", [], token, "retry-terminal")
        assert (await send("getGraphRun"))["status"] == "interrupted"
        row = chat_library.load_chat_row(path, doc.current_chat_id)
        saved = row["data"]["graph_runs"][0]
        assert saved["status"] == "running" and all(s["status"] == "completed" for s in saved["steps"])
        retry = await send("resumeGraphRun", "retry-terminal")
        assert retry["status"] == "completed" and len(doc.command_log) == 2
        assert report.state.table_output.current.version == 2
        assert chat_library.load_chat_row(path, doc.current_chat_id)["data"]["graph_runs"][0]["status"] == "completed"
        assert "9" in note.content

    asyncio.run(scenario())


def test_concurrent_replay_and_navigation_while_checkpoint_in_flight(tmp_path, monkeypatch):
    async def scenario():
        path = tmp_path / "graphs.db"
        bus, doc, send = open_session(path)
        source, report, note = setup_chain(doc)
        token = (await send("previewGraphRun", []))["previewToken"]
        original_write = chat_library.save_chat_atomically_row
        started, released = threading.Event(), threading.Event()

        def slow_write(*args, **kwargs):
            started.set()
            assert released.wait(5)
            return original_write(*args, **kwargs)

        monkeypatch.setattr(chat_library, "save_chat_atomically_row", slow_write)
        args = [[], token, "parallel-request"]
        first = asyncio.create_task(send("executeGraphRun", *args))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            assert (await send("executeGraphRun", *args))["status"] == "running"
            with pytest.raises(IntentValidationError, match="in progress"):
                await send("executeGraphRun", [], token, "parallel-second")
            await bus.dispatch_intent("app-chat-library", "newChat", [])
            assert source.id in doc.nodes and len(doc.graph_runs) == 1
        finally:
            released.set()
        assert (await first)["status"] == "completed" and len(doc.command_log) == 2

    asyncio.run(scenario())


def test_cancellation_drains_the_graph_save_and_preserves_its_row_for_resume(tmp_path, monkeypatch):
    async def scenario():
        path = tmp_path / "graphs.db"
        bus, doc, send = open_session(path)
        setup_chain(doc)
        token = (await send("previewGraphRun", []))["previewToken"]
        original_write = chat_library.save_chat_atomically_row
        started, released = threading.Event(), threading.Event()

        def pause_first_save(*args, **kwargs):
            started.set()
            assert released.wait(5)
            return original_write(*args, **kwargs)

        monkeypatch.setattr(chat_library, "save_chat_atomically_row", pause_first_save)
        accepted = asyncio.create_task(send("executeGraphRun", [], token, "cancel-checkpoint"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            accepted.cancel()
            await asyncio.sleep(0)
            assert bus.chat_mutation_guard["active"]
            assert not doc.command_log
        finally:
            released.set()
        with pytest.raises(asyncio.CancelledError):
            await accepted
        saved_id = doc.current_chat_id
        assert saved_id and not bus.chat_mutation_guard["active"]
        assert (await send("getGraphRun"))["status"] == "interrupted"
        assert (await send("resumeGraphRun", "cancel-checkpoint"))["status"] == "completed"
        assert doc.current_chat_id == saved_id and len(chat_library.get_all_chats(path)) == 1

    asyncio.run(scenario())


def test_malformed_import_cannot_restore_half_a_plan_and_unmapped_ids_never_alias(tmp_path):
    async def scenario():
        _, doc, send = open_session(tmp_path / "graphs.db")
        setup_chain(doc)
        token = (await send("previewGraphRun", []))["previewToken"]
        await send("executeGraphRun", [], token, "import-record")
        record, = doc.graph_runs
        legacy = dict(record)
        legacy["previewToken"] = legacy.pop("previewHash")
        restored, = restore_graph_runs([legacy], {node_id: node_id for node_id in doc.nodes})
        assert restored == record and "previewToken" not in restored

        first, second = record["steps"]
        raw = [{**record, "status": "running", "steps": [first, {**second, "status": "pending"}]}]
        # The second destination id is present as a new value in the map but
        # absent as an original id. It must never point at that other node.
        saved = restore_graph_runs(raw, {first["nodeId"]: second["nodeId"]})
        assert saved[0]["status"] == "interrupted"
        missing = saved[0]["steps"][1]
        assert missing["nodeId"] == "" and missing["status"] == "blocked"
        raw[0]["steps"][0] = {**first, "inputHash": ["bad"]}
        assert not restore_graph_runs(raw, {})
        assert not restore_graph_runs([None, {"runId": []}, {"status": {"pending": True}}], {})

    asyncio.run(scenario())



@pytest.mark.parametrize("commit_succeeds", [False, True], ids=["rollback", "commit"])
def test_final_write_is_reported_as_running_until_sqlite_commits(tmp_path, monkeypatch, commit_succeeds):
    async def scenario():
        path = tmp_path / "graphs.db"
        _, doc, send = open_session(path)
        setup_chain(doc)
        token = (await send("previewGraphRun", []))["previewToken"]
        original_write = chat_library.save_chat_atomically_row
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        released = threading.Event()

        def pause_terminal(db_path, chat_id, title, data, *args, **kwargs):
            if data["graph_runs"][-1]["status"] == "completed":
                # The writer runs in a thread; notify the event loop without
                # assuming how long earlier graph checkpoints take on CI.
                loop.call_soon_threadsafe(started.set)
                released.wait()
                if not commit_succeeds:
                    raise OSError("terminal write rolled back")
            return original_write(db_path, chat_id, title, data, *args, **kwargs)

        def persisted_status():
            row = chat_library.load_chat_row(path, doc.current_chat_id)
            return row["data"]["graph_runs"][-1]["status"]

        monkeypatch.setattr(chat_library, "save_chat_atomically_row", pause_terminal)
        args = [[], token, "terminal-receipt"]
        run = asyncio.create_task(send("executeGraphRun", *args))
        started_waiter = asyncio.create_task(started.wait())
        try:
            # Wait for the intercepted terminal write, or fail promptly if the
            # run ends without reaching it. The timeout is only a deadlock guard.
            async with asyncio.timeout(60):
                done, _ = await asyncio.wait({started_waiter, run}, return_when=asyncio.FIRST_COMPLETED)
            if run in done:
                await run
                pytest.fail("graph run finished without reaching the terminal SQLite write")
            assert not run.done()
            assert (await send("executeGraphRun", *args))["status"] == "running"
            assert (await send("getGraphRun"))["status"] == "running"
            assert persisted_status() == "running"
        finally:
            released.set()
            started_waiter.cancel()
            await asyncio.gather(started_waiter, return_exceptions=True)

        if commit_succeeds:
            assert (await run)["status"] == "completed"
            assert (await send("getGraphRun"))["status"] == "completed"
            assert persisted_status() == "completed"
        else:
            with pytest.raises(IntentValidationError, match="checkpoint"):
                await run
            assert (await send("getGraphRun"))["status"] == "interrupted"
            assert persisted_status() == "running"

    asyncio.run(scenario())


def test_checkpoint_never_recreates_a_deleted_graph(tmp_path):
    async def scenario():
        path = tmp_path / "graphs.db"
        bus, doc, send = open_session(path)
        setup_chain(doc)
        assert await bus.dispatch_intent("app-chat-library", "saveChat", [])
        run_id = doc.current_chat_id
        token = (await send("previewGraphRun", []))["previewToken"]
        assert chat_library.delete_chat(path, run_id)
        with pytest.raises(IntentValidationError, match="checkpoint"):
            await send("executeGraphRun", [], token, "deleted-graph")
        assert not doc.command_log and not chat_library.get_all_chats(path)
        assert doc.current_chat_id == run_id

    asyncio.run(scenario())


def test_workspace_archive_preserves_completed_history_and_resumes_interrupted_run(tmp_path, monkeypatch):
    async def scenario():
        source_db = tmp_path / "original.db"
        _, doc, send = open_session(source_db)
        raw, report, note = setup_chain(doc)
        first = (await send("previewGraphRun", []))["previewToken"]
        await send("executeGraphRun", [], first, "finished-before-export")

        doc.set_table_source(raw.id, "Region,Qty\nWest,12", raw.content)
        token = (await send("previewGraphRun", []))["previewToken"]
        fail_write(monkeypatch, 3)
        with pytest.raises(IntentValidationError, match="checkpoint"):
            await send("executeGraphRun", [], token, "interrupted-export")
        row, notes, pins = chat_library.load_chat_snapshot(source_db, doc.current_chat_id)
        archive = tmp_path / "workflows.graphlink"
        chat_library.workspace_archive.export_archive(archive, [{**row, "notes": notes, "pins": pins}])

        imported_db = tmp_path / "imported.db"
        workspace_id = chat_library.get_all_workspaces(imported_db)[0]["id"]
        imported_id, = chat_library.import_workspace_rows_atomically(imported_db, archive, workspace_id)
        bus, imported, send_again = open_session(imported_db)
        await bus.dispatch_intent("app-chat-library", "loadChat", [imported_id])
        run = await send_again("getGraphRun")
        assert run and run["status"] == "interrupted"
        assert [s["status"] for s in run["steps"]] == ["completed", "pending"]
        historical, = run["history"]
        assert (historical["runId"], historical["status"]) == ("finished-before-export", "completed")
        assert (await send_again("executeGraphRun", [], first, historical["runId"]))["status"] == "completed"

        result = await send_again("resumeGraphRun", run["runId"])
        assert result["status"] == "completed"
        restored_report, restored_note = (imported.nodes[step["nodeId"]] for step in result["steps"])
        assert restored_report.id != report.id and restored_note.id != note.id
        assert restored_report.state.table_output.current.version == 3
        assert "12" in restored_note.content and restored_note.content == restored_report.state.artifact_content
        assert len(imported.command_log) == 1

    asyncio.run(scenario())
