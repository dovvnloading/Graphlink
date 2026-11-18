"""Graph Runner + Workbench integration: controlled workers, never sleeps."""
from __future__ import annotations

import asyncio

import pytest

from backend import chat_library
from backend.api import intents_graph_model as local_model
from backend.canvas import SceneDocument
from backend.events import IntentValidationError, SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import discover_plugins
from backend.plugins import register_plugins
from graphlink_plugins.scientific import workbench
from graphlink_plugins.scientific.core import calculate_result
from graphlink_plugins.scientific.execution import ExecutionFailure
from graphlink_settings_store import SettingsManager


def open_session(path, settings):
    document = SceneDocument()
    bus = SessionBus("scientific-runner-tests")
    notifications = NotificationState()
    bus.register_topic("notification", notifications.payload)
    bus.register_topic("scene", document.scene_payload)
    register_plugins(bus, notifications, document, settings, plugin_registry=discover_plugins())
    chat_library.register_chat_library(
        bus, path, document, notifications, autosave_interval_seconds=None,
        settings_manager=settings,
    )

    async def scene(intent, *args):
        return await bus.dispatch_intent("scene", intent, list(args))

    return document, bus, scene


def settings_for(tmp_path):
    settings = SettingsManager(tmp_path / "science-settings.dat")
    for pack in ("core", "statistics"):
        settings.set_plugin_grant("science_" + pack, True)
    return settings


async def node(bus, title, parent=None):
    return await bus.dispatch_intent("app-plugins", "executePlugin", [title, parent, 100, 100])


async def intent(bus, pack, name, **arguments):
    return await bus.dispatch_intent(
        "app-plugins", "invokePluginIntent", ["science_" + pack, name, arguments],
    )


async def configure(bus, pack, node_id, operation, inputs, sources=None):
    answer = await intent(
        bus, pack, "configure_runner", node_id=node_id, operation_id=operation,
        inputs=inputs, sources=sources or {},
    )
    assert answer == {"ok": True}


async def manually_run(bus, pack, node_id, operation, inputs):
    answer = await intent(bus, pack, "run", node_id=node_id, operation_id=operation, inputs=inputs)
    assert answer == {"ok": True}


def ref(document, source, column=0):
    state = document.nodes[source].state.data
    receipt = state["latest"]["provenance"]
    return {
        "nodeId": source, "sourceKey": state["nodeKey"],
        "resultHash": receipt["resultHash"], "receiptHash": receipt["receiptHash"],
        "selector": ["column", column],
    }


async def seed_csv_pair(bus, document):
    first = await node(bus, "Scientific Core")
    second = await node(bus, "Scientific Core")
    await manually_run(bus, "core", first, "numeric_csv", {"csv": "x\n1\n2\n3"})
    await manually_run(bus, "core", second, "numeric_csv", {"csv": "y\n2\n4\n6"})
    target = await node(bus, "Statistics", first)
    document.connect(second, target)
    await configure(
        bus, "statistics", target, "linear_regression",
        {"x": [1, 2, 3], "y": [2, 4, 6]},
        {"x": ref(document, first), "y": ref(document, second)},
    )
    await configure(bus, "core", first, "numeric_csv", {"csv": "x\n2\n4\n6"})
    await configure(bus, "core", second, "numeric_csv", {"csv": "y\n5\n8\n11"})
    return first, second, target


