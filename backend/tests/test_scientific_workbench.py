"""Exercise the same grants, picker, typed intent and saved graph that the SPA uses."""

import asyncio
import json
import shutil
import tomllib

import pytest

from backend.canvas import SceneDocument
from backend.domain.graph_runner import plan_graph_run
from backend.events import SessionBus
from backend.notifications import NotificationState
from backend.plugin_sdk import DEFAULT_PLUGINS_ROOT, discover_plugins
from backend.plugins import plugins_payload, register_plugin_tools, register_plugins
from backend.session_load import restore_chat_into_document
from backend.session_save import build_chat_data
from backend.tools import ToolRegistry
from graphlink_plugins.scientific.catalog import PACKS, operations
from graphlink_plugins.scientific.core import Citation, Field, MAX_POINTS, Operation, VERSION, run_operation
from graphlink_settings_store import SettingsManager


@pytest.fixture
def session(tmp_path):
    # An isolated registry keeps grants and discovery out of the process-wide
    # bundled-plugin registry used by unrelated settings and picker tests.
    root = tmp_path / "plugins"
    for pack in PACKS:
        directory = "science_" + pack
        shutil.copytree(DEFAULT_PLUGINS_ROOT / directory, root / directory)
    registry = discover_plugins(root)
    settings = SettingsManager(tmp_path / "session.dat")
    document = SceneDocument()
    bus = SessionBus("science-tests")
    notice = NotificationState()
    bus.register_topic("notification", notice.payload)
    bus.register_topic("scene", document.scene_payload)
    register_plugins(bus, notice, document, settings, plugin_registry=registry)
    return document, bus, registry, settings


def dispatch(bus, name, *args):
    return asyncio.run(bus.dispatch_intent("app-plugins", name, list(args)))


def grant(bus, pack):
    dispatch(bus, "setPluginGrant", "science_" + pack, True)


def run(bus, pack, node_id, op, inputs, **kwargs):
    return dispatch(bus, "invokePluginIntent", "science_" + pack, "run",
                    {"node_id": node_id, "operation_id": op, "inputs": inputs, **kwargs})


def envelope(document, node_id):
    return json.loads(document._plugin_state_wire(document.nodes[node_id])["science"])


def test_manifest_to_canvas_and_run_for_every_example(session):
    document, bus, registry, settings = session
    assert not registry.node_kinds
    assert not registry.intents
    grants = plugins_payload(registry, settings)["grants"]
    assert {row["pluginId"] for row in grants} == {"science_" + pack for pack in PACKS}
    assert all(not row["granted"] for row in grants)
    for pack, (title, _) in PACKS.items():
        with (DEFAULT_PLUGINS_ROOT / ("science_" + pack) / "plugin.toml").open("rb") as f:
            manifest = tomllib.load(f)
        assert manifest["plugin"]["version"] == VERSION
        grant(bus, pack)
        node_id = dispatch(bus, "executePlugin", title, None, 110, 90)
        node = document.nodes[node_id]
        if len(document.nodes) == 1:
            assert (node.x, node.y) == (110, 90)
        else:
            # Same viewport centre, so each later pack steps clear to the right.
            assert node.y == 90 and node.x > 110
        state = envelope(document, node_id)
        assert state["latest"] is None
        assert [o["id"] for o in state["operations"]] == [o.id for o in operations(pack)]
        for op in state["operations"]:
            assert run(bus, pack, node_id, op["id"], op["example"]) == {"ok": True}
            result = envelope(document, node_id)["latest"]
            assert result["operationId"] == op["id"] and result["provenance"]["packVersion"] == VERSION
            assert result["summary"] == node.content
            assert len(result["provenance"]["resultHash"]) == 64
    rects = [(n.x, n.y, *document.node_footprint(n)) for n in document.nodes.values()]
    for index, (ax, ay, aw, ah) in enumerate(rects):
        for bx, by, bw, bh in rects[index + 1:]:
            assert ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay
    assert plan_graph_run(document, list(document.nodes)) == []
    assert register_plugin_tools(ToolRegistry(), registry, settings, document) == ()
    categories = plugins_payload(registry, settings)["categories"]
    assert len(categories) == 1 and categories[0]["name"] == "Scientific Workbench"
    assert len(categories[0]["plugins"]) == len(PACKS)


