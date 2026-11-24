import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, replace

import pytest

from backend import chat_library, workflow_library_store as store
from backend.workflow import detached_store
from graphlink_migrations import run_sqlite_migrations
from backend.workflow.contracts import (
    InputBinding, PortSpec, StepOutputSource, WorkflowInputSource,
    WorkflowOutput, WorkflowSpec, WorkflowStepSpec, freeze_config,
)
from backend.workflow.library import (
    configured_spec, decode_spec, execute, export_document, import_document,
    library_registry, make_document, validate_document,
)


def example():
    config = freeze_config({"operation": "copy"})
    adapter = library_registry().describe("note.text", "1", config)
    step = WorkflowStepSpec("one", adapter, config, (InputBinding("text", WorkflowInputSource("input")),))
    spec = WorkflowSpec(1, "workflow", "revision", "Test",
                        (PortSpec("input", "text", True),), (step,),
                        (WorkflowOutput(PortSpec("output", "text", True), StepOutputSource("one", "text")),))
    return make_document(spec, [{"name": "operation", "step_id": "one", "setting": "operation",
                                 "kind": "choice", "choices": ["copy", "word_count"]}])


def test_version_pin_rename_duplicate_delete_and_migration(tmp_path):
    path = tmp_path / "chats.db"
    with closing(chat_library._connect(path)) as conn, conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == chat_library.CHATS_DB_SCHEMA_VERSION
        first = store.save(conn, example(), name="Original")
    with closing(chat_library._connect(path)) as conn, conn:
        second = store.save(conn, example(), name="Second", workflow_id=first["workflow_id"],
                            expected_revision=first["revision"])
        assert second["version"] == 2
        with pytest.raises(ValueError, match="newer"):
            store.save(conn, example(), name="Stale", workflow_id=first["workflow_id"],
                       expected_revision=first["revision"])
        store.rename(conn, first["workflow_id"], "Renamed")
        assert store.list_library(conn, "rename")[0]["version"] == 2
        assert store.get(conn, first["workflow_id"], first["revision"])["spec"]["name"] == "Original"
        copy = store.duplicate(conn, first["workflow_id"], first["revision"], "Copy")
        assert copy["workflow_id"] != first["workflow_id"]
        store.delete(conn, first["workflow_id"])
        assert len(store.list_library(conn)) == 1
        assert len(store.history(conn, first["workflow_id"])) == 2
        assert store.get(conn, first["workflow_id"], first["revision"])
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("version_seven", ["detached", "library"])
def test_upgrade_from_either_version_seven_schema(tmp_path, version_seven):
    path = tmp_path / "chats.db"
    migrations = dict(chat_library._MIGRATIONS)
    migrations[7] = detached_store.migrate if version_seven == "detached" else store.migrate
    with closing(sqlite3.connect(path)) as conn:
        run_sqlite_migrations(conn, 7, migrations)
        if version_seven == "library":
            with conn:
                original = store.save(conn, example(), name="Original")
    with closing(chat_library._connect(path)) as conn, conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"detached_workflow_runs", "workflow_library", "workflow_library_versions",
                "workflow_child_runs", "workflow_presets", "detached_workflow_operations"} <= tables
        if version_seven == "library":
            assert store.get(conn, original["workflow_id"], original["revision"])["spec"]["name"] == "Original"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_round_trip_and_parameters():
    document = example()
    assert import_document(export_document(document)) == document
    spec = configured_spec(document, {"operation": "word_count"})
    result = asyncio.run(execute(spec, {"input": "one two three"}, "test"))
    assert result == {"output": {"kind": "text", "text": "3"}}
    assert asyncio.run(execute(decode_spec(document["spec"]), {"input": "abc"}, "test"))["output"]["text"] == "abc"
    with pytest.raises(ValueError, match="parameter"):
        configured_spec(document, {"operation": "unknown"})


def test_invalid_spec_and_secrets():
    document = example()
    step = document["spec"]["steps"][0]
    step["bindings"][0]["source"] = {"kind": "step", "step_id": "one", "port": "text"}
    document["spec"]["inputs"] = []
    with pytest.raises(ValueError, match="cycle"):
        validate_document(document)
    document = example()
    document["spec"]["steps"][0]["adapter"]["side_effect_class"] = "writes"
    with pytest.raises(ValueError, match="interface"):
        validate_document(document)
    document = example()
    document["spec"]["name"] = "sk-" + "a" * 32
    with pytest.raises(ValueError, match="credentials"):
        import_document(json.dumps(document))
    assert "sk-" not in export_document(document)
    spec = decode_spec(example()["spec"])
    broken = replace(spec, outputs=(WorkflowOutput(PortSpec("bad", "image", True), StepOutputSource("one", "text")),))
    with pytest.raises(ValueError, match="incompatible"):
        decode_spec(json.loads(json.dumps(asdict(broken))))


