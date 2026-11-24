"""MCP nodes resolve actual graph inputs and keep results stable across reopen."""
from __future__ import annotations

import copy
import json

import pytest

import backend.agents  # noqa: F401 - initialize the ordinary canvas import graph
from backend.domain.graph import SceneDocument
from backend.domain.graph_run_history import can_prune_run, restore_graph_runs
from backend.domain.graph_run_inspector import build_graph_run_trace
from backend.domain.graph_runner import (
    bound_dependents, execute_graph_step, plan_graph_run, validate_graph_run_ancestors,
)
from backend.domain.model import SceneError
from backend.domain.node_states import PersistedPluginState, PluginStateSnapshot
from backend.plugin_sdk import HostContext, PluginRegistry
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from graphlink_plugins.mcp_connect import core, plugin

TIME = "2026-11-23T12:00:00+00:00"
TOOL = {"serverId": "server-1", "serverName": "Example", "name": "echo",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
                        "required": ["query"], "additionalProperties": False}}


def node(document, *, tool=None, arguments=None, bindings=None, parent=None):
    result = document.add_plugin_node(
        core.KIND, 100, 100, parent, title="MCP tool", state=PluginStateSnapshot.from_data(core.new_state()),
    )
    core.configure(document, result, tool or TOOL, arguments if arguments is not None else {"query": "hello"}, bindings or {})
    return result


def complete(document, target, *, text="hello", structured=None, error=False):
    prepared = core.prepare(document, target)
    return core.commit_result(
        target, {"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": error},
        request_id="graph-" + "1" * 32, input_hash=prepared.input_hash, sources=prepared.sources,
        started_at=TIME, finished_at=TIME,
    )


def registry():
    host = HostContext("mcp_connect")
    plugin.register(host)
    result = PluginRegistry()
    result.node_kinds.update(host._node_kinds)
    return result


def test_configuration_is_explicit_literal_only_and_preview_does_not_execute():
    document = SceneDocument()
    target = node(document)
    prepared = core.prepare(document, target)
    assert prepared.arguments == {"query": "hello"} and not prepared.current
    step, = plan_graph_run(document, [target.id])
    assert step["kind"] == "tool" and step["operation"] == "tool"
    assert step["sourceId"] == target.id and step["sourceIds"] == [] and step["status"] == "ready"
    assert core.state_data(target)["latest"] is None
    with pytest.raises(SceneError, match="asynchronous"):
        execute_graph_step(document, step)
    digest = complete(document, target)
    assert core.prepare(document, target).current
    assert plan_graph_run(document, [target.id])[0]["status"] == "current"
    assert digest == core.output_hash(target)
    assert core.valid_result(core.state_data(target)["latest"])


def test_text_binding_replaces_literal_and_tracks_changes_without_erasing_result():
    document = SceneDocument()
    source = document.add_note(0, 0)
    source.content = "from a note"
    target = node(document, parent=source.id, arguments={"query": "ignored"},
                  bindings={"query": {"sourceId": source.id, "output": "text", "pointer": ""}})
    before = core.prepare(document, target)
    assert before.arguments == {"query": "from a note"}
    assert before.sources[0]["sourceId"] == source.id
    assert core.dependencies(document, target) == (source.id,)
    complete(document, target, text="saved response")
    source.content = "changed note"
    changed = core.prepare(document, target)
    assert not changed.current and changed.input_hash != before.input_hash
    assert target.content == "saved response"
    assert bound_dependents(document, {source.id}) == [target.id]


def test_mcp_json_pointer_fanin_and_downstream_text_wait_for_actual_results():
    document = SceneDocument()
    first, second = node(document), node(document)
    tool = {**TOOL, "inputSchema": {"type": "object", "required": ["left", "right"], "properties": {
        "left": {"type": "number"}, "right": {"type": "string"},
    }}}
    join = node(document, tool=tool, arguments={})
    document.connect(first.id, join.id)
    document.connect(second.id, join.id)
    core.configure(document, join, tool, {}, {
        "left": {"sourceId": first.id, "output": "json", "pointer": "/items/1/value"},
        "right": {"sourceId": second.id, "output": "text", "pointer": ""},
    })
    note = document.add_note(0, 0)
    document.bind_note_text_input(join.id, note.id)
    plan = plan_graph_run(document, [join.id])
    assert [step["status"] for step in plan] == ["ready", "ready", "waiting", "waiting"]
    assert plan[2]["sourceIds"] == [first.id, second.id]
    complete(document, first, structured={"items": [{"value": 2}, {"value": 9}]})
    complete(document, second, text="right-hand value")
    assert core.prepare(document, join).arguments == {"left": 9, "right": "right-hand value"}
    complete(document, join, text="joined")
    document.run_note_text_input(note.id)
    assert note.content == "joined"
    first_config = core.configuration(first)
    core.configure(document, first, TOOL, {"query": "changed"}, {})
    assert core.config_hash(first) != core.fingerprint(first_config)
    with pytest.raises(SceneError, match="upstream MCP"):
        document.run_note_text_input(note.id)
    assert document._text_input_status(note) == "stale"