def test_csv_to_statistics_provenance_undo_grant_and_reopen(session):
    document, bus, registry, settings = session
    grant(bus, "core")
    grant(bus, "statistics")
    # Leave a gap so IDs are genuinely reminted (n2 -> n1) on reopen.
    document.add_note(0, 0)
    document.remove_nodes(list(document.nodes))
    source = dispatch(bus, "executePlugin", "Scientific Core")
    text = 'Station,"Temperature, °C"\n1,8\n2,10\n3,18'
    assert run(bus, "core", source, "numeric_csv", {"csv": text})["ok"]
    snapshot = envelope(document, source)
    ref = {"nodeId": source, "sourceKey": snapshot["nodeKey"],
           "resultHash": snapshot["latest"]["provenance"]["resultHash"], "selector": ["column", 1]}
    node_id = dispatch(bus, "executePlugin", "Statistics", source)
    def calculate(inputs, **kwargs):
        return run(bus, "statistics", node_id, "describe", {"observations": inputs}, **kwargs)
    assert calculate([8, 10, 18], sources={"observations": ref})["ok"]
    initial = envelope(document, node_id)["latest"]
    assert initial["values"]["mean"] == 12
    assert initial["provenance"]["sources"]["observations"]["sourceKey"] == snapshot["nodeKey"]
    commands = len(document.command_log)
    assert calculate([8, 10, 18], sources={"observations": ref})["ok"]
    assert not calculate([8, 10, 19], sources={"observations": ref})["ok"]
    assert not calculate([8])["ok"]
    assert not calculate([8, 10, 18], sources={"observations": {**ref, "resultHash": "old"}})["ok"]
    assert not run(bus, "core", node_id, "numeric_csv", {"csv": text})["ok"]
    assert envelope(document, node_id)["latest"] == initial
    assert len(document.command_log) == commands
    document.undo()
    assert envelope(document, node_id)["latest"] is None
    document.redo()
    assert envelope(document, node_id)["latest"] == initial
    saved = build_chat_data(document, plugin_registry=registry, settings_manager=settings)
    restore_chat_into_document(document, {"data": saved}, [], [], plugin_registry=registry, settings_manager=settings)
    source, node_id = document.nodes
    assert source != ref["nodeId"]
    assert envelope(document, node_id)["latest"] == initial
    assert calculate([8, 10, 18], sources={"observations": ref})["ok"]
    assert envelope(document, node_id)["latest"] == initial
    dispatch(bus, "setPluginGrant", "science_core", False)
    assert not calculate([8, 10, 18], sources={"observations": ref})["ok"]
    dispatch(bus, "setPluginGrant", "science_statistics", False)
    assert calculate([8, 10, 18]) is None


@pytest.mark.parametrize("op,inputs", [
    ("convert_unit", {"value": -1, "from_unit": "K", "to_unit": "°C"}),
    ("convert_unit", {"value": 1, "from_unit": "kg", "to_unit": "m"}),
    ("convert_unit", {"value": True, "from_unit": "s", "to_unit": "min"}),
    ("convert_unit", {"value": float("inf"), "from_unit": "s", "to_unit": "min"}),
    ("numeric_csv", {"csv": "a,a\n1,2"}),
    ("numeric_csv", {"csv": "a,b\n1"}),
    ("numeric_csv", {"csv": "a,b\nNaN,1"}),
    ("numeric_csv", {"csv": 'a\n"unterminated'}),
    ("numeric_csv", {"csv": "a\n" + "1\n" * (MAX_POINTS + 1)}),
    ("plot_xy", {"x": [0, 1], "y": [1], "x_label": "X", "y_label": "Y"}),
    ("plot_xy", {"x": [0], "y": [1], "x_label": "x", "y_label": "y", "extra": 1}),
])
def test_core_rejects_invalid_measurements(op, inputs):
    operation = next(o for o in operations("core") if o.id == op)
    with pytest.raises(ValueError):
        run_operation("core", operation, inputs)


def test_unit_conversion_and_data_bounds():
    convert = next(o for o in operations("core") if o.id == "convert_unit")
    assert run_operation("core", convert, convert.example)["values"]["value (K)"] == 298.15
    solve = next(o for o in operations("mathematics") if o.id == "linear_system")
    for invalid_matrix in ([[1], [1, 2]], [[True]], [[1] * 33], [1]):
        with pytest.raises(ValueError):
            run_operation("mathematics", solve, {"matrix": invalid_matrix, "rhs": [1]})


def test_described_fields_name_the_sibling_that_sets_their_unit():
    def described(pack, operation_id):
        op = next(o for o in operations(pack) if o.id == operation_id)
        return {f["key"]: f for f in op.describe()["fields"]}

    convert = described("core", "convert_unit")
    assert convert["value"]["unit_field"] == "from_unit"
    assert "unit_field" not in convert["from_unit"]
    # Every advertised setter is a real text input with a usable example value.
    for pack in PACKS:
        for op in operations(pack):
            keys = {f.key: f for f in op.fields}
            for field in op.describe()["fields"]:
                if "unit_field" in field:
                    assert keys[field["unit_field"]].kind == "text"
                    assert isinstance(op.example.get(field["unit_field"]), str)


def test_choice_fields_and_operation_metadata_are_validated_at_the_python_boundary():
    field = Field("mode", "Assay mode", "choice", choices=("EC50", "IC50"))
    citation = Citation("Method paper", "https://doi.org/10.1002/pst.426")
    operation = Operation(
        "assay_mode", "Assay mode", "Test allowed categorical inputs", (field,),
        {"mode": "EC50"},
        lambda data: {"summary": data["mode"], "values": {"selected": data["mode"]}},
        methods="Categorical mode selects the interpretation, not the computation.",
        citations=(citation,),
    )
    described = operation.describe()
    assert described["fields"][0]["choices"] == ("EC50", "IC50")
    assert described["methods"] == operation.methods
    assert described["citations"] == [{"label": citation.label, "url": citation.url}]
    assert run_operation("biology", operation, {"mode": "IC50"})["values"]["selected"] == "IC50"
    for invalid in ("ec50", "Other", "", 1, None):
        with pytest.raises(ValueError, match="must be one of"):
            run_operation("biology", operation, {"mode": invalid})
    with pytest.raises(ValueError, match="exactly the inputs"):
        run_operation("biology", operation, {"mode": "EC50", "extra": "no"})
    for options in ((), ("EC50", "EC50"), (" EC50", "IC50"), (1, "IC50")):
        with pytest.raises(ValueError, match="Choice fields require"):
            Field("mode", "Assay mode", "choice", choices=options)
    with pytest.raises(ValueError, match="Only choice fields"):
        Field("mode", "Assay mode", "text", choices=("EC50",))
    with pytest.raises(ValueError, match="Example for mode"):
        Operation("broken", "Broken", "", (field,), {"mode": "invalid"}, lambda _: {})
    with pytest.raises(ValueError, match="HTTPS URL"):
        Citation("Unsafe", "javascript:alert(1)")
    # Non-choice catalog entries still use the original wire shape.
    assert "choices" not in operations("biology")[0].describe()["fields"][0]
    assert "methods" not in operations("core")[0].describe()