def test_ready_order_typed_fan_in_and_receipts_without_sleep(tmp_path, monkeypatch):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "graph.db", settings_for(tmp_path))
        calls = []
        waiting = False
        both_entered = asyncio.Event()
        release = asyncio.Event()

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event, input_units=None):
            calls.append((pack, operation.id, inputs, sources, timeout_s, cancel_event))
            if waiting and operation.id == "numeric_csv":
                if len([row for row in calls if row[1] == "numeric_csv"]) >= 4:
                    both_entered.set()
                await release.wait()
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        first, second, target = await seed_csv_pair(bus, document)
        preview = await scene("previewGraphRun", [])
        assert [step["nodeId"] for step in preview["steps"]] == [first, second, target]
        assert [step["status"] for step in preview["steps"]] == ["ready", "ready", "waiting"]
        assert preview["steps"][2]["sourceIds"] == [first, second]
        assert len(calls) == 2  # Explicit manual source setup, not the preview.
        waiting = True
        task = asyncio.create_task(scene(
            "executeGraphRun", [], preview["previewToken"], "science-fanin", 2,
        ))
        await asyncio.wait_for(both_entered.wait(), 5)
        assert [row[1] for row in calls].count("linear_regression") == 0
        release.set()
        run = await task
        assert run["status"] == "completed"
        assert [row["status"] for row in run["steps"]] == ["completed"] * 3
        assert [row[1] for row in calls][-1] == "linear_regression"
        fitted = run["steps"][2]
        assert fitted["scientificResult"]["inputs"] == {"x": [2, 4, 6], "y": [5, 8, 11]}
        assert fitted["scientificResult"]["values"]["slope"] == pytest.approx(1.5)
        assert fitted["scientificResult"]["provenance"]["sources"]["x"]["sourceKey"] == document.nodes[first].state.data["nodeKey"]
        assert isinstance(fitted["elapsedMs"], int) and fitted["elapsedMs"] >= 0
        assert len(fitted["scientificResult"]["provenance"]["receiptHash"]) == 64
        latest = await scene("getGraphRun")
        assert latest["trace"]["steps"][2]["scientificResult"]["resultHash"]
        # A new preview recognizes unchanged scientific steps as current.
        assert [step["status"] for step in (await scene("previewGraphRun", []))["steps"]] == ["current"] * 3
        assert len(calls) == 5

    asyncio.run(scenario())


def test_input_caps_and_unconfigured_nodes_are_not_implicit(tmp_path, monkeypatch):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "graph.db", settings_for(tmp_path))
        raw = await node(bus, "Statistics")
        assert (await scene("previewGraphRun", []))["steps"] == []
        rejected = await intent(
            bus, "statistics", "configure_runner", node_id=raw,
            operation_id="describe", inputs={"observations": [1] * 1025}, sources={},
        )
        assert not rejected["ok"] and document.nodes[raw].state.data.get("runner") is None
        assert (await scene("previewGraphRun", []))["steps"] == []
    asyncio.run(scenario())


def test_typed_timeout_is_terminal_and_keeps_the_previous_result(tmp_path, monkeypatch):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "graph.db", settings_for(tmp_path))
        source = await node(bus, "Scientific Core")
        await configure(bus, "core", source, "convert_unit", {
            "value": 5, "from_unit": "m", "to_unit": "cm",
        })

        async def timed_out(*args, **kwargs):
            raise ExecutionFailure("timeout", "Scientific worker exceeded its wall-clock budget.", 37)

        monkeypatch.setattr(workbench, "execute_in_process", timed_out)
        preview = await scene("previewGraphRun", [])
        run = await scene("executeGraphRun", [], preview["previewToken"], "science-timeout")
        assert run["status"] == "partial"
        step = run["steps"][0]
        assert step["status"] == "failed"
        assert (step["errorType"], step["elapsedMs"]) == ("timeout", 37)
        assert "wall-clock budget" in step["detail"]
        assert "scientificResult" not in step
        assert document.nodes[source].state.data["latest"] is None
        assert (await scene("getGraphRun"))["steps"][0]["errorType"] == "timeout"
    asyncio.run(scenario())


def test_run_cancel_drains_science_and_prevents_resume(tmp_path, monkeypatch):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "graph.db", settings_for(tmp_path))
        source = await node(bus, "Scientific Core")
        await configure(bus, "core", source, "convert_unit", {
            "value": 7, "from_unit": "m", "to_unit": "cm",
        })
        started = asyncio.Event()
        released = asyncio.Event()
        drained = asyncio.Event()
        calls = 0

        async def held(*args, **kwargs):
            nonlocal calls
            calls += 1
            started.set()
            try:
                await released.wait()
            finally:
                drained.set()

        monkeypatch.setattr(workbench, "execute_in_process", held)
        preview = await scene("previewGraphRun", [])
        execution = asyncio.create_task(scene("executeGraphRun", [], preview["previewToken"], "science-cancel"))
        await asyncio.wait_for(started.wait(), 5)
        assert await scene("cancelGraphRun", "science-cancel") == {"ok": True}
        result = await execution
        assert drained.is_set()
        assert result["status"] == "cancelled"
        assert result["steps"][0]["status"] == "cancelled"
        assert result["steps"][0]["errorType"] == "cancelled"
        assert document.nodes[source].state.data["latest"] is None
        assert (await scene("resumeGraphRun", "science-cancel"))["status"] == "cancelled"
        assert calls == 1
    asyncio.run(scenario())


