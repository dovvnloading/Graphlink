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

# These deadlines only bound a hang: a passing test continues as soon as the
# event fires. Windows CI under coverage can spend substantial time loading
# packs while another worker starts subprocesses; this is a hang bound only.
EVENT_DEADLINE_S = 60


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

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event, input_units=None, **_uncertainty):
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
        await asyncio.wait_for(both_entered.wait(), EVENT_DEADLINE_S)
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
        await asyncio.wait_for(started.wait(), EVENT_DEADLINE_S)
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

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event, input_units=None, **_uncertainty):
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

        async def scientific_result(pack, operation, inputs, sources, *, timeout_s, cancel_event, input_units=None, **_uncertainty):
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

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event, input_units=None, **_uncertainty):
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


def test_table_backed_science_commits_and_adopts_an_uncheckpointed_receipt(tmp_path, monkeypatch):
    """TableState is a real scientific state; recovery must not replay its result."""
    from graphlink_plugins.scientific.data_table import TableState

    async def scenario():
        document, bus, scene = open_session(tmp_path / "table-graph.db", settings_for(tmp_path))
        target = await node(bus, "Scientific Core")
        imported = await intent(bus, "core", "import_table", node_id=target, text="x\n1\n2\n", format="csv")
        assert imported == {"ok": True}
        assert isinstance(document.nodes[target].state, TableState)
        await configure(bus, "core", target, "convert_unit", {
            "value": 2, "from_unit": "m", "to_unit": "cm",
        })
        calls = 0

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event,
                             input_units=None, **_extra):
            nonlocal calls
            calls += 1
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        preview = await scene("previewGraphRun", [])
        completed = await scene("executeGraphRun", [], preview["previewToken"], "science-table-receipt")
        assert completed["status"] == "completed"
        assert completed["steps"][0]["scientificResult"]["values"]["value (cm)"] == 200
        assert isinstance(document.nodes[target].state, TableState)

        # Reproduce a durable result with an interrupted/pending graph receipt,
        # as can happen when the calculation commits before a checkpoint.
        stored = document.graph_runs[-1]
        stored["status"] = "interrupted"
        stored["steps"][0]["status"] = "pending"
        stored["steps"][0].pop("scientificResult", None)
        stored["steps"][0].pop("outputHash", None)
        resumed = await scene("resumeGraphRun", "science-table-receipt")
        assert resumed["status"] == "completed"
        assert resumed["steps"][0]["scientificResult"]["values"]["value (cm)"] == 200
        assert "not replayed" in resumed["steps"][0]["detail"]
        assert calls == 1

    asyncio.run(scenario())


def test_live_linked_scalar_preserves_source_units_when_upstream_changes(tmp_path, monkeypatch):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "units-graph.db", settings_for(tmp_path))
        source = await node(bus, "Scientific Core")
        target = await node(bus, "Scientific Core", source)
        await manually_run(bus, "core", source, "convert_unit", {
            "value": 2, "from_unit": "m", "to_unit": "cm",
        })
        reference = {**ref(document, source), "selector": ["values", "value (cm)"]}
        await configure(bus, "core", target, "convert_unit", {
            "value": "200 cm", "from_unit": "m", "to_unit": "mm",
        }, {"value": reference})

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event,
                             input_units=None, **_extra):
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        first_preview = await scene("previewGraphRun", [])
        assert [step["nodeId"] for step in first_preview["steps"]] == [target]
        first = await scene("executeGraphRun", [], first_preview["previewToken"], "linked-source-units-1")
        assert first["status"] == "completed"
        assert first["steps"][0]["scientificResult"]["values"]["value (mm)"] == 2000
        assert first["steps"][0]["scientificResult"]["inputs"]["value"] == 2
        assert first["steps"][0]["scientificResult"]["provenance"]["inputUnits"]["value"] == "cm"

        await manually_run(bus, "core", source, "convert_unit", {
            "value": 4, "from_unit": "m", "to_unit": "cm",
        })
        second_preview = await scene("previewGraphRun", [])
        assert second_preview["steps"][0]["status"] == "ready"
        second = await scene("executeGraphRun", [], second_preview["previewToken"], "linked-source-units-2")
        assert second["status"] == "completed"
        assert second["steps"][0]["scientificResult"]["values"]["value (mm)"] == 4000
        assert second["steps"][0]["scientificResult"]["provenance"]["inputUnits"]["value"] == "cm"

    asyncio.run(scenario())