def test_damaged_receipt_is_visible_and_preserved(session):
    document, bus, _, _ = session
    grant(bus, "core")
    node = dispatch(bus, "executePlugin", "Scientific Core")
    assert run(bus, "core", node, "convert_unit", operations("core")[1].example)["ok"]
    saved = build_chat_data(document, plugin_registry=session[2], settings_manager=session[3])
    saved["nodes"][0]["plugin_state"]["latest"]["values"]["value (K)"] = 9001
    restore_chat_into_document(document, {"data": saved}, [], [], plugin_registry=session[2], settings_manager=session[3])
    node = next(iter(document.nodes))
    assert "damaged" in envelope(document, node)["error"]
    assert not run(bus, "core", node, "convert_unit", operations("core")[1].example)["ok"]
    again = build_chat_data(document, plugin_registry=session[2], settings_manager=session[3])
    assert again["nodes"][0]["plugin_state"] == saved["nodes"][0]["plugin_state"]


def test_export_and_optimistic_edits_are_bound_to_the_visible_run(session):
    document, bus, _, _ = session
    grant(bus, "core")
    node = dispatch(bus, "executePlugin", "Scientific Core")
    other = dispatch(bus, "executePlugin", "Scientific Core")
    initial = envelope(document, node)["runToken"]
    assert initial != envelope(document, other)["runToken"]
    example = operations("core")[1].example
    assert run(bus, "core", node, "convert_unit", example, expected_run=initial)["ok"]
    latest = envelope(document, node)
    assert latest["runToken"] != initial
    assert run(bus, "core", node, "convert_unit", {**example, "value": 77}, expected_run=initial)["conflict"]
    assert envelope(document, node) == latest
    def export(token):
        return dispatch(bus, "invokePluginIntent", "science_core", "export_result",
                        {"node_id": node, "expected_run": token})
    assert export(initial)["conflict"]
    encoded = export(latest["runToken"])["json"]
    assert '"value":25.0' in encoded
    assert json.loads(encoded) == latest["latest"]
    assert envelope(document, node) == latest


def test_receipt_integrity_legacy_data_and_structure():
    import copy
    from graphlink_plugins.scientific.core import fingerprint, readable_result
    op = operations("core")[1]
    latest = run_operation("core", op, op.example)
    def valid(result):
        return readable_result({"version": 1, "latest": result}, "core")
    for key, value in {"createdAt": "2024-02-03T04:05:06+00:00", "sources": {"value": "broken"},
                       "packVersion": "9.8.7", "receiptHash": "0" * 64}.items():
        changed = copy.deepcopy(latest)
        changed["provenance"][key] = value
        assert not valid(changed)
    damaged = copy.deepcopy(latest)
    del damaged["provenance"]["receiptHash"]
    assert not valid(damaged)
    legacy = copy.deepcopy(damaged)
    receipt = legacy["provenance"]
    receipt["packVersion"] = "1.0.0"
    outputs = {k: legacy[k] for k in ("values", "summary")}
    receipt["resultHash"] = fingerprint(["core", "1.0.0", op.id, receipt["inputHash"], outputs])
    assert valid(legacy)
    for bad_time in ("yesterday", "", "2024-02-03T04:05:06"):
        receipt["createdAt"] = bad_time
        assert not valid(legacy)


def test_equal_numbers_from_a_new_lineage_do_not_reuse_an_old_citation(session):
    document, bus, _, _ = session
    grant(bus, "core")
    grant(bus, "statistics")
    parents = [dispatch(bus, "executePlugin", "Scientific Core") for _ in range(2)]
    middle = dispatch(bus, "executePlugin", "Statistics", parents[0])
    document.connect(parents[1], middle)
    leaf = dispatch(bus, "executePlugin", "Scientific Core", middle)
    def source(node, selector):
        state = envelope(document, node)
        receipt = state["latest"]["provenance"]
        return {"nodeId": node, "sourceKey": state["nodeKey"], "resultHash": receipt["resultHash"],
                "receiptHash": receipt["receiptHash"], "selector": selector}
    def analyze(parent):
        return run(bus, "statistics", middle, "describe", {"observations": [8, 12]},
                   sources={"observations": source(parent, ["column", 0])})
    for parent in parents:
        assert run(bus, "core", parent, "numeric_csv", {"csv": "T\n8\n12"})["ok"]
    assert analyze(parents[0])["ok"]
    citation = source(middle, ["values", "mean"])
    inputs = {"value": 10, "from_unit": "m", "to_unit": "cm"}
    assert run(bus, "core", leaf, "convert_unit", inputs, sources={"value": citation})["ok"]
    assert analyze(parents[1])["ok"]
    replacement = source(middle, ["values", "mean"])
    assert replacement["resultHash"] == citation["resultHash"]
    assert replacement["receiptHash"] != citation["receiptHash"]
    assert not run(bus, "core", leaf, "convert_unit", inputs, sources={"value": citation})["ok"]
    assert run(bus, "core", leaf, "convert_unit", inputs, sources={"value": replacement})["ok"]
    assert envelope(document, leaf)["latest"]["provenance"]["sources"]["value"]["receiptHash"] == replacement["receiptHash"]