def test_current_ancestor_receipt_is_rechecked_before_downstream_write():
    document = SceneDocument()
    source = document.add_note(0, 0)
    source.content = "input"
    target = node(document, parent=source.id, arguments={},
                  bindings={"query": {"sourceId": source.id, "output": "text", "pointer": ""}})
    complete(document, target)
    note = document.add_note(0, 0)
    document.bind_note_text_input(target.id, note.id)
    steps = plan_graph_run(document, [note.id])
    by_id = {step["nodeId"]: step for step in steps}
    validate_graph_run_ancestors(document, steps[1], by_id)
    source.content = "new input"
    with pytest.raises(SceneError, match="upstream input"):
        validate_graph_run_ancestors(document, steps[1], by_id)


@pytest.mark.parametrize("change", ["missing", "disconnected", "pointer", "type", "manual", "receipt"])
def test_broken_sources_and_damaged_outputs_never_authorize_execution(change):
    document = SceneDocument()
    source = document.add_note(0, 0)
    source.content = '{"text":"hello"}'
    target = node(document, parent=source.id, arguments={},
                  bindings={"query": {"sourceId": source.id, "output": "json", "pointer": "/text"}})
    complete(document, target)
    if change == "missing":
        document.nodes.pop(source.id)
    elif change == "disconnected":
        document.edges.clear()
    elif change == "pointer":
        source.content = '{"other":1}'
    elif change == "type":
        source.content = '{"text":12}'
    elif change == "manual":
        target.content = "edited response"
    else:
        damaged = core.state_data(target)
        damaged["latest"]["text"] = "tampered"
        target.state = PluginStateSnapshot.from_data(damaged)
    with pytest.raises(SceneError):
        core.prepare(document, target)
    steps = plan_graph_run(document, [target.id])
    assert steps[0]["status"] == "blocked"


def test_configure_rejects_cycles_atomically_and_errors_stay_in_their_branch():
    document = SceneDocument()
    first = node(document)
    second = node(document, parent=first.id, arguments={},
                  bindings={"query": {"sourceId": first.id, "output": "text", "pointer": ""}})
    prior = first.state
    document.connect_unchecked(second.id, first.id)
    with pytest.raises(SceneError, match="cycle"):
        core.configure(document, first, TOOL, {}, {"query": {"sourceId": second.id, "output": "text", "pointer": ""}})
    assert first.state is prior
    unrelated = node(document)
    document.nodes.pop(first.id)
    steps = {step["nodeId"]: step for step in plan_graph_run(document, [])}
    assert steps[second.id]["status"] == "blocked" and steps[unrelated.id]["status"] == "ready"


def test_schema_fingerprint_and_history_versions_preserve_previous_results():
    document = SceneDocument()
    target = node(document)
    old_hash = core.config_hash(target)
    complete(document, target, text="first")
    first = core.state_data(target)["latest"]
    core.configure(document, target, TOOL, {"query": "second"}, {})
    assert core.config_hash(target) != old_hash and not core.prepare(document, target).current
    complete(document, target, text="second")
    state = core.state_data(target)
    assert state["latest"]["version"] == 2 and state["history"] == [first]
    for index in range(12):
        complete(document, target, text=str(index))
    assert len(core.state_data(target)["history"]) == core.HISTORY_LIMIT
    assert core.state_data(target)["latest"]["version"] == 14
    assert all(core.valid_result(result) for result in core.state_data(target)["history"])


def test_result_history_is_trimmed_before_exceeding_host_persistence_limit(tmp_path):
    from backend.asset_store import AssetStore
    from backend.plugin_sdk import MAX_PLUGIN_STATE_BYTES

    document = SceneDocument()
    target = node(document)
    for index in range(6):
        complete(document, target, text=str(index) + "x" * 140_000)
    state = core.state_data(target)
    assert len(state["history"]) < core.HISTORY_LIMIT
    assert len(target.state.json_text.encode()) <= MAX_PLUGIN_STATE_BYTES
    assert state["latest"]["version"] == 6
    assert core.valid_result(state["latest"])
    # The normal persistence path accepts every committed state, even with
    # history disabled by size pressure. No new storage budget is introduced.
    store = AssetStore(tmp_path / "assets")
    saved = build_chat_data(document, plugin_registry=registry(), asset_store=store)
    descriptor = saved["nodes"][0]["plugin_state_asset"]
    assert json.loads(store.get(descriptor["asset_ref"]))["latest"]["version"] == 6