def test_runner_save_token_rejects_stale_configuration_and_undo_restores_it(tmp_path):
    from graphlink_plugins.scientific.workbench import _run_token

    async def scenario():
        document, bus, scene = open_session(tmp_path / "undo-graph.db", settings_for(tmp_path))
        target = await node(bus, "Scientific Core")
        before = _run_token(document.nodes[target].state.data)
        original_inputs = {"value": 2, "from_unit": "m", "to_unit": "cm"}
        changed_inputs = {"value": 3, "from_unit": "m", "to_unit": "cm"}
        saved = await intent(bus, "core", "configure_runner", node_id=target,
                             operation_id="convert_unit", inputs=original_inputs,
                             sources={}, expected_run=before)
        assert saved == {"ok": True}
        changed_token = _run_token(document.nodes[target].state.data)
        assert changed_token != before
        stale = await intent(bus, "core", "configure_runner", node_id=target,
                             operation_id="convert_unit", inputs=changed_inputs,
                             sources={}, expected_run=before)
        assert stale["ok"] is False and stale["conflict"] is True
        assert document.nodes[target].state.data["runner"]["inputs"] == original_inputs
        assert document.undo()
        assert "runner" not in document.nodes[target].state.data
        assert _run_token(document.nodes[target].state.data) == before
        assert (await scene("previewGraphRun", []))["steps"] == []

    asyncio.run(scenario())


def test_unconfigured_source_with_stale_provenance_is_not_implicitly_recomputed(tmp_path):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "stale-graph.db", settings_for(tmp_path))
        raw = await node(bus, "Scientific Core")
        intermediary = await node(bus, "Statistics", raw)
        target = await node(bus, "Scientific Core", intermediary)
        await manually_run(bus, "core", raw, "numeric_csv", {"csv": "x\n1\n2"})
        middle_run = await intent(
            bus, "statistics", "run", node_id=intermediary, operation_id="describe",
            inputs={"observations": [1, 2]}, sources={"observations": ref(document, raw)},
        )
        assert middle_run == {"ok": True}
        linked = {**ref(document, intermediary), "selector": ["values", "mean"]}
        await configure(bus, "core", target, "convert_unit", {
            "value": 1.5, "from_unit": "m", "to_unit": "cm",
        }, {"value": linked})
        assert document.nodes[intermediary].state.data.get("runner") is None
        before = await scene("previewGraphRun", [])
        assert [s["nodeId"] for s in before["steps"]] == [target]
        assert before["steps"][0]["status"] == "ready"

        # The intermediate is deliberately NOT configured. Its old numeric
        # receipt remains checksummed, but its cited CSV is no longer current.
        await manually_run(bus, "core", raw, "numeric_csv", {"csv": "x\n3\n4"})
        after = await scene("previewGraphRun", [])
        assert after["steps"][0]["status"] == "blocked"
        assert "stale" in after["steps"][0]["detail"].lower()
        assert document.nodes[intermediary].state.data["latest"]["values"]["mean"] == 1.5

    asyncio.run(scenario())


def test_cancel_after_science_commit_retains_completed_receipt(tmp_path, monkeypatch):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "late-cancel.db", settings_for(tmp_path))
        target = await node(bus, "Scientific Core")
        await configure(bus, "core", target, "convert_unit", {
            "value": 2, "from_unit": "m", "to_unit": "cm",
        })

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event,
                             input_units=None, **_extra):
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        dispatched = bus.dispatch_intent
        committed = asyncio.Event()

        async def held_reply(topic, name, arguments):
            reply = await dispatched(topic, name, arguments)
            if (topic == "app-plugins" and name == "invokePluginIntent"
                    and arguments[1] == "run" and arguments[2].get("graph_run_id")):
                # The workbench already mutated the graph; only the answer to
                # Graph Runner is paused here. No sleeps or process timing.
                committed.set()
                await asyncio.Event().wait()
            return reply

        monkeypatch.setattr(bus, "dispatch_intent", held_reply)
        preview = await scene("previewGraphRun", [])
        task = asyncio.create_task(scene(
            "executeGraphRun", [], preview["previewToken"], "science-cancel-after-commit",
        ))
        await asyncio.wait_for(committed.wait(), EVENT_DEADLINE_S)
        assert document.nodes[target].state.data["latest"] is not None
        assert await scene("cancelGraphRun", "science-cancel-after-commit") == {"ok": True}
        run = await task
        assert run["status"] == "cancelled"
        assert run["steps"][0]["status"] == "completed"
        assert run["steps"][0]["scientificResult"]["values"]["value (cm)"] == 200
        assert (await scene("getGraphRun"))["steps"][0]["status"] == "completed"

    asyncio.run(scenario())