def test_cold_disabled_pack_preserves_nodes_edges_and_data_then_regrants(session, tmp_path):
    from backend.asset_store import AssetStore
    document, bus, registry, settings = session
    grant(bus, "core")
    grant(bus, "statistics")
    source = dispatch(bus, "executePlugin", "Scientific Core")
    target = dispatch(bus, "executePlugin", "Statistics", source)
    assert run(bus, "core", source, "numeric_csv", {"csv": "a\n" + "\n".join(map(str, range(1024)))})["ok"]
    latest = envelope(document, source)
    store = AssetStore(tmp_path / "assets")
    saved = build_chat_data(document, asset_store=store, plugin_registry=registry, settings_manager=settings)
    descriptor = saved["nodes"][0]["plugin_state_asset"]
    dispatch(bus, "setPluginGrant", "science_core", False)
    fresh_root = tmp_path / "cold-plugins"
    shutil.copytree(tmp_path / "plugins", fresh_root)
    cold = discover_plugins(fresh_root)
    assert "science_core.workbench" not in cold.node_kinds
    restored = SceneDocument()
    notice = NotificationState()
    new_bus = SessionBus("cold")
    new_bus.register_topic("notification", notice.payload)
    new_bus.register_topic("scene", restored.scene_payload)
    register_plugins(new_bus, notice, restored, settings, plugin_registry=cold)
    restore_chat_into_document(restored, {"data": saved}, [], [], store, plugin_registry=cold, settings_manager=settings)
    assert restored.unrestored_node_count == 0
    by_kind = {node.kind: node for node in restored.nodes.values()}
    assert {edge.target for edge in restored.edges.values()} == {by_kind["science_statistics.workbench"].id}
    restored_source = by_kind["science_core.workbench"]
    assert restored._plugin_state_wire(restored_source) == {}
    assert "science_core" not in cold.loaded_plugin_ids
    assert build_chat_data(restored, asset_store=store, plugin_registry=cold, settings_manager=settings)["nodes"][0]["plugin_state_asset"] == descriptor
    grant(new_bus, "core")
    assert envelope(restored, restored_source.id) == latest
    assert target in document.nodes


@pytest.mark.parametrize("broken", [None, "missing", {"asset_ref": "mistyped-checksum"}])
def test_invalid_asset_descriptor_survives_repeated_saves(session, broken):
    document, bus, registry, settings = session
    grant(bus, "core")
    node = dispatch(bus, "executePlugin", "Scientific Core")
    saved = build_chat_data(document, plugin_registry=registry, settings_manager=settings)
    record = saved["nodes"][0]
    record.pop("plugin_state")
    record["plugin_state_asset"] = broken
    restore_chat_into_document(document, {"data": saved}, [], [], plugin_registry=registry, settings_manager=settings)
    node = next(iter(document.nodes))
    assert envelope(document, node)["error"]
    assert not run(bus, "core", node, "convert_unit", operations("core")[1].example)["ok"]
    again = build_chat_data(document, plugin_registry=registry, settings_manager=settings)
    assert again["nodes"][0]["plugin_state_asset"] == broken


@pytest.mark.parametrize("change", ["revoke", "overwrite", "disconnect", "revoke_source", "delete"])
def test_work_in_flight_cannot_commit_over_a_changed_graph(session, monkeypatch, change):
    from backend.domain.node_states import PluginStateSnapshot
    from graphlink_plugins.scientific import workbench
    from graphlink_plugins.scientific.core import calculate_result

    document, bus, _, _ = session
    grant(bus, "core")
    grant(bus, "statistics")
    source = dispatch(bus, "executePlugin", "Scientific Core")
    target = dispatch(bus, "executePlugin", "Statistics", source)
    run(bus, "core", source, "numeric_csv", {"csv": "x\n1\n2"})
    state = envelope(document, source)
    ref = {"nodeId": source, "sourceKey": state["nodeKey"], "selector": ["column", 0],
           "resultHash": state["latest"]["provenance"]["resultHash"]}
    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def paused(*args, **_kwargs):
            if args[2]["observations"] == [1, 2]:
                entered.set()
                await finish.wait()
            return calculate_result(*args)

        # The stale-state guard is independent of the worker IPC mechanism.
        # Hold the isolated-call seam without spawning a second process.
        monkeypatch.setattr(workbench, "execute_in_process", paused)

        async def submit(values, sources):
            return await bus.dispatch_intent("app-plugins", "invokePluginIntent", [
                "science_statistics", "run", {"node_id": target, "operation_id": "describe",
                "inputs": {"observations": values}, "sources": sources}])
        task = asyncio.create_task(submit([1, 2], {"observations": ref}))
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            if change in ("revoke", "revoke_source"):
                pack = "science_core" if change == "revoke_source" else "science_statistics"
                await bus.dispatch_intent("app-plugins", "setPluginGrant", [pack, False])
            elif change == "overwrite":
                # A new run on this node is now rejected as busy. Model an
                # independent undoable user edit to its saved state instead.
                node = document.nodes[target]
                new_result = calculate_result(
                    "statistics", next(op for op in operations("statistics") if op.id == "describe"),
                    {"observations": [10.0, 20.0]}, None,
                )
                key = node.state.data["nodeKey"]

                def overwrite():
                    node.state = PluginStateSnapshot.from_data(
                        {"version": 1, "nodeKey": key, "latest": new_result})
                    node.content = new_result["summary"]

                document.record_command("science:statistics:describe", "user", overwrite, node_ids=[target])
            elif change == "delete":
                document.remove_nodes([target])
            else:
                document.remove_edges(list(document.edges))
        finally:
            finish.set()
        assert not (await task)["ok"]
    asyncio.run(scenario())
    if target in document.nodes:
        latest = document.nodes[target].state.data["latest"]
        if change == "overwrite":
            assert latest["values"]["mean"] == 15
        else:
            assert latest is None