def test_tool_error_is_a_saved_receipt_but_is_not_a_fresh_source():
    document = SceneDocument()
    target = node(document)
    complete(document, target, text="Service rejected the request", error=True)
    latest = core.state_data(target)["latest"]
    assert core.valid_result(latest) and latest["isError"]
    assert not core.prepare(document, target).current
    with pytest.raises(SceneError, match="upstream MCP"):
        core.source_output(document, target, "text")
    core.set_status(target, "reconciliation-required", "Check the saved request.")
    with pytest.raises(SceneError, match="uncertain"):
        core.prepare(document, target)
    with pytest.raises(SceneError, match="Resolve"):
        core.configure(document, target, TOOL, {}, {})


def test_structured_rows_feed_native_tables_charts_and_reports_and_mark_stale():
    document = SceneDocument()
    target = node(document)
    complete(document, target, structured={"rows": [{"group": "North", "sales": 2}, {"group": "North", "sales": 3}]})
    chart = document.create_table_output(target.id, "chart", "group", "sales")
    report = document.create_table_output(target.id, "artifact", "group", "sales")
    assert chart.state.chart_data["values"] == [5]
    assert "5" in report.state.artifact_content
    assert document._node_wire(chart)["tableInputStatus"] == "current"
    core.configure(document, target, TOOL, {"query": "new"}, {})
    assert document._node_wire(chart)["tableInputStatus"] == "stale"
    assert [step["status"] for step in plan_graph_run(document, [chart.id])] == ["ready", "waiting"]
    complete(document, target, structured={"rows": [{"group": "North", "sales": 7}]})
    plan = plan_graph_run(document, [chart.id])
    execute_graph_step(document, plan[1])
    assert chart.state.chart_data["values"] == [7]


def test_bound_table_checks_all_ancestors_before_resolving_arguments():
    document = SceneDocument()
    source = document.add_table(0, 0, "group,value\nA,2")
    first = document.create_table_output(source.id, "table", "group", "value")
    second = document.create_table_output(first.id, "table", "group", "value")
    tool = {**TOOL, "inputSchema": {"type": "object", "properties": {"query": {"type": "array"}}, "required": ["query"]}}
    target = node(document, tool=tool, arguments={}, parent=second.id,
                  bindings={"query": {"sourceId": second.id, "output": "table", "pointer": ""}})
    complete(document, target)
    assert core.prepare(document, target).current
    document.set_table_source(source.id, "group,value\nA,8", source.content)
    with pytest.raises(SceneError, match="upstream table"):
        core.prepare(document, target)
    document.run_table_output(first.id)
    with pytest.raises(SceneError, match="upstream table"):
        core.prepare(document, target)
    document.run_table_output(second.id)
    assert not core.prepare(document, target).current


def test_unsupported_table_shapes_and_invalid_output_schema_fail_before_overwrite():
    document = SceneDocument()
    target = node(document)
    complete(document, target, structured={"rows": [{"nested": {"value": 2}}]})
    with pytest.raises(SceneError, match="scalar"):
        core.table_rows(target)
    core.configure(document, target, {**TOOL, "outputSchema": {"type": "object", "required": ["answer"]}}, {"query": "new"}, {})
    before = target.state
    with pytest.raises(SceneError, match="required"):
        complete(document, target, structured={"other": 1})
    assert target.state is before


def test_save_reopen_remaps_note_and_tool_bindings_without_changing_receipt_hashes(tmp_path):
    document = SceneDocument()
    unused = document.add_note(0, 0)
    document.remove_nodes([unused.id])
    source = document.add_note(0, 0)
    source.content = "input"
    target = node(document, parent=source.id, arguments={},
                  bindings={"query": {"sourceId": source.id, "output": "text", "pointer": ""}})
    complete(document, target, structured={"rows": [{"group": "A", "value": 8}]})
    child = node(document, parent=target.id, arguments={},
                 bindings={"query": {"sourceId": target.id, "output": "text", "pointer": ""}})
    complete(document, child)
    chart = document.create_table_output(target.id, "chart", "group", "value")
    before = {core.state_data(item)["nodeKey"]: (core.config_hash(item), core.output_hash(item), core.prepare(document, item).input_hash)
              for item in (target, child)}
    data = build_chat_data(document, plugin_registry=registry())
    notes, pins = data.pop("notes_data"), data.pop("pins_data")
    reopened = SceneDocument()
    restore_chat_into_document(reopened, {"data": data}, notes, pins, plugin_registry=registry())
    tools = [item for item in reopened.nodes.values() if item.kind == core.KIND]
    assert len(tools) == 2 and {item.id for item in tools} != {target.id, child.id}
    for item in tools:
        state = core.state_data(item)
        assert (core.config_hash(item), core.output_hash(item), core.prepare(reopened, item).input_hash) == before[state["nodeKey"]]
        assert core.prepare(reopened, item).current and core.valid_result(state["latest"])
    steps = plan_graph_run(reopened, [])
    assert all(step["status"] == "current" for step in steps)
    reopened_chart = next(item for item in reopened.nodes.values() if item.kind == "chart")
    assert reopened_chart.id != chart.id and reopened_chart.state.table_output.source_id in {item.id for item in tools}