def test_midrun_scientific_edit_is_reported_as_conflict(tmp_path, monkeypatch):
    from backend.domain.node_states import PluginStateSnapshot

    async def scenario():
        document, bus, scene = open_session(tmp_path / "changed-graph.db", settings_for(tmp_path))
        target = await node(bus, "Scientific Core")
        await configure(bus, "core", target, "convert_unit", {
            "value": 2, "from_unit": "m", "to_unit": "cm",
        })
        started, release = asyncio.Event(), asyncio.Event()

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event,
                             input_units=None, **_extra):
            started.set()
            await release.wait()
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        preview = await scene("previewGraphRun", [])
        task = asyncio.create_task(scene(
            "executeGraphRun", [], preview["previewToken"], "science-midrun-conflict",
        ))
        await asyncio.wait_for(started.wait(), EVENT_DEADLINE_S)
        edited = document.nodes[target]
        previous_data = edited.state.data
        document.record_command(
            "editScientificState", "user",
            lambda: setattr(edited, "state", PluginStateSnapshot.from_data(previous_data)),
            node_ids=(target,),
        )
        release.set()
        run = await task
        assert run["status"] == "partial"
        assert run["steps"][0]["status"] == "failed"
        assert run["steps"][0]["errorType"] == "conflict"
        assert document.nodes[target].state.data["latest"] is None

    asyncio.run(scenario())


def test_revoked_grant_during_graph_calculation_cannot_commit(tmp_path, monkeypatch):
    async def scenario():
        settings = settings_for(tmp_path)
        document, bus, scene = open_session(tmp_path / "revoked-graph.db", settings)
        target = await node(bus, "Scientific Core")
        await configure(bus, "core", target, "convert_unit", {
            "value": 2, "from_unit": "m", "to_unit": "cm",
        })
        entered, release = asyncio.Event(), asyncio.Event()

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event,
                             input_units=None, **_extra):
            entered.set()
            await release.wait()
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        preview = await scene("previewGraphRun", [])
        task = asyncio.create_task(scene(
            "executeGraphRun", [], preview["previewToken"], "science-grant-revoke",
        ))
        await asyncio.wait_for(entered.wait(), EVENT_DEADLINE_S)
        settings.set_plugin_grant("science_core", False)
        release.set()
        run = await task
        assert run["status"] == "partial"
        assert run["steps"][0]["status"] != "completed"
        assert document.nodes[target].state.data["latest"] is None

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["delete", "undo"])
def test_deletion_or_undo_invalidates_an_approved_preview(tmp_path, change):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "preview-stale.db", settings_for(tmp_path))
        target = await node(bus, "Scientific Core")
        await configure(bus, "core", target, "convert_unit", {
            "value": 2, "from_unit": "m", "to_unit": "cm",
        })
        preview = await scene("previewGraphRun", [])
        assert preview["steps"][0]["status"] == "ready"
        if change == "undo":
            assert document.undo()
        else:
            document.remove_nodes([target])
        with pytest.raises(IntentValidationError, match="Preview"):
            await scene("executeGraphRun", [], preview["previewToken"], "science-stale-preview")
        assert not document.graph_runs

    asyncio.run(scenario())


def test_imported_dependency_cycle_is_blocked_then_undo_leaves_unconfigured_leaf(tmp_path, monkeypatch):
    async def scenario():
        document, bus, scene = open_session(tmp_path / "cycle-graph.db", settings_for(tmp_path))
        first = await node(bus, "Scientific Core")
        second = await node(bus, "Scientific Core")
        calls = 0

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event,
                             input_units=None, **_extra):
            nonlocal calls
            calls += 1
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        for item in (first, second):
            await manually_run(bus, "core", item, "convert_unit", {
                "value": 1, "from_unit": "m", "to_unit": "cm",
            })
        # This is only possible in imported legacy graphs; connect() itself
        # correctly rejects cycles in newly edited graphs.
        document.connect_unchecked(first, second)
        document.connect_unchecked(second, first)
        for target, source in ((first, second), (second, first)):
            await configure(bus, "core", target, "convert_unit", {
                "value": 100, "from_unit": "cm", "to_unit": "mm",
            }, {"value": {**ref(document, source), "selector": ["values", "value (cm)"]}})
        preview = await scene("previewGraphRun", [])
        assert len(preview["steps"]) == 2
        assert all(row["status"] == "blocked" and "cycle" in row["detail"] for row in preview["steps"])
        assert calls == 2
        # Undo only the second configuration; its old saved result remains
        # usable as a leaf without silently running the unconfigured source.
        assert document.undo()
        preview = await scene("previewGraphRun", [])
        assert [step["nodeId"] for step in preview["steps"]] == [first]
        assert preview["steps"][0]["sourceIds"] == [second]
        assert preview["steps"][0]["status"] == "ready"
        run = await scene("executeGraphRun", [], preview["previewToken"], "science-leaf-after-undo")
        assert run["status"] == "completed"
        assert calls == 3
        assert document.nodes[second].state.data.get("runner") is None
        assert document.nodes[first].state.data["latest"]["values"]["value (mm)"] == 1000

    asyncio.run(scenario())