def test_repeated_renders_validate_immutable_state_once_and_hide_it_on_revoke(session, monkeypatch):
    from graphlink_plugins.scientific import workbench

    document, bus, _, _ = session
    grant(bus, "core")
    node = dispatch(bus, "executePlugin", "Scientific Core")
    assert run(bus, "core", node, "convert_unit", operations("core")[1].example)["ok"]
    state = document.nodes[node].state
    altered_copy = state.data
    altered_copy["latest"]["values"].clear()
    real_check = workbench.readable_result
    calls = []
    def checked(*args):
        calls.append(1)
        return real_check(*args)
    monkeypatch.setattr(workbench, "readable_result", checked)
    for _ in range(4):
        assert envelope(document, node)["latest"]["values"]["value (K)"] == 298.15
    assert len(calls) <= 1
    dispatch(bus, "setPluginGrant", "science_core", False)
    assert document._plugin_state_wire(document.nodes[node]) == {}



def test_chart_this_result_is_connected_receipted_refreshable_and_undoable(session):
    from backend.api.intents_chart import register_chart_intents

    document, bus, registry, settings = session

    class NoExtractionAgent:
        async def start_chart_generation(self, **kwargs):
            raise AssertionError("A scientific chart must never use model extraction")

    register_chart_intents(bus, document, NotificationState(), NoExtractionAgent())
    grant(bus, "core")
    source = dispatch(bus, "executePlugin", "Scientific Core")
    samples = {"x": [0, 1, 2], "y": [1, 2, 4], "x_label": "Time (s)", "y_label": "Distance (m)"}
    assert run(bus, "core", source, "plot_xy", samples)["ok"]
    first_receipt = envelope(document, source)["latest"]["provenance"]
    chart_id = asyncio.run(bus.dispatch_intent("scene", "generateChart", [source, "scatter"]))
    chart = document.nodes[chart_id]
    assert chart.kind == "chart"
    assert chart.state.chart_type == "scatter"
    assert chart.state.chart_source_node_id == source
    assert any(edge.source == source and edge.target == chart_id for edge in document.edges.values())
    assert chart.state.chart_data["xUnit"] == "s"
    assert chart.state.chart_data["yUnit"] == "m"
    assert chart.state.chart_data["scienceReceipt"] == {
        "sourceKey": envelope(document, source)["nodeKey"],
        "resultHash": first_receipt["resultHash"], "receiptHash": first_receipt["receiptHash"],
    }
    assert document.scene_payload()["nodes"][-1]["chartData"]["scienceReceipt"]["receiptHash"] == first_receipt["receiptHash"]

    # A duplicate run is a no-op. A changed run changes the receipt, but
    # retains the old plotted data until this exact chart is refreshed.
    assert run(bus, "core", source, "plot_xy", samples)["ok"]
    assert chart.state.chart_error == ""
    assert run(bus, "core", source, "plot_xy", {**samples, "y": [1, 3, 9]})["ok"]
    assert "refresh" in document.nodes[chart_id].state.chart_error
    assert document.nodes[chart_id].state.chart_data["values"] == [1, 2, 4]
    assert len([n for n in document.nodes.values() if n.kind == "chart"]) == 1
    receipt = envelope(document, source)["latest"]["provenance"]["receiptHash"]

    assert asyncio.run(bus.dispatch_intent("scene", "generateChart", [chart_id, "scatter"])) == chart_id
    assert document.nodes[chart_id].state.chart_data["values"] == [1, 3, 9]
    assert document.nodes[chart_id].state.chart_data["scienceReceipt"]["receiptHash"] == receipt
    assert document.nodes[chart_id].state.chart_error == ""
    assert document.undo()
    assert document.nodes[chart_id].state.chart_data["values"] == [1, 2, 4]
    assert document.nodes[chart_id].state.chart_error
    assert document.redo()
    assert document.nodes[chart_id].state.chart_error == ""

    # The regular chart serializer stores chartData and its parent. Both
    # survive reopening, with source ids reminted and stable nodeKey intact.
    saved = build_chat_data(document, plugin_registry=registry, settings_manager=settings)
    restore_chat_into_document(document, {"data": saved}, [], [],
                               plugin_registry=registry, settings_manager=settings)
    restored_source = next(n for n in document.nodes.values() if n.kind == "science_core.workbench")
    restored_chart = next(n for n in document.nodes.values() if n.kind == "chart")
    assert restored_chart.state.chart_source_node_id == restored_source.id
    assert restored_chart.state.chart_data["scienceReceipt"]["receiptHash"] == receipt
    assert any(e.source == restored_source.id and e.target == restored_chart.id for e in document.edges.values())