@pytest.mark.parametrize("status,expected", [("connecting", "idle"), ("awaiting-approval", "idle"), ("running", "reconciliation-required")])
def test_restored_transient_status_never_authorizes_an_ambiguous_retry(status, expected):
    document = SceneDocument()
    target = node(document)
    core.set_status(target, status)
    core.remap_sources(document, {target.id: target.id})
    assert core.state_data(target)["status"] == expected


def test_missing_assets_and_damaged_versions_are_visible_and_retained_for_recovery():
    document = SceneDocument()
    target = node(document)
    target.state = PersistedPluginState(unresolved_asset_ref="a" * 64)
    wire = json.loads(plugin.serialize(target)["mcp"])
    assert wire["status"] == "error" and "asset" in wire["error"]
    damaged = {**core.new_state(), "version": 22}
    target.state = plugin.deserialize(damaged)
    assert plugin.serialize_saved(target) == damaged
    with pytest.raises(SceneError, match="unsupported"):
        core.state_data(target)


def test_saved_tool_step_fanin_and_known_error_receipt_restore_without_replay():
    document = SceneDocument()
    target = node(document)
    complete(document, target, error=True)
    step, = plan_graph_run(document, [])
    step.update(status="failed", outputHash=core.output_hash(target), errorType="tool_error", endpointHash="f" * 64,
                externalAttempt={"requestId": "graph-" + "1" * 32, "kind": "tool", "provider": "mcp:server-1",
                                 "model": "", "tool": "echo", "inputRef": "input:" + step["inputHash"],
                                 "inputHash": step["inputHash"], "phase": "completed"})
    run = {"runId": "tool-run-1", "selectionHash": "b" * 64, "previewHash": "c" * 64, "startedAt": TIME,
           "status": "partial", "steps": [step]}
    restored, = restore_graph_runs([run], {target.id: "new-target"})
    recovered = restored["steps"][0]
    assert recovered["status"] == "failed" and recovered["sourceIds"] == []
    assert recovered["sourceId"] == "new-target" and recovered["endpointHash"] == "f" * 64
    trace = build_graph_run_trace(restored)
    assert trace["steps"][0]["sourceNodeIds"] == []
    assert trace["steps"][0]["attempts"][0]["phase"] == "completed"
    wrong = copy.deepcopy(run)
    wrong["steps"][0]["errorType"] = "untrusted_error"
    assert restore_graph_runs([wrong], {target.id: "new-target"})[0]["steps"][0]["status"] == "reconciliation-required"


@pytest.mark.parametrize("status,phase", [("completed", "completed"), ("failed", "completed"),
                                         ("running", "dispatched"), ("reconciliation-required", "reconciliation-required")])
def test_deleted_historical_source_does_not_reclassify_known_tool_receipts(status, phase):
    from backend.domain.graph_run_external import ExternalCall

    document = SceneDocument()
    source = document.add_note(0, 0)
    source.content = "input"
    target = node(document, arguments={}, parent=source.id,
                  bindings={"query": {"sourceId": source.id, "output": "text", "pointer": ""}})
    complete(document, target, error=status == "failed")
    step, = plan_graph_run(document, [target.id])
    attempt = ExternalCall(kind="tool", provider_name="mcp:server-1", tool="echo",
                           input_ref="input:" + step["inputHash"], input_hash=step["inputHash"]).journal_record()
    attempt["phase"] = phase
    step.update(status=status, outputHash=core.output_hash(target), externalAttempt=attempt)
    if status == "failed":
        step["errorType"] = "tool_error"
    run = {"runId": "tool-deleted-source", "selectionHash": "b" * 64, "previewHash": "c" * 64,
           "startedAt": TIME, "status": "partial", "steps": [step]}
    recovered = restore_graph_runs([run], {target.id: "new-target"})[0]["steps"][0]
    assert recovered["sourceIds"] == [""] and recovered["sourceId"] == ""
    assert recovered["status"] == (status if phase == "completed" else "reconciliation-required")
    assert recovered["externalAttempt"] == attempt