def test_restart_resume_does_not_replay_committed_science(tmp_path, monkeypatch):
    async def scenario():
        path = tmp_path / "graph.db"
        settings = settings_for(tmp_path)
        document, bus, scene = open_session(path, settings)
        source = await node(bus, "Scientific Core")
        await configure(bus, "core", source, "convert_unit", {
            "value": 12, "from_unit": "m", "to_unit": "cm",
        })
        calls = 0

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event, input_units=None):
            nonlocal calls
            calls += 1
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        original = chat_library.save_chat_atomically_row
        checkpoints = 0

        def fail_terminal(*args, **kwargs):
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints == 3:
                raise OSError("injected terminal checkpoint failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(chat_library, "save_chat_atomically_row", fail_terminal)
        preview = await scene("previewGraphRun", [])
        with pytest.raises(IntentValidationError, match="checkpoint"):
            await scene("executeGraphRun", [], preview["previewToken"], "science-resume")
        assert calls == 1
        chat_id = document.current_chat_id
        assert chat_id
        row = chat_library.load_chat_row(path, chat_id)
        assert row["data"]["graph_runs"][0]["steps"][0]["status"] == "completed"

        reopened, new_bus, new_scene = open_session(path, settings)
        await new_bus.dispatch_intent("app-chat-library", "loadChat", [chat_id])
        paused = await new_scene("getGraphRun")
        assert paused["status"] == "interrupted"
        assert paused["steps"][0]["status"] == "completed"
        assert paused["steps"][0]["scientificResult"]["values"]["value (cm)"] == 1200
        result = await new_scene("resumeGraphRun", "science-resume")
        assert result["status"] == "completed"
        assert calls == 1
        assert (await new_scene("previewGraphRun", []))["steps"][0]["status"] == "current"
    asyncio.run(scenario())


def test_mixed_model_and_science_run_keeps_operation_dispatch_separate(tmp_path, monkeypatch):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "graph.db", settings_for(tmp_path))
        prompt = document.add_chat_node(0, 0, "Why this graph?", True)
        model = document.add_model_node(prompt.id, "Ollama", "test-local:latest")
        downstream = document.add_note(0, 0)
        document.bind_note_text_input(model.id, downstream.id)
        science = await node(bus, "Scientific Core")
        await configure(bus, "core", science, "convert_unit", {
            "value": 25, "from_unit": "degC", "to_unit": "K",
        })
        model_calls = []
        scientific_calls = []

        def model_reply(*args, **kwargs):
            model_calls.append((args, kwargs))
            return {"message": {"content": "Local result"}}

        async def scientific_result(pack, operation, inputs, sources, *, timeout_s, cancel_event, input_units=None):
            scientific_calls.append((pack, operation.id))
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(local_model, "local_ollama_complete", model_reply)
        monkeypatch.setattr(workbench, "execute_in_process", scientific_result)
        preview = await scene("previewGraphRun", [])
        assert {step["kind"] for step in preview["steps"]} == {"model", "note", "scientific"}
        run = await scene("executeGraphRun", [], preview["previewToken"], "mixed-science-model", 2)
        assert run["status"] == "completed"
        assert all(step["status"] == "completed" for step in run["steps"])
        assert document.nodes[downstream.id].content == "Local result"
        assert document.nodes[science].state.data["latest"]["values"]["value (K)"] == pytest.approx(298.15)
        assert len(model_calls) == 1
        assert scientific_calls == [("core", "convert_unit")]
        assert not any(step["kind"] == "scientific" and "externalAttempt" in step for step in run["steps"])
    asyncio.run(scenario())


def test_runner_retains_unit_aware_inputs_and_receipt(tmp_path, monkeypatch):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "graph.db", settings_for(tmp_path))
        target = await node(bus, "Scientific Core")

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event, input_units=None):
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        await configure(bus, "core", target, "convert_unit", {
            "value": "2500 mm", "from_unit": "m", "to_unit": "cm",
        })
        assert document.nodes[target].state.data["runner"]["inputs"]["value"] == "2500 mm"
        preview = await scene("previewGraphRun", [])
        run = await scene("executeGraphRun", [], preview["previewToken"], "science-unit-input")
        assert run["status"] == "completed"
        result = run["steps"][0]["scientificResult"]
        assert result["inputs"]["value"] == pytest.approx(2.5)
        assert result["values"]["value (cm)"] == pytest.approx(250)
        assert result["provenance"]["inputUnits"]["value"] == "mm"
        assert (await scene("previewGraphRun", []))["steps"][0]["status"] == "current"

    asyncio.run(scenario())