def test_chart_this_does_not_bypass_science_grants_or_accept_bad_source_shapes(session):
    from backend.api.intents_chart import register_chart_intents

    document, bus, _, _ = session

    class NoExtractionAgent:
        async def start_chart_generation(self, **kwargs):
            raise AssertionError("Do not invoke model generation for scientific results")

    register_chart_intents(bus, document, NotificationState(), NoExtractionAgent())
    grant(bus, "core")
    source = dispatch(bus, "executePlugin", "Scientific Core")
    assert run(bus, "core", source, "numeric_csv", {"csv": "A,B\n1,2\n3,4"})["ok"]
    chart_id = asyncio.run(bus.dispatch_intent("scene", "generateChart", [source, "heatmap"]))
    assert chart_id in document.nodes
    assert document.nodes[chart_id].state.chart_data["matrix"] == [[1, 2], [3, 4]]
    dispatch(bus, "setPluginGrant", "science_core", False)
    assert asyncio.run(bus.dispatch_intent("scene", "generateChart", [source, "boxplot"])) is None
    assert asyncio.run(bus.dispatch_intent("scene", "generateChart", [chart_id, "heatmap"])) is None
    assert len([node for node in document.nodes.values() if node.kind == "chart"]) == 1



def test_chart_receipt_freshness_detects_equal_numbers_from_different_sources(session):
    from backend.api.intents_chart import register_chart_intents

    document, bus, _, _ = session
    register_chart_intents(bus, document, NotificationState(), None)
    grant(bus, "core")
    source_a = dispatch(bus, "executePlugin", "Scientific Core")
    source_b = dispatch(bus, "executePlugin", "Scientific Core")
    child = dispatch(bus, "executePlugin", "Scientific Core", source_a)
    document.connect(source_b, child)
    for node_id in (source_a, source_b):
        assert run(bus, "core", node_id, "numeric_csv", {"csv": "Y\n1\n2\n3"})["ok"]

    def cite(node_id):
        snapshot = envelope(document, node_id)
        receipt = snapshot["latest"]["provenance"]
        return {"nodeId": node_id, "sourceKey": snapshot["nodeKey"],
                "resultHash": receipt["resultHash"], "receiptHash": receipt["receiptHash"],
                "selector": ["column", 0]}

    assert cite(source_a)["resultHash"] == cite(source_b)["resultHash"]
    assert cite(source_a)["receiptHash"] != cite(source_b)["receiptHash"]
    inputs = {"x": [0, 1, 2], "y": [1, 2, 3], "x_label": "Time (s)", "y_label": "Measurement"}
    assert run(bus, "core", child, "plot_xy", inputs, sources={"y": cite(source_a)})["ok"]
    chart_id = asyncio.run(bus.dispatch_intent("scene", "generateChart", [child, "scatter"]))
    before = document.nodes[chart_id].state.chart_data["scienceReceipt"]["receiptHash"]

    assert run(bus, "core", child, "plot_xy", inputs, sources={"y": cite(source_b)})["ok"]
    updated = envelope(document, child)["latest"]["provenance"]
    assert updated["resultHash"] == document.nodes[chart_id].state.chart_data["scienceReceipt"]["resultHash"]
    assert updated["receiptHash"] != before
    assert "refresh" in document.nodes[chart_id].state.chart_error
    assert document.nodes[chart_id].state.chart_data["values"] == [1, 2, 3]