def test_capture_unpack_and_run_as_one_step(tmp_path):
    from backend.domain.graph import SceneDocument
    from backend.domain.graph_runner import plan_graph_run
    from backend.workflow.graph_execution import GraphRunExecution
    from backend.workflow.graph_steps import adapter_for_step
    from backend.workflow import canvas

    doc = SceneDocument()
    source = doc.add_note(0, 0)
    source.content = "private source must not enter the library"
    first = doc.add_note(400, 0)
    second = doc.add_note(800, 0)
    doc.bind_note_text_input(source.id, first.id, "copy")
    doc.bind_note_text_input(first.id, second.id, "word_count")
    captured = canvas.selected_workflow(doc, [source.id, first.id, second.id], "Count", ["output_2"])
    assert "private source" not in json.dumps(captured)
    assert len(captured["spec"]["inputs"]) == 1
    with closing(chat_library._connect(tmp_path / "chats.db")) as conn, conn:
        pin = store.save(conn, captured, name="Count")
        frozen = store.get(conn, pin["workflow_id"], pin["revision"])
    node_id = canvas.insert(doc, frozen, 1, 1200, 0)
    node = doc.nodes[node_id]
    canvas.configure(doc, node, {}, {}, {"input_1": source.id})
    steps = plan_graph_run(doc, [node_id])
    assert len(steps) == 1 and steps[0]["kind"] == "workflow" and steps[0]["status"] == "ready"

    async def resolve(wid, rev):
        assert (wid, rev) == (pin["workflow_id"], pin["revision"])
        return frozen

    def record(operation, node_ids):
        return doc.record_command("runGraphStep", "user", operation, node_ids=node_ids, run_id="test-run")

    async def checkpoint():
        return True

    execution = GraphRunExecution(
        doc, None, None, checkpoint, {"runId": "test-run"},
        {node_id: steps[0]}, record, 0, workflow_resolver=resolve,
    )
    asyncio.run(adapter_for_step(steps[0]).run(execution, steps[0]))
    assert steps[0]["status"] == "completed"
    assert canvas.data(node)["outputs"]["output_2"]["text"] == "7"
    assert len(steps[0]["childRun"]["steps"]) == 2
    assert plan_graph_run(doc, [node_id])[0]["status"] == "current"
    ids = canvas.unpack(doc, frozen, {"input_1": "one two"}, {}, 1600, 0)
    assert len(ids) == 3
    doc.run_note_text_input(ids[1])
    doc.run_note_text_input(ids[2])
    assert doc.nodes[ids[2]].content == "2"
    with pytest.raises(ValueError, match="cycle"):
        canvas.configure(doc, node, {}, {}, {"input_1": node_id})


def test_presets_pin_parameters_and_triggers_never_backfill(tmp_path):
    from datetime import datetime
    from backend.workflow.api import WorkflowService

    async def scenario():
        path = tmp_path / "chats.db"
        workspace = chat_library.get_default_workspace_id(path)
        with closing(chat_library._connect(path)) as conn, conn:
            pin = store.save(conn, example(), name="Automation")
        service = WorkflowService(path)
        automation = service.automation
        request = {"workflow_id": pin["workflow_id"], "revision": pin["revision"],
                   "inputs": {"input": "one two three"}, "parameters": {"operation": "word_count"}}
        preview = automation.preview(request)
        body = {"request": request, "name": "Count", "allowed_effects": ["pure"],
                "cost_usd": 0, "fingerprint": preview["fingerprint"], "approved": True,
                "trigger": {"kind": "interval", "seconds": 10}}
        with pytest.raises(PermissionError):
            automation.save(workspace, {**body, "approved": False})
        preset = automation.save(workspace, body)
        pid = preset["preset_id"]
        with pytest.raises(PermissionError):
            automation.run(workspace, pid, request={**request, "inputs": {"input": "changed"}})
        assert automation.list(workspace + 1) == []
        await automation.tick(monotonic=0)
        assert service.engine().store.list(workspace) == []
        await automation.tick(monotonic=10)
        rows = service.engine().store.list(workspace)
        assert len(rows) == 1
        assert (await service.engine().wait(workspace, rows[0]["run_id"]))["outputs"]["output"]["text"] == "3"
        await automation.tick(monotonic=1000)
        assert len(service.engine().store.list(workspace)) == 1
        first = automation.run(workspace, pid, operation_id="repeat-0001")
        await service.engine().wait(workspace, first)
        assert automation.run(workspace, pid, operation_id="repeat-0001") == first
        await service.shutdown()

        # Fresh process baseline: a missed interval is not admitted.
        restarted = WorkflowService(path)
        await restarted.automation.tick(monotonic=9999)
        assert len(restarted.engine().store.list(workspace)) == 2
        assert restarted.automation.delete(workspace, pid)
        daily = restarted.automation.save(workspace, {
            **body, "trigger": {"kind": "daily", "time": "09:00"},
        })
        await restarted.automation.tick(now=datetime(2026, 1, 1, 9, 1))
        assert len(restarted.engine().store.list(workspace)) == 2
        await restarted.automation.tick(now=datetime(2026, 1, 2, 9, 0))
        rows = restarted.engine().store.list(workspace)
        assert len(rows) == 3
        await restarted.engine().wait(workspace, rows[0]["run_id"])
        assert restarted.automation.delete(workspace, daily["preset_id"])

        folder = tmp_path / "watched"
        folder.mkdir()
        restarted.automation.save(workspace, {**body, "trigger": {"kind": "folder", "path": str(folder)}})
        await restarted.automation.tick()
        assert len(restarted.engine().store.list(workspace)) == 3
        (folder / "new.txt").write_text("new")
        await restarted.automation.tick()
        rows = restarted.engine().store.list(workspace)
        assert len(rows) == 4
        await restarted.engine().wait(workspace, rows[0]["run_id"])
        await restarted.automation.tick()
        assert len(restarted.engine().store.list(workspace)) == 4
        await restarted.shutdown()
    asyncio.run(scenario())


