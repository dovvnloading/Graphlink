import asyncio
import json
import sqlite3
from dataclasses import asdict, replace

import pytest

from backend import chat_library, workflow_library_store as store
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
    with chat_library._connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == chat_library.CHATS_DB_SCHEMA_VERSION
        first = store.save(conn, example(), name="Original")
    with chat_library._connect(path) as conn:
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
