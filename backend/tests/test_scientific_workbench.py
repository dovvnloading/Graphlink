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
from graphlink_plugins.scientific.core import MAX_POINTS, VERSION, run_operation
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
        assert (node.x, node.y) == (110, 90)
        state = envelope(document, node_id)
        assert state["latest"] is None
        assert [o["id"] for o in state["operations"]] == [o.id for o in operations(pack)]
        for op in state["operations"]:
            assert run(bus, pack, node_id, op["id"], op["example"]) == {"ok": True}
            result = envelope(document, node_id)["latest"]
            assert result["operationId"] == op["id"] and result["provenance"]["packVersion"] == VERSION
            assert result["summary"] == node.content
            assert len(result["provenance"]["resultHash"]) == 64
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
    import threading
    from graphlink_plugins.scientific import workbench

    document, bus, _, _ = session
    grant(bus, "core")
    grant(bus, "statistics")
    source = dispatch(bus, "executePlugin", "Scientific Core")
    target = dispatch(bus, "executePlugin", "Statistics", source)
    run(bus, "core", source, "numeric_csv", {"csv": "x\n1\n2"})
    state = envelope(document, source)
    ref = {"nodeId": source, "sourceKey": state["nodeKey"], "selector": ["column", 0],
           "resultHash": state["latest"]["provenance"]["resultHash"]}
    entered, finish = threading.Event(), threading.Event()
    calculate = workbench.calculate_result

    def paused(*args):
        if args[2]["observations"] == [1, 2]:
            entered.set()
            assert finish.wait(4), "Calculation blocked the event loop."
        return calculate(*args)

    monkeypatch.setattr(workbench, "calculate_result", paused)
    async def scenario():
        async def submit(values, sources):
            return await bus.dispatch_intent("app-plugins", "invokePluginIntent", [
                "science_statistics", "run", {"node_id": target, "operation_id": "describe",
                "inputs": {"observations": values}, "sources": sources}])
        task = asyncio.create_task(submit([1, 2], {"observations": ref}))
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            if change in ("revoke", "revoke_source"):
                pack = "science_core" if change == "revoke_source" else "science_statistics"
                await bus.dispatch_intent("app-plugins", "setPluginGrant", [pack, False])
            elif change == "overwrite":
                assert (await submit([10, 20], {}))["ok"]
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