def test_builder_workflow_edits_preview_and_recheck_after_approval(tmp_path):
    from backend.domain.graph import SceneDocument
    from backend.providers.base import ToolCall
    from backend.tools import GRAPH_MUTATE, GRAPH_READ, RunContext, ToolRegistry
    from backend.tools_workflow import register_workflow_tools
    from backend.workflow import canvas

    async def scenario():
        path = tmp_path / "chats.db"
        with closing(chat_library._connect(path)) as conn, conn:
            pin = store.save(conn, example(), name="Builder workflow")
        doc = SceneDocument()
        doc.current_workspace_id = chat_library.get_default_workspace_id(path)
        registry = ToolRegistry()
        register_workflow_tools(registry, doc, path)
        approvals = []
        decision = [False]
        edit_during_approval = [None]

        async def approve(call):
            approvals.append(call.name)
            if edit_during_approval[0] is not None:
                edit_during_approval[0]()
            return decision[0]

        ctx = RunContext(frozenset({GRAPH_READ, GRAPH_MUTATE}), approve)

        async def call(name, args):
            return await registry.invoke(ToolCall("test", "workflow." + name, args), ctx)

        async def preview(action, args):
            result = await call("preview_edit", {"action": action, "arguments": args})
            assert not result.is_error, result.content
            return json.loads(result.content)

        async def apply(action, args):
            return await call(action, {"review": await preview(action, args)})

        listed = await call("list", {})
        assert not listed.is_error and json.loads(listed.content)["workflows"][0]["workflow_id"] == pin["workflow_id"]
        args = {"workflow_id": pin["workflow_id"], "revision": pin["revision"]}
        review = await preview("instantiate", args)
        assert not doc.nodes and not approvals
        denied = await call("instantiate", {"review": review})
        assert denied.is_error and not doc.nodes
        decision[0] = True
        inserted = await call("instantiate", {"review": review})
        assert not inserted.is_error, inserted.content
        node_id = json.loads(inserted.content)["node_id"]
        assert doc.nodes[node_id].kind == canvas.KIND
        assert canvas.data(doc.nodes[node_id])["document"]["spec"]["revision"] == pin["revision"]

        made = await apply("create_control", {"kind": "input", "text": "hello world"})
        assert not made.is_error, made.content
        source_id = json.loads(made.content)["node_id"]
        args = {"source_id": source_id, "operation": "word_count"}
        review = await preview("create_step", args)
        before = set(doc.nodes)
        edit_during_approval[0] = lambda: setattr(doc.nodes[source_id], "content", "changed")
        stale = await call("create_step", {"review": review})
        assert stale.is_error and set(doc.nodes) == before
        edit_during_approval[0] = None
        step = await apply("create_step", args)
        assert not step.is_error, step.content
        step_id = json.loads(step.content)["node_id"]
        wired = await apply("wire_inputs", {"node_id": node_id, "inputs": {},
                                           "parameters": {"operation": "copy"}, "sources": {"input": step_id}})
        assert not wired.is_error, wired.content
        state = canvas.data(doc.nodes[node_id])
        assert state["sources"] == {"input": step_id} and not state["childRunId"]
        assert len(approvals) == 6

    asyncio.run(scenario())


def test_unpack_invalid_source_leaves_graph_unchanged():
    from backend.domain.graph import SceneDocument
    from backend.workflow import canvas

    doc = SceneDocument()
    before_nodes = set(doc.nodes)
    before_edges = set(doc.edges)
    with pytest.raises(Exception, match="(?i)(source|node|missing|exist)"):
        canvas.unpack(doc, example(), {}, {}, 0, 0, connected={"input": "missing"})
    assert set(doc.nodes) == before_nodes
    assert set(doc.edges) == before_edges


def test_workflow_config_hash_pins_source_identity():
    from backend.domain.graph import SceneDocument
    from backend.workflow import canvas

    doc = SceneDocument()
    first = doc.add_note(0, 0)
    second = doc.add_note(400, 0)
    first.content = second.content = "same text"
    node = doc.nodes[canvas.insert(doc, example(), 1, 800, 0)]
    canvas.configure(doc, node, {}, {}, {"input": first.id})
    before = canvas.config_hash(node)
    canvas.configure(doc, node, {}, {}, {"input": second.id})
    assert canvas.config_hash(node) != before
