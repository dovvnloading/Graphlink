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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"detached_workflow_runs", "workflow_library", "workflow_library_versions",
                "workflow_child_runs", "workflow_presets"} <= tables
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