def test_typed_table_import_link_drop_and_asset_roundtrip(session, tmp_path):
    """A table is separate from the unchanged legacy operation/result receipt."""
    import hashlib

    from backend.asset_store import AssetStore
    from graphlink_plugins.scientific.data_table import read_table

    document, bus, registry, settings = session
    grant(bus, "core")
    grant(bus, "statistics")
    source = dispatch(bus, "executePlugin", "Scientific Core")
    source_text = (
        "Sample,Temperature (degC),Flag,Observed,Group,Comment\n"
        "A,12.5,true,2026-11-01,low,first\n"
        "B,NA,false,2026-11-02,low,second\n"
        "C,16.5,true,2026-11-03,high,third\n"
    )
    imported = dispatch(bus, "invokePluginIntent", "science_core", "import_table", {
        "node_id": source, "text": source_text, "format": "csv",
        "missing_values": ["", "NA"], "filename": "observations.csv",
        "column_types": {"Group": "categorical"},
    })
    assert imported == {"ok": True}
    state = envelope(document, source)
    assert state["latest"] is None
    table = state["table"]
    assert table["rowCount"] == 3 and table["columnCount"] == 6
    assert len(table["preview"]) == 3
    assert [c["type"] for c in table["columns"]] == [
        "text", "numeric", "boolean", "datetime", "categorical", "text",
    ]
    assert table["columns"][1]["name"] == "Temperature"
    assert table["columns"][1]["unit"] == "degC"
    assert table["columns"][1]["missingCount"] == 1
    assert table["preview"][1][1] is None
    assert table["provenance"]["fileHash"] == hashlib.sha256(source_text.encode()).hexdigest()
    assert table["provenance"]["parseOptions"]["missingValues"] == ["", "NA"]
    assert table["provenance"]["parseOptions"]["delimiter"] == ","
    assert read_table(table)["rows"][0][3] == "2026-11-01"

    target = dispatch(bus, "executePlugin", "Statistics", source)
    selection = {"node_id": target, "source_node_id": source, "column": 1,
                 "expected_hash": table["asset_ref"]}
    rejected = dispatch(bus, "invokePluginIntent", "science_statistics", "table_column", selection)
    assert rejected["ok"] is False and "missing" in rejected["error"].lower()
    chosen = dispatch(bus, "invokePluginIntent", "science_statistics", "table_column",
                      {**selection, "policy": "drop"})
    assert chosen["ok"] and chosen["values"] == [12.5, 16.5]
    assert chosen["reference"]["selector"] == ["table_column", 1, "drop", 0, 3]
    before = envelope(document, target)["latest"]
    assert run(bus, "statistics", target, "describe",
               {"observations": chosen["values"]},
               sources={"observations": chosen["reference"]}) == {"ok": True}
    result = envelope(document, target)["latest"]
    assert result["values"]["mean"] == 14.5
    assert result["provenance"]["sources"]["observations"]["receiptHash"] == table["provenance"]["receiptHash"]
    assert before is None

    store = AssetStore(tmp_path / "assets")
    saved = build_chat_data(document, asset_store=store, plugin_registry=registry, settings_manager=settings)
    assert store.verify(table["asset_ref"])
    assert table["asset_ref"] in json.dumps(saved)
    assert source_text not in json.dumps(saved)
    restore_chat_into_document(document, {"data": saved}, [], [], asset_store=store,
                               plugin_registry=registry, settings_manager=settings)
    reloaded_source, reloaded_target = list(document.nodes)
    assert envelope(document, reloaded_source)["table"] == table
    assert envelope(document, reloaded_target)["latest"] == result
    assert dispatch(bus, "invokePluginIntent", "science_statistics", "table_column", {
        "node_id": reloaded_target, "source_node_id": reloaded_source,
        "column": 1, "policy": "drop",
    })["values"] == [12.5, 16.5]
    # Old source node ids can still be resolved through the durable nodeKey.
    assert run(bus, "statistics", reloaded_target, "describe",
               {"observations": chosen["values"]},
               sources={"observations": chosen["reference"]}) == {"ok": True}
    # Saving the same table again does not duplicate its content-addressed asset.
    assert store.verify(table["asset_ref"])


def test_typed_table_parser_limits_tsv_and_error_atomicity(session):
    import hashlib
    from graphlink_plugins.scientific.data_table import (
        TABLE_MAX_CELLS, TABLE_MAX_ROWS, numeric_column, parse_table, read_table,
    )

    body = b"Temperature (degC)\tOK\tWhen\n1.5\ttrue\t2026-10-02\n\tfalse\t2026-10-03\n"
    meta = parse_table(body, fmt="tsv", filename="test.tsv")
    assert meta["columns"][0] == {
        "name": "Temperature", "label": "Temperature (degC)", "unit": "degC",
        "type": "numeric", "missingCount": 1,
    }
    assert [col["type"] for col in meta["columns"]] == ["numeric", "boolean", "datetime"]
    assert read_table(meta)["rows"] == [[1.5, True, "2026-10-02"], [None, False, "2026-10-03"]]
    assert meta["provenance"]["parseOptions"]["format"] == "tsv"
    assert meta["provenance"]["fileHash"] == hashlib.sha256(body).hexdigest()
    with pytest.raises(ValueError, match="missing"):
        numeric_column(meta, 0)
    assert numeric_column(meta, 0, "drop") == [1.5]
    with pytest.raises(ValueError, match="Only numeric"):
        numeric_column(meta, 1)
    with pytest.raises(ValueError, match="wrong number|cells; expected"):
        parse_table(b"A,B\n1,2\n3\n")
    with pytest.raises(ValueError, match="UTF-8"):
        parse_table(b"Value\n\xff\n")
    with pytest.raises(ValueError, match="Invalid finite numeric"):
        parse_table(b"Count\n9007199254740993\n", hints={"Count": "numeric"})
    with pytest.raises(ValueError, match="Invalid finite numeric"):
        parse_table(b"Value\n1e-999\n", hints={"Value": "numeric"})
    with pytest.raises(ValueError, match="Row 2"):
        parse_table(b"Date\nnot-a-date\n", hints={"Date": "datetime"})

    # Exercise the advertised row AND cell limits, rather than the old 1024/4096.
    large = b"".join([b"a,b,c,d,e,f,g,h,i,j\n", b"1,2,3,4,5,6,7,8,9,10\n" * TABLE_MAX_ROWS])
    full = parse_table(large)
    assert full["rowCount"] == TABLE_MAX_ROWS and len(full["columns"]) * full["rowCount"] == TABLE_MAX_CELLS
    assert len(read_table(full)["rows"]) == TABLE_MAX_ROWS
    assert len(numeric_column(full, 0, "reject", 0, 1024)) == 1024
    with pytest.raises(ValueError, match="1024"):
        numeric_column(full, 0, "reject", 0, 1025)
    with pytest.raises(ValueError, match="100,000 rows"):
        parse_table(b"value\n" + b"1\n" * (TABLE_MAX_ROWS + 1))

    document, bus, _, _ = session
    grant(bus, "core")
    source = dispatch(bus, "executePlugin", "Scientific Core")
    assert dispatch(bus, "invokePluginIntent", "science_core", "import_table", {
        "node_id": source, "text": "Value\n1\n2\n",
    }) == {"ok": True}
    prior = envelope(document, source)["table"]
    fail = dispatch(bus, "invokePluginIntent", "science_core", "import_table", {
        "node_id": source, "text": "Value\n1\nbad\n",
        "column_types": {"Value": "numeric"},
    })
    assert fail["ok"] is False
    assert envelope(document, source)["table"] == prior