@pytest.mark.parametrize("run_status", ["completed", "partial", "cancelled"])
def test_history_retention_preserves_uncertain_fences_even_when_run_status_is_finished(run_status):
    from backend.domain.graph_run_external import ExternalCall

    attempt = ExternalCall(kind="tool", provider_name="mcp:server-1", tool="echo", input_ref="input:1", input_hash="a" * 64).journal_record()
    run = {"status": run_status, "steps": [{"status": "cancelled", "externalAttempt": attempt}]}
    assert not can_prune_run(run)
    attempt["phase"] = "reconciliation-required"
    assert not can_prune_run(run)
    attempt["phase"] = "completed"
    assert can_prune_run(run)
    attempt["phase"] = "reconciled"
    assert not can_prune_run(run)
    attempt["requestId"] = "invalid-request"
    assert not can_prune_run(run)
    run["steps"] = [{"status": "reconciliation-required"}]
    assert not can_prune_run(run)
    run["steps"] = [{"status": "running"}]
    assert not can_prune_run(run)
    run["steps"] = [{"status": "failed"}]
    assert can_prune_run(run)
    run["status"] = "interrupted"
    assert not can_prune_run(run)


def test_acknowledged_refresh_barrier_requires_a_later_successful_receipt_before_pruning():
    from backend.domain.graph_run_external import ExternalCall

    attempt = ExternalCall(kind="tool", provider_name="mcp:server-1", tool="echo", input_ref="input:1", input_hash="a" * 64).journal_record()
    attempt["phase"] = "reconciled"
    step = {"kind": "tool", "nodeId": "target", "status": "failed", "startedAt": TIME, "finishedAt": TIME,
            "externalAttempt": attempt}
    run = {"status": "partial", "steps": [step]}
    later = {"kind": "tool", "nodeId": "target", "status": "completed", "inputHash": "a" * 64,
             "outputHash": "b" * 64, "startedAt": "2026-11-23T12:01:00+00:00", "finishedAt": "2026-11-23T12:01:01+00:00",
             "externalAttempt": {**attempt, "phase": "completed", "requestId": "graph-" + "2" * 32}}
    history = [run, {"status": "completed", "steps": [later]}]
    assert can_prune_run(run, history)
    for changes in ({"nodeId": "other"}, {"status": "failed", "errorType": "tool_error"},
                    {"errorType": "tool_error"}, {"startedAt": TIME}, {"inputHash": "c" * 64}, {"outputHash": ""}):
        assert not can_prune_run(run, [run, {"steps": [{**later, **changes}]}])


def test_acknowledged_barrier_survives_history_pressure_and_undo_to_older_output(tmp_path, monkeypatch):
    import asyncio

    from backend.mcp_client import McpError
    from backend.tests.test_mcp_connect_intents import add_tool, open_session, run_tool, setup

    async def scenario():
        settings, server = setup(tmp_path, monkeypatch)
        document, bus, scene = open_session(tmp_path / "acknowledged-history.db", settings)
        node_id = await add_tool(bus, scene)
        first, _ = await run_tool(scene, node_id, "known-before-acknowledgment")
        assert first["status"] == "completed"
        undo_position = len(document.command_log)

        def fail(_name, _arguments):
            raise McpError("Remote outcome is unknown")

        server.on_call = fail
        uncertain, _ = await run_tool(scene, node_id, "unknown-before-acknowledgment")
        request_id = uncertain["steps"][0]["externalAttempt"]["requestId"]
        await scene("reconcileMcpTool", node_id, request_id)
        server.on_call = None
        other_id = await add_tool(bus, scene, "Unrelated output")
        for index in range(13):
            completed, _ = await run_tool(scene, other_id, "ack-history-pressure-" + str(index))
            assert completed["status"] == "completed"
        attempts = await scene("getMcpToolAttempts", node_id)
        assert any(item["requestId"] == request_id and item["phase"] == "reconciled" for item in attempts)
        while len(document.command_log) > undo_position:
            document.undo()
        document.undo()
        document.redo()
        restored = document.nodes[node_id]
        assert core.state_data(restored)["latest"]["version"] == 1
        assert not core.state_data(restored).get("requiresRefresh", False)
        assert not core.prepare(document, restored).current
        assert (await scene("previewGraphRun", [node_id]))["steps"][0]["status"] == "ready"
        assert len(server.calls) == 15

    asyncio.run(scenario())