def test_scientific_intents_are_not_builder_or_agent_tools():
    registry = discover_plugins()
    science = [
        spec for spec in registry.intents
        if spec.plugin_id.startswith("science_")
        and spec.name in {"configure_runner", "run", "cancel", "table_column", "import_table"}
    ]
    assert science
    assert all(spec.expose_to_builder is False for spec in science)


def test_sqlite_crash_gap_recovers_saved_science_without_replaying(tmp_path, monkeypatch):
    """A durable calculation with a pending receipt is adopted after reopening."""
    import copy

    async def scenario():
        path = tmp_path / "science-crash-gap.db"
        settings = settings_for(tmp_path)
        document, bus, scene = open_session(path, settings)
        target = await node(bus, "Scientific Core")
        await configure(bus, "core", target, "convert_unit", {
            "value": 9, "from_unit": "m", "to_unit": "cm",
        })
        calls = 0

        async def controlled(pack, operation, inputs, sources, *, timeout_s, cancel_event,
                             input_units=None, **_extra):
            nonlocal calls
            calls += 1
            return calculate_result(pack, operation, inputs, sources, input_units=input_units)

        monkeypatch.setattr(workbench, "execute_in_process", controlled)
        original = chat_library.save_chat_atomically_row
        saves = 0

        def crash_between_receipts(db_path, chat_id, title, chat_data, notes_data, pins_data, **kwargs):
            nonlocal saves
            saves += 1
            if saves == 2:
                # Simulate a worker-result autosave that landed just before
                # the Graph Runner step receipt could be marked complete.
                persisted = copy.deepcopy(chat_data)
                step = persisted["graph_runs"][-1]["steps"][0]
                assert step["status"] == "completed"
                step["status"] = "pending"
                step.pop("scientificResult", None)
                step.pop("outputHash", None)
                persisted["graph_runs"][-1]["status"] = "running"
                original(db_path, chat_id, title, persisted, notes_data, pins_data, **kwargs)
                raise OSError("crash between scientific commit and graph receipt")
            return original(db_path, chat_id, title, chat_data, notes_data, pins_data, **kwargs)

        monkeypatch.setattr(chat_library, "save_chat_atomically_row", crash_between_receipts)
        preview = await scene("previewGraphRun", [])
        with pytest.raises(IntentValidationError, match="checkpoint"):
            await scene("executeGraphRun", [], preview["previewToken"], "science-crash-gap")
        assert calls == 1
        chat_id = document.current_chat_id
        assert chat_id is not None
        row = chat_library.load_chat_row(path, chat_id)
        assert row["data"]["graph_runs"][-1]["steps"][0]["status"] == "pending"

        reopened, new_bus, new_scene = open_session(path, settings)
        await new_bus.dispatch_intent("app-chat-library", "loadChat", [chat_id])
        assert (await new_scene("getGraphRun"))["status"] == "interrupted"
        resumed = await new_scene("resumeGraphRun", "science-crash-gap")
        assert resumed["status"] == "completed"
        assert resumed["steps"][0]["scientificResult"]["values"]["value (cm)"] == 900
        assert "not replayed" in resumed["steps"][0]["detail"]
        assert calls == 1

    asyncio.run(scenario())


def test_valid_older_science_version_is_stale_even_when_inputs_match(tmp_path):
    """A verified historical receipt must not freeze an obsolete calculation."""
    from copy import deepcopy

    from backend.domain.node_states import PluginStateSnapshot
    from graphlink_plugins.scientific.core import fingerprint, readable_result

    async def scenario():
        document, bus, scene = open_session(tmp_path / "older-science.db", settings_for(tmp_path))
        target = await node(bus, "Scientific Core")
        fields = {"value": 2, "from_unit": "m", "to_unit": "cm"}
        await manually_run(bus, "core", target, "convert_unit", fields)
        await configure(bus, "core", target, "convert_unit", fields)
        initial = await scene("previewGraphRun", [])
        assert initial["steps"][0]["status"] == "current"

        node_state = document.nodes[target].state.data
        historical = deepcopy(node_state["latest"])
        prior_version = "1.0.0"
        provenance = historical["provenance"]
        provenance["packVersion"] = prior_version
        payload = {key: historical[key] for key in ("summary", "values", "table", "plot", "uncertainty") if key in historical}
        provenance["resultHash"] = fingerprint([
            "core", prior_version, historical["operationId"],
            fingerprint(historical["inputs"]), payload,
        ])
        provenance["receiptHash"] = fingerprint({
            key: val for key, val in provenance.items() if key != "receiptHash"
        })
        revised = {**node_state, "latest": historical}
        assert readable_result(revised, "core")
        document.nodes[target].state = PluginStateSnapshot.from_data(revised)
        preview = await scene("previewGraphRun", [])
        assert preview["steps"][0]["status"] == "ready"

    asyncio.run(scenario())