# Scientific integration audit: malformed saved state, byte/marker caps,
# graph-runner compatibility and asynchronous selection fencing.


def test_graph_runner_accepts_imported_table_node_state(session):
    from backend.api.intents_graph_runner import _state_data

    document, bus, _, _ = session
    grant(bus, "core")
    node_id = dispatch(bus, "executePlugin", "Scientific Core")
    assert dispatch(bus, "invokePluginIntent", "science_core", "import_table", {
        "node_id": node_id, "text": "Value\n1\n",
    }) == {"ok": True}
    data = _state_data(document.nodes[node_id])
    assert data["table"]["rowCount"] == 1


def test_linked_sources_reject_a_non_object_even_when_keys_match():
    from typing import cast

    from graphlink_plugins.scientific.workbench import _verify_sources

    malformed = cast(dict[str, object], ["observations"])
    with pytest.raises(ValueError, match="object keyed by input field"):
        _verify_sources(None, None, {"observations": [1.0]}, malformed, None)


def test_table_missing_markers_cannot_exceed_published_cap():
    from graphlink_plugins.scientific.data_table import parse_table

    with pytest.raises(ValueError, match="32"):
        parse_table(b"Value\n1\n", missing=[f"marker_{i}" for i in range(32)])


def test_oversized_pasted_source_is_rejected_before_encoding(monkeypatch):
    from graphlink_plugins.scientific import data_table

    class EncodingTrap(str):
        def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
            raise AssertionError("oversized input was encoded before its size was checked")

    monkeypatch.setattr(data_table, "TABLE_MAX_SOURCE_BYTES", 4)
    with pytest.raises(ValueError, match="64 MiB"):
        data_table.decode_input(text=EncodingTrap("12345"), encoded_file=None)


def test_table_metadata_rejects_malformed_preview_and_columns():
    from graphlink_plugins.scientific.data_table import parse_table, table_metadata_valid

    table = parse_table(b"Value\n1\n")
    assert table_metadata_valid(table)
    assert not table_metadata_valid({**table, "preview": [None]})
    assert not table_metadata_valid({**table, "previewColumns": 999})
    assert not table_metadata_valid({
        **table, "columns": [{**table["columns"][0], "name": None}],
    })


def test_table_column_returns_validation_error_for_corrupt_asset_rows():
    from graphlink_plugins.scientific.core import canonical_bytes, fingerprint
    from graphlink_plugins.scientific.data_table import (
        _STAGING, numeric_column, parse_table, table_metadata_valid,
    )

    table = parse_table(b"Value\n1\n")
    # A matching content hash is insufficient: the stored rows must obey the
    # typed-table schema before the selector indexes them.
    forged = canonical_bytes({
        "version": 1, "columns": table["columns"], "rows": [{"0": 1}],
    })
    ref = _STAGING.put(forged)
    receipt = {k: v for k, v in table["provenance"].items() if k != "receiptHash"}
    receipt["tableHash"] = ref
    receipt["receiptHash"] = fingerprint(receipt)
    damaged = {**table, "asset_ref": ref, "provenance": receipt}
    assert table_metadata_valid(damaged)
    with pytest.raises(ValueError, match="damaged"):
        numeric_column(damaged, 0)


@pytest.mark.parametrize("change", ["disconnect", "delete_target", "revoke_source", "revoke_target"])
def test_table_column_selection_rechecks_its_target_after_io(session, monkeypatch, change):
    import threading

    from graphlink_plugins.scientific import workbench

    document, bus, _, _ = session
    grant(bus, "core")
    grant(bus, "statistics")
    source = dispatch(bus, "executePlugin", "Scientific Core")
    assert dispatch(bus, "invokePluginIntent", "science_core", "import_table", {
        "node_id": source, "text": "Value\n1\n2\n",
    }) == {"ok": True}
    target = dispatch(bus, "executePlugin", "Statistics", source)
    started, finish = threading.Event(), threading.Event()

    def suspended_column(*_args: object) -> list[float]:
        started.set()
        assert finish.wait(timeout=10)
        return [1.0, 2.0]

    monkeypatch.setattr(workbench, "numeric_column", suspended_column)

    async def scenario() -> None:
        pending = asyncio.create_task(bus.dispatch_intent(
            "app-plugins", "invokePluginIntent",
            ["science_statistics", "table_column", {
                "node_id": target, "source_node_id": source, "column": 0,
            }],
        ))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            if change == "disconnect":
                document.remove_edges(list(document.edges))
            elif change == "delete_target":
                document.remove_nodes([target])
            else:
                pack = "science_core" if change == "revoke_source" else "science_statistics"
                await bus.dispatch_intent("app-plugins", "setPluginGrant", [pack, False])
        finally:
            finish.set()
        response = await pending
        assert response["ok"] is False
        if change.startswith("revoke"):
            assert "disabled" in response["error"]
        else:
            assert response["conflict"] is True

    asyncio.run(scenario())
